"""Inferensi: muat model, hitung fitur dari sinyal mikrofon, kalibrasi ruangan, dan prediksi.

Urutan pra-pemrosesan sama dengan training: mono, 16 kHz, features.features_from_signal (47 fitur mentah),
lalu tambah_relatif terhadap baseline ruangan tenang (src/kalibrasi.py), lalu kolom disusun sesuai FEATURE_COLS.
Pipeline model yang disimpan memilih sendiri kolom kombinasinya, jadi urutan fitur selalu sama dengan training.
"""
from math import gcd

import joblib
import numpy as np
import pandas as pd
from scipy.signal import resample_poly

from config import MODEL_PATH, FEATURE_COLS, SR, FITUR_VERSI, KALIBRASI_MAKS_RMS
from features import features_from_signal
from kalibrasi import referensi, baseline_dari_klip, tambah_relatif

AMBANG = 0.5          # peluang berisik >= AMBANG berarti BERISIK (sama dengan evaluasi training)
MIN_DETIK = 3.0


class ModelBelumSiap(RuntimeError):
    pass


def load_bundle():
    if not MODEL_PATH.exists():
        raise ModelBelumSiap("Model belum ada. Jalankan dulu: python src/train.py")
    b = joblib.load(MODEL_PATH)
    if b.get("fitur_versi") != FITUR_VERSI or list(b.get("features", [])) != FEATURE_COLS:
        raise ModelBelumSiap("Model dibuat dengan versi fitur lain. Jalankan ulang: python src/train.py")
    return b


def ke_16k(y, sr):
    """Sinyal mono float32 pada 16 kHz (resample polifase bila sample rate berbeda)."""
    y = np.asarray(y, dtype=np.float32).flatten()
    sr = int(round(sr))
    if sr != SR:
        g = gcd(sr, SR)
        y = resample_poly(y, SR // g, sr // g).astype(np.float32)
    return y


def fitur_dari_sinyal(y, sr):
    y16 = ke_16k(y, sr)
    if len(y16) < MIN_DETIK * SR:
        raise ValueError(f"Rekaman terlalu pendek (minimal {MIN_DETIK:.0f} detik).")
    if float(np.abs(y16).max()) < 1e-4:
        raise ValueError("Sinyal mikrofon nyaris nol. Mikrofon mati, salah perangkat, atau izin belum diberikan.")
    return features_from_signal(y16, SR), y16


def kalibrasi_klip(y, sr):
    """Satu klip ruangan tenang menjadi nilai referensi baseline, plus peringatan bila ruangan tampak tidak tenang."""
    fitur, _ = fitur_dari_sinyal(y, sr)
    ref = referensi(fitur)
    peringatan = None
    if ref["rms_mean"] > KALIBRASI_MAKS_RMS:
        peringatan = ("Ruangan terdengar cukup keras saat kalibrasi. Kalibrasi sebaiknya dilakukan saat ruangan tenang, "
                      "karena kebisingan dinilai relatif terhadap kondisi ini.")
    return {"referensi": ref, "rms": ref["rms_mean"], "peringatan": peringatan}


def prediksi(y, sr, daftar_referensi, bundle):
    """Prediksi BERISIK atau TIDAK BERISIK untuk satu rekaman (10 detik) dengan baseline hasil kalibrasi."""
    fitur, _ = fitur_dari_sinyal(y, sr)
    if daftar_referensi:
        baseline = baseline_dari_klip(daftar_referensi)
    elif bundle.get("pakai_kalibrasi", True):
        raise ValueError("Kalibrasi ruangan diperlukan sebelum prediksi.")
    else:
        baseline = referensi(fitur)
    baris = tambah_relatif(fitur, baseline)
    X = pd.DataFrame([baris])[FEATURE_COLS]
    model = bundle["model"]
    p = float(model.predict_proba(X)[0][list(model.classes_).index("berisik")])
    return {"label": "berisik" if p >= AMBANG else "tidak_berisik", "peluang_berisik": p,
            "rms": float(fitur["rms_mean"]), "rel_rms_db": float(baris["rel_rms_db"])}
