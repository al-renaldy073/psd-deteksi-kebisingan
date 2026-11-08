"""Aplikasi web deteksi kebisingan (Flask). Jalankan:  python app.py  lalu buka http://127.0.0.1:5000

Mikrofon direkam di browser (tanpa echo cancellation, noise suppression, dan auto gain agar level asli terjaga),
dikirim ke server per 10 detik, diproses model, dan peringatan suara diputar di browser bila berisik.

Akses dari HP atau laptop lain memerlukan HTTPS (browser memblokir mikrofon di alamat http:// selain localhost).
Untuk itu letakkan aplikasi ini di belakang reverse proxy HTTPS (lihat Caddyfile dan docker-compose.https.yml);
ProxyFix di bawah membuat Flask mengenali skema/host asli dari header X-Forwarded-*.
"""
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import deque
from functools import wraps
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, send_file, send_from_directory, session
from scipy.io import wavfile
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from config import (RAW_DIR, REPORT_DIR, SR, CHUNK_DETIK, JEDA_DETIK, RMS_HENING, N_MFCC, PAKAI_CHUNKING, KALIBRASI_MAKS_RMS,
                    FEATURE_MENTAH, FEATURE_RELATIF, FEATURE_COLS)
import inference as inf

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
# Di belakang reverse proxy HTTPS (Caddy/Nginx) skema dan host asli dibaca dari X-Forwarded-*. Matikan dengan TRUST_PROXY=0.
if os.environ.get("TRUST_PROXY", "1") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

FOLDER = {"berisik": "berisik", "tidak_berisik": "non-berisik"}

# ---------------------------------------------------------------------------------------------------------------
# Peran pengguna: ADMIN (mulai/berhenti streaming, kalibrasi, kumpulkan data, evaluasi) dan FRONT (hanya melihat
# gelombang audio dan mendengar peringatan). Diatur lewat variabel lingkungan; lihat docker-compose.yml.
# ---------------------------------------------------------------------------------------------------------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin6")
ARDUINO_TOKEN = os.environ.get("ARDUINO_TOKEN", "")
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=60 * 60 * 12)
FASE_SAH = {"siaga", "kalibrasi", "rekam", "proses", "jeda"}
KAL_ARDUINO = ROOT / "data" / "kalibrasi_arduino.json"


def peran():
    return session.get("peran")


def butuh_admin(fn):
    @wraps(fn)
    def dalam(*a, **k):
        if peran() != "admin":
            return galat("Hanya admin yang dapat melakukan ini." if peran() else "Silakan masuk dulu.", 403 if peran() else 401)
        return fn(*a, **k)
    return dalam


def butuh_masuk(fn):
    @wraps(fn)
    def dalam(*a, **k):
        if not peran():
            return galat("Silakan masuk dulu.", 401)
        return fn(*a, **k)
    return dalam


def butuh_admin_atau_stream(fn):
    """Admin (sesi login) ATAU halaman admin yang sedang merekam (token siklus streaming).

    Token dibuat saat admin menekan Mulai dan dikirim di header X-Stream-Token. Dengan begitu streaming tetap berjalan
    walau sesi login di browser berganti (keluar, atau masuk sebagai front di tab lain), selama halaman admin yang
    memegang mikrofon tetap terbuka. Token tidak berlaku setelah streaming dihentikan atau dimulai ulang."""
    @wraps(fn)
    def dalam(*a, **k):
        if peran() == "admin":
            return fn(*a, **k)
        token = request.headers.get("X-Stream-Token", "")
        if token:
            if STREAM.token_sah(token):
                return fn(*a, **k)
            return galat("Streaming sudah dihentikan atau diambil alih sesi lain.", 409)
        return galat("Hanya admin yang dapat melakukan ini." if peran() else "Silakan masuk dulu.", 403 if peran() else 401)
    return dalam


