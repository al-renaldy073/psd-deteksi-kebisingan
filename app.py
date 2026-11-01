"""Aplikasi web deteksi kebisingan (Flask). Jalankan:  python app.py  lalu buka http://127.0.0.1:5000

Mikrofon direkam di browser (tanpa echo cancellation, noise suppression, dan auto gain agar level asli terjaga),
dikirim ke server per 10 detik, diproses model, dan peringatan suara diputar di browser bila berisik.

Akses dari HP atau laptop lain memerlukan HTTPS (browser memblokir mikrofon di alamat http:// selain localhost).
Untuk itu letakkan aplikasi ini di belakang reverse proxy HTTPS (lihat Caddyfile dan docker-compose.https.yml);
ProxyFix di bawah membuat Flask mengenali skema/host asli dari header X-Forwarded-*.
"""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory
from scipy.io import wavfile
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from config import (RAW_DIR, REPORT_DIR, SR, CHUNK_DETIK, RMS_HENING, N_MFCC, PAKAI_CHUNKING, KALIBRASI_MAKS_RMS,
                    FEATURE_MENTAH, FEATURE_RELATIF, FEATURE_COLS)
import inference as inf

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
# Di belakang reverse proxy HTTPS (Caddy/Nginx) skema dan host asli dibaca dari X-Forwarded-*. Matikan dengan TRUST_PROXY=0.
if os.environ.get("TRUST_PROXY", "1") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

FOLDER = {"berisik": "berisik", "tidak_berisik": "non-berisik"}
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
    return render_template("index.html")


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
def kalibrasi():
    try:
        pcm, sr = baca_audio()
        return jsonify(inf.kalibrasi_klip(pcm, sr))
    except Exception as e:
        return galat(e)


@app.post("/api/prediksi")
def prediksi():
    try:
        pcm, sr = baca_audio()
        ref = json.loads(request.form.get("baseline", "[]"))
        return jsonify(inf.prediksi(pcm, sr, ref, bundle()))
    except inf.ModelBelumSiap as e:
        return galat(e, 503)
    except Exception as e:
        return galat(e)


@app.post("/api/simpan")
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


@app.get("/api/peringatan")
def peringatan():
    for nama in ("jangan_rame.wav", "jangan_rame.mp3"):
        if (ROOT / "assets" / nama).exists():
            return send_file(ROOT / "assets" / nama, conditional=True)
    return galat("File assets/jangan_rame.wav atau .mp3 tidak ditemukan.", 404)


@app.get("/reports/<path:nama>")
def laporan(nama):
    return send_from_directory(REPORT_DIR, nama)


if __name__ == "__main__":
    # Dengarkan semua interface agar perangkat lain di jaringan dapat menjangkau server.
    # Akses mikrofon dari browser melalui IP/domain tetap memerlukan HTTPS dengan sertifikat tepercaya.
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
