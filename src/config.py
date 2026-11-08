"""Konfigurasi pusat proyek deteksi kebisingan."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CSV_PATH = PROCESSED_DIR / "fitur_audio.csv"
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "model_kebisingan.joblib"
REPORT_DIR = ROOT / "reports"

SR = 16000
CHUNK_DETIK = 10                 # durasi rekaman streaming mikrofon (dan ukuran potongan bila PAKAI_CHUNKING=True)
JEDA_DETIK = 2                   # jeda antar siklus streaming (setelah hasil/peringatan, sebelum rekaman berikutnya)
MIN_CHUNK_DETIK = 5              # potongan sisa yang lebih pendek dari ini dibuang (hanya bila PAKAI_CHUNKING=True)
RMS_HENING = 0.001
N_MFCC = 13

# True  = audio dipotong per CHUNK_DETIK, 1 potongan = 1 baris data (jumlah data lebih banyak).
# False = audio dipakai UTUH, 1 file = 1 baris data (200 baris).
PAKAI_CHUNKING = False

FITUR_VERSI = 5                  # naikkan jika definisi fitur berubah, agar model lama tidak dipakai diam-diam

BASE_FEATURES = ["rms", "centroid", "zcr"] + [f"mfcc_{i}" for i in range(1, N_MFCC + 1)]
FEATURE_DASAR = [f"{name}_{stat}" for name in BASE_FEATURES for stat in ("mean", "std")]
# Fitur tambahan (numpy, features._fitur_tambahan): persentil level suara (dB), roll-off, bandwidth, flatness, energi 6 pita (dB).
BAND_HZ = [(0, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000)]
FEATURE_TAMBAHAN = (["level_db_p10", "level_db_p50", "level_db_p90"]
                    + [f"{n}_{s}" for n in ("rolloff", "bandwidth", "flatness") for s in ("mean", "std")]
                    + [f"banda_{i}_db" for i in range(1, len(BAND_HZ) + 1)])
FEATURE_MENTAH = list(FEATURE_DASAR) + FEATURE_TAMBAHAN      # 47 fitur keluaran ekstraksi (isi fitur_audio.csv)

# Kalibrasi ruangan: fitur yang bergantung pada gain mikrofon dinyatakan RELATIF terhadap rekaman ruangan tenang
# dari mikrofon yang sama (lihat src/kalibrasi.py). REF_LEVEL = fitur level yang dikurangi baseline.
REF_LEVEL = (["level_db_p10", "level_db_p50", "level_db_p90"] + [f"banda_{i}_db" for i in range(1, len(BAND_HZ) + 1)]
             + ["mfcc_1_mean"])
REF_KEYS = ["rms_mean"] + REF_LEVEL                          # 11 nilai yang disimpan sebagai baseline
FEATURE_RELATIF = ["rel_rms_db", "rms_variasi"] + ["rel_" + k for k in REF_LEVEL]     # 12 fitur relatif
FEATURE_COLS = FEATURE_MENTAH + FEATURE_RELATIF              # 59 kolom yang diterima pipeline model
KALIBRASI_MAKS_RMS = 0.05        # baseline lebih keras dari ini = ruangan saat kalibrasi tidak tenang (peringatan)

# Pembersihan data (src/pembersihan.py). Kode perekam yang dikeluarkan beserta alasannya; data mentah tidak diubah.
KODE_DIABAIKAN = {
    "24-168": "label diduga terbalik: rekaman 'berisik' 13,8 dB lebih pelan daripada 'tidak berisik'; "
              "satu-satunya dari 35 perekam yang berlawanan arah",
}