class StatusStream:
    """Status streaming yang dibagi ke semua pengguna: diisi admin (atau perangkat Arduino), dibaca halaman front.
    Disimpan di memori server (satu proses), sehingga cukup untuk satu instance aplikasi."""

    def __init__(self):
        self.kunci = threading.Lock()
        self.jalan, self.sumber, self.fase = False, "browser", "siaga"
        self.fase_sejak = time.time()
        self.seq = 0
        self.gel = deque(maxlen=800)          # potongan gelombang terbaru: {s, v:[puncak 0..1], db}
        self.hasil, self.hasil_id = None, 0
        self.pembaruan = 0.0                  # kapan terakhir ada data dari admin/Arduino
        self.arduino_kal_diminta, self.arduino_terlihat = 0, 0.0
        self.token = None                     # token siklus streaming browser (lihat butuh_admin_atau_stream)
        self.suara_front = True               # admin yang menentukan apakah halaman front memutar suara peringatan

    def token_sah(self, t):
        with self.kunci:
            return bool(self.jalan and self.token and t and hmac.compare_digest(t.encode(), self.token.encode()))

    def kontrol(self, aksi, fase=None, sumber="browser"):
        with self.kunci:
            if aksi == "mulai":
                if self.jalan and self.sumber != sumber:
                    raise RuntimeError(f"Sumber '{self.sumber}' sedang aktif; hentikan dulu.")
                self.jalan, self.sumber, self.fase, self.fase_sejak = True, sumber, "siaga", time.time()
                self.token = secrets.token_urlsafe(24) if sumber == "browser" else None
                self.gel.clear()
            elif aksi == "berhenti":
                self.jalan, self.fase, self.fase_sejak, self.token = False, "siaga", time.time(), None
            elif aksi == "fase" and fase in FASE_SAH and self.jalan:
                if fase != self.fase:
                    self.fase, self.fase_sejak = fase, time.time()
            self.pembaruan = time.time()

    def gelombang(self, v, db):
        with self.kunci:
            self.seq += 1
            self.gel.append({"s": self.seq, "v": v, "db": db})
            self.pembaruan = time.time()

    def hasil_baru(self, h):
        with self.kunci:
            self.hasil_id += 1
            self.hasil = {"id": self.hasil_id, "label": h["label"], "peluang": h["peluang_berisik"], "rel_db": h["rel_rms_db"],
                          "rms": h["rms"], "waktu": time.strftime("%H:%M:%S")}
            self.pembaruan = time.time()

    def foto(self, sejak):
        with self.kunci:
            return {"jalan": self.jalan, "sumber": self.sumber, "fase": self.fase, "fase_detik": round(time.time() - self.fase_sejak, 1),
                    "seq": self.seq, "gel": [g for g in self.gel if g["s"] > sejak][-400:], "hasil": self.hasil,
                    "umur_data": round(time.time() - self.pembaruan, 1) if self.pembaruan else None,
                    "suara_front": self.suara_front,
                    "arduino": {"terlihat_detik": round(time.time() - self.arduino_terlihat, 1) if self.arduino_terlihat else None,
                                "kalibrasi_diminta": self.arduino_kal_diminta}}


STREAM = StatusStream()
_cache = {"mtime": None, "bundle": None}


def bundle():
    """Muat model; dimuat ulang otomatis bila file model diperbarui oleh train.py."""
    mtime = inf.MODEL_PATH.stat().st_mtime if inf.MODEL_PATH.exists() else None
    if _cache["bundle"] is None or mtime != _cache["mtime"]:
        _cache["bundle"], _cache["mtime"] = inf.load_bundle(), mtime
    return _cache["bundle"]


def baca_audio():
    f = request.files.get("audio")
    if f is None:
        raise ValueError("Audio tidak diterima.")
    pcm = np.frombuffer(f.read(), dtype="<f4")
    sr = float(request.form.get("sr", 0))
    if not 8000 <= sr <= 96000 or len(pcm) < sr:
        raise ValueError("Audio atau sample rate tidak valid.")
    return pcm, sr


def galat(e, kode=400):
    if isinstance(e, HTTPException):          # mis. 413 saat berkas melebihi MAX_CONTENT_LENGTH
        kode = e.code or kode
        pesan = "Berkas terlalu besar (maksimal 40 MB)." if kode == 413 else e.description
        return jsonify({"galat": pesan}), kode
    return jsonify({"galat": str(e)}), kode


