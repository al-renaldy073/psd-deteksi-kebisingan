"""Fitur relatif terhadap ruangan tenang (kalibrasi mikrofon).

Masalah: kerasnya rekaman mentah bergantung pada gain mikrofon, jarak, dan perangkat, sehingga ambang "berisik" dari
satu perekam tidak berlaku untuk mikrofon lain. Solusi: setiap perekam memberi baseline (rekaman ruangan tenang), lalu
fitur level dinyatakan sebagai selisih dB terhadap baseline itu. Fitur yang tidak bergantung gain (ZCR, centroid,
roll-off, bandwidth, flatness, MFCC 2 sampai 13) dipakai apa adanya.

Saat training baseline sebuah file = median file TIDAK BERISIK LAIN dari perekam yang sama (file itu sendiri tidak
dihitung). Saat dipakai, baseline = median 1 sampai 3 klip ruangan tenang dari tahap kalibrasi.
"""
import numpy as np
import pandas as pd

from config import REF_KEYS, REF_LEVEL, FEATURE_COLS

EPS = 1e-8


def referensi(fitur):
    """Ambil nilai fitur yang dibutuhkan sebagai baseline dari satu klip tenang."""
    return {k: float(fitur[k]) for k in REF_KEYS}


def baseline_dari_klip(daftar_referensi):
    """Median beberapa klip tenang menjadi satu baseline."""
    return {k: float(np.median([r[k] for r in daftar_referensi])) for k in REF_KEYS}


def tambah_relatif(fitur, baseline):
    """Tambahkan 12 fitur relatif ke dictionary fitur mentah."""
    out = dict(fitur)
    out["rel_rms_db"] = float(20 * np.log10((fitur["rms_mean"] + EPS) / (baseline["rms_mean"] + EPS)))
    out["rms_variasi"] = float(fitur["rms_std"] / (fitur["rms_mean"] + EPS))
    for k in REF_LEVEL:
        out["rel_" + k] = float(fitur[k] - baseline[k])
    return out


def bangun_relatif(df):
    """Untuk data latih: hitung fitur relatif tiap file dengan baseline leave-one-out per perekam.
    File tanpa klip tenang lain dari perekam yang sama tidak punya baseline dan dikeluarkan.
    Mengembalikan (df dengan kolom FEATURE_COLS, jumlah file yang dikeluarkan)."""
    df = df.reset_index(drop=True)
    baris, simpan = [], []
    for i, r in df.iterrows():
        tenang = df[(df["speaker_id"] == r["speaker_id"]) & (df["label"] == "tidak_berisik") & (df.index != i)]
        if tenang.empty:
            continue
        baseline = baseline_dari_klip([referensi(t) for _, t in tenang.iterrows()])
        baris.append(tambah_relatif(r.to_dict(), baseline))
        simpan.append(i)
    hasil = pd.DataFrame(baris)
    meta = df.loc[simpan, ["filename", "speaker_id", "label"]].reset_index(drop=True)
    return pd.concat([meta, hasil[FEATURE_COLS]], axis=1), len(df) - len(simpan)