@app.context_processor
def versi_aset():
    """Tambahkan versi (waktu ubah file) ke URL static agar browser HP/laptop tidak memakai cache lama."""
    def v(nama):
        try:
            return int((ROOT / "static" / nama).stat().st_mtime)
        except OSError:
            return 0
    return {"aset_v": v}


@app.after_request
def header_umum(resp):
    # Mengizinkan mikrofon hanya untuk halaman ini sendiri (relevan bila nanti ditanam/di-proxy).
    resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/") and request.path != "/api/peringatan":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(HTTPException)
def galat_http(e):
    if request.path.startswith("/api/"):
        return galat(e)
    return e


@app.get("/")
def beranda():
    if peran() == "admin":
        return render_template("index.html", jeda=JEDA_DETIK)
    return redirect("/front" if peran() == "front" else "/login")


@app.get("/front")
def front():
    if not peran():
        return redirect("/login")
    # ?embed=1: dipakai tab "Tampilan front" di dashboard admin (iframe) agar admin tidak perlu meninggalkan halamannya.
    return render_template("front.html", peran=peran(), embed=request.args.get("embed") == "1")


@app.route("/login", methods=["GET", "POST"])
def login():
    pesan = None
    if request.method == "POST":
        pilihan = request.form.get("peran", "")
        if pilihan == "admin":
            if hmac.compare_digest(request.form.get("password", "").encode(), ADMIN_PASSWORD.encode()):
                session.clear(); session["peran"] = "admin"; session.permanent = True
                return redirect("/")
            pesan = "Password admin salah."
        elif pilihan == "front":
            if peran() == "admin":
                # Cookie sesi dipakai bersama semua tab di browser yang sama. Menimpanya dengan peran front akan
                # memutus dashboard admin yang sedang streaming (permintaannya ditolak), jadi sesi admin dipertahankan.
                return redirect("/front")
            session.clear(); session["peran"] = "front"; session.permanent = True
            return redirect("/front")
    return render_template("login.html", pesan=pesan, peran=peran()), (401 if pesan else 200)


@app.get("/keluar")
def keluar():
    session.clear()
    return redirect("/login")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "model_ada": inf.MODEL_PATH.exists()})


def _kelompok_pembersihan(d):
    """Rapikan label pembersihan dari hasil_evaluasi.json (file JSON tidak diubah) menjadi daftar alasan yang singkat."""
    hasil = []
    for alasan, jumlah in (d or {}).items():
        if alasan.startswith("kode diabaikan"):
            teks = "Kode perekam 24-168 dikeluarkan (label diduga terbalik)"
        elif "kontradiksi" in alasan:
            teks = "Audio identik dengan label berbeda (kontradiksi)"
        elif alasan.startswith("salinan identik"):
            teks = "Salinan identik berlabel sama (disisakan satu)"
        else:
            teks = alasan
        hasil.append({"alasan": teks, "jumlah": jumlah})
    return hasil


@app.get("/api/info")
@butuh_admin
def info():
    try:
        b = bundle()
    except inf.ModelBelumSiap as e:
        return galat(e, 503)
    f_hasil = REPORT_DIR / "hasil_evaluasi.json"
    hasil = json.loads(f_hasil.read_text(encoding="utf-8")) if f_hasil.exists() else {}
    tabel = []
    f = REPORT_DIR / "perbandingan_model.csv"
    if f.exists():
        tabel = json.loads(pd.read_csv(f).drop(columns=["Parameter"]).to_json(orient="records"))
    data = {k: len(list((RAW_DIR / v).glob("*"))) if (RAW_DIR / v).exists() else 0 for k, v in FOLDER.items()}
    hasil["pembersihan_ringkas"] = _kelompok_pembersihan(hasil.get("pembersihan"))
    hasil["confusion_matrix"] = hasil.get("confusion_matrix(berisik,tidak_berisik)")
    konfigurasi = {"sample_rate": SR, "durasi_rekaman": CHUNK_DETIK, "rms_hening": RMS_HENING, "n_mfcc": N_MFCC,
                   "pakai_chunking": PAKAI_CHUNKING, "n_fitur_mentah": len(FEATURE_MENTAH),
                   "n_fitur_relatif": len(FEATURE_RELATIF), "n_kolom": len(FEATURE_COLS),
                   "ambang": inf.AMBANG, "kalibrasi_maks_rms": KALIBRASI_MAKS_RMS}
    return jsonify({"model": b["model_name"], "algoritma": b["algoritma"], "kombinasi": b["kombinasi_fitur"],
                    "fitur_terpilih": b["fitur_terpilih"], "parameter": b.get("parameter"),
                    "n_fitur_kombinasi": len(b.get("fitur_kombinasi", [])), "pakai_kalibrasi": b["pakai_kalibrasi"],
                    "hasil": hasil, "tabel": tabel, "jumlah_data": data, "konfigurasi": konfigurasi})


@app.post("/api/kalibrasi")
@butuh_admin_atau_stream
def kalibrasi():
    try:
        pcm, sr = baca_audio()
        return jsonify(inf.kalibrasi_klip(pcm, sr))
    except Exception as e:
        return galat(e)


@app.post("/api/prediksi")
@butuh_admin_atau_stream
def prediksi():
    if peran() != "admin" and request.form.get("stream") != "1":
        return galat("Hanya admin yang dapat melakukan ini.", 403)       # token streaming hanya untuk siklus streaming
    if request.form.get("stream") == "1":
        # Siklus streaming hanya diterima selagi streaming berjalan dan (bila token dikirim) tokennya masih berlaku.
        # Tab lama yang streamingnya sudah dihentikan atau diambil alih berhenti sendiri walau cookie admin masih sah.
        tok = request.headers.get("X-Stream-Token", "")
        if not STREAM.jalan or (tok and not STREAM.token_sah(tok)):
            return galat("Streaming sudah dihentikan atau diambil alih sesi lain.", 409)
    try:
        pcm, sr = baca_audio()
        ref = json.loads(request.form.get("baseline", "[]"))
        h = inf.prediksi(pcm, sr, ref, bundle())
        if request.form.get("stream") == "1":      # hanya siklus streaming yang dibagikan ke halaman front
            STREAM.hasil_baru(h)
        return jsonify(h)
    except inf.ModelBelumSiap as e:
        return galat(e, 503)
    except Exception as e:
        return galat(e)


@app.post("/api/simpan")
@butuh_admin
def simpan():
    """Simpan klip berlabel dari mikrofon ke data/raw agar bisa dipakai melatih ulang (python src/extract_dataset.py)."""
    try:
        pcm, sr = baca_audio()
        label, sesi = request.form.get("label", ""), request.form.get("sesi", "")
        if label not in FOLDER or not re.fullmatch(r"[A-Za-z0-9-]{2,20}", sesi):
            raise ValueError("Label atau nama sesi tidak valid (huruf, angka, strip; tanpa spasi atau garis bawah).")
        y = inf.ke_16k(pcm, sr)
        if float(np.abs(y).max()) < 1e-3:
            raise ValueError("Sinyal mikrofon nyaris nol, klip tidak disimpan.")
        folder = RAW_DIR / FOLDER[label]
        folder.mkdir(parents=True, exist_ok=True)
        pola = re.compile(rf"^{re.escape(sesi)} {FOLDER[label]} (\d+)\.wav$")
        no = max([int(m.group(1)) for p in folder.glob("*.wav") if (m := pola.match(p.name))], default=0) + 1
        nama = f"{sesi} {FOLDER[label]} {no}.wav"
        wavfile.write(folder / nama, SR, (np.clip(y, -1, 1) * 32767).astype(np.int16))
        return jsonify({"file": nama, "rms": float(np.sqrt(np.mean(y ** 2))),
                        "jumlah": {k: len(list((RAW_DIR / v).glob("*"))) for k, v in FOLDER.items()}})
    except Exception as e:
        return galat(e)


@app.post("/api/kontrol")
@butuh_admin_atau_stream
def kontrol():
    """Admin memulai/menghentikan streaming dan melaporkan fase siklus. Front tidak punya akses ke endpoint ini."""
    d = request.get_json(silent=True) or {}
    aksi, sumber = d.get("aksi"), d.get("sumber", "browser")
    if aksi not in ("mulai", "berhenti", "fase", "kalibrasi_arduino") or sumber not in ("browser", "arduino"):
        return galat("Aksi tidak dikenal.")
    if peran() != "admin" and aksi not in ("fase", "berhenti"):
        return galat("Hanya admin yang dapat memulai streaming.", 403)       # token hanya untuk fase/berhenti
    tok = request.headers.get("X-Stream-Token", "")
    if tok and aksi in ("fase", "berhenti") and not STREAM.token_sah(tok):
        # tab lama (token kedaluwarsa) tidak boleh mengubah atau menghentikan streaming milik sesi yang lebih baru
        return galat("Streaming sudah dihentikan atau diambil alih sesi lain.", 409)
    try:
        if aksi == "kalibrasi_arduino":
            if not ARDUINO_TOKEN:
                return galat("Arduino belum diaktifkan: atur ARDUINO_TOKEN pada docker-compose.yml.", 503)
            STREAM.arduino_kal_diminta = 2
        else:
            STREAM.kontrol(aksi, d.get("fase"), sumber)
    except RuntimeError as e:
        return galat(e, 409)
    data = STREAM.foto(10 ** 12)
    if aksi == "mulai" and sumber == "browser":
        data["token"] = STREAM.token          # dikembalikan hanya ke admin yang menekan Mulai
    return jsonify(data)


@app.post("/api/gelombang")
@butuh_admin_atau_stream
def gelombang():
    """Potongan gelombang dari mikrofon admin (puncak per ~85 ms) agar front dapat menampilkannya hampir real time."""
    d = request.get_json(silent=True) or {}
    v = [min(1.0, max(0.0, float(x))) for x in (d.get("v") or [])][:8]
    if not v or not STREAM.jalan:
        return jsonify({"ok": False})
    STREAM.gelombang([round(x, 4) for x in v], round(float(d.get("db", -90)), 1))
    return jsonify({"ok": True})


@app.post("/api/suara-front")
@butuh_admin
def suara_front():
    """Admin mengaktifkan/mematikan suara peringatan di halaman front. Front tidak punya kontrol suara sendiri."""
    d = request.get_json(silent=True) or {}
    with STREAM.kunci:
        STREAM.suara_front = bool(d.get("aktif"))
        nilai = STREAM.suara_front
    return jsonify({"ok": True, "suara_front": nilai})


@app.get("/api/status")
@butuh_masuk
def status():
    """Dibaca halaman front (dan admin) beberapa kali per detik: fase, potongan gelombang baru, dan hasil terakhir."""
    try:
        sejak = int(request.args.get("sejak", 0))
    except ValueError:
        sejak = 0
    foto = STREAM.foto(sejak)
    foto["durasi"], foto["jeda"] = CHUNK_DETIK, JEDA_DETIK
    foto["peran"] = peran()
    return jsonify(foto)


# ---------------------------------------------------------------------------------------------------------------
# Perangkat Arduino (ESP32 + mikrofon I2S) sebagai sumber audio alternatif. Autentikasi dengan header X-Device-Token.
# Perangkat mengirim PCM int16 mono 16 kHz mentah; baseline kalibrasinya disimpan di server (data/kalibrasi_arduino.json).
# ---------------------------------------------------------------------------------------------------------------
def cek_perangkat():
    if not ARDUINO_TOKEN:
        raise PermissionError("Arduino dinonaktifkan (ARDUINO_TOKEN belum diatur).")
    if not hmac.compare_digest(request.headers.get("X-Device-Token", "").encode(), ARDUINO_TOKEN.encode()):
        raise PermissionError("Token perangkat salah.")
    STREAM.arduino_terlihat = time.time()


def pcm_perangkat():
    sr = float(request.headers.get("X-Sample-Rate", 16000))
    mentah = request.get_data()
    pcm = np.frombuffer(mentah[: len(mentah) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    if not 8000 <= sr <= 48000 or len(pcm) < 3 * sr:
        raise ValueError("Audio perangkat terlalu pendek atau sample rate tidak valid (minimal 3 detik).")
    return pcm, sr


def baca_kal_arduino():
    try:
        return json.loads(KAL_ARDUINO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


@app.get("/api/arduino/perintah")
def arduino_perintah():
    """Perangkat bertanya tiap beberapa detik: apakah admin menyuruh merekam atau mengkalibrasi?"""
    try:
        cek_perangkat()
    except PermissionError as e:
        return galat(e, 403)
    return jsonify({"rekam": bool(STREAM.jalan and STREAM.sumber == "arduino"), "kalibrasi": STREAM.arduino_kal_diminta,
                    "durasi": CHUNK_DETIK, "jeda": JEDA_DETIK, "sample_rate": SR})


@app.post("/api/arduino/kalibrasi")
def arduino_kalibrasi():
    try:
        cek_perangkat()
        if STREAM.arduino_kal_diminta <= 0:
            return galat("Admin belum meminta kalibrasi Arduino.", 409)
        pcm, sr = pcm_perangkat()
        ref = inf.kalibrasi_klip(pcm, sr)
        daftar = ([] if STREAM.arduino_kal_diminta == 2 else baca_kal_arduino())[-2:] + [ref["referensi"]]
        KAL_ARDUINO.parent.mkdir(parents=True, exist_ok=True)
        KAL_ARDUINO.write_text(json.dumps(daftar), encoding="utf-8")
        STREAM.arduino_kal_diminta -= 1
        return jsonify({"rms": ref["rms"], "peringatan": ref["peringatan"], "tersisa": STREAM.arduino_kal_diminta})
    except PermissionError as e:
        return galat(e, 403)
    except Exception as e:
        return galat(e)


@app.post("/api/arduino/prediksi")
def arduino_prediksi():
    try:
        cek_perangkat()
        if not (STREAM.jalan and STREAM.sumber == "arduino"):
            return galat("Streaming Arduino belum dimulai admin.", 409)
        ref = baca_kal_arduino()
        if not ref:
            return galat("Belum ada kalibrasi Arduino. Minta admin menekan Kalibrasi Arduino.", 409)
        pcm, sr = pcm_perangkat()
        STREAM.kontrol("fase", "proses")
        h = inf.prediksi(pcm, sr, ref, bundle())
        puncak = np.abs(pcm[: len(pcm) // 4096 * 4096]).reshape(-1, 4096).max(axis=1)      # gelombang ringkas untuk front
        for i in range(0, len(puncak), 3):
            STREAM.gelombang([float(x) for x in puncak[i:i + 3]], round(float(20 * np.log10(np.sqrt(np.mean(pcm ** 2)) + 1e-8)), 1))
        STREAM.hasil_baru(h)
        STREAM.kontrol("fase", "jeda")
        return jsonify(h)
    except PermissionError as e:
        return galat(e, 403)
    except inf.ModelBelumSiap as e:
        return galat(e, 503)
    except Exception as e:
        return galat(e)


@app.get("/api/peringatan")
@butuh_masuk
def peringatan():
    for nama in ("jangan_rame.wav", "jangan_rame.mp3"):
        if (ROOT / "assets" / nama).exists():
            return send_file(ROOT / "assets" / nama, conditional=True)
    return galat("File assets/jangan_rame.wav atau .mp3 tidak ditemukan.", 404)


@app.get("/reports/<path:nama>")
@butuh_admin
def laporan(nama):
    return send_from_directory(REPORT_DIR, nama)


if __name__ == "__main__":
    # Dengarkan semua interface agar perangkat lain di jaringan dapat menjangkau server.
    # Akses mikrofon dari browser melalui IP/domain tetap memerlukan HTTPS dengan sertifikat tepercaya.
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
