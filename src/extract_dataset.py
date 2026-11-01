"""Ekstraksi dataset MP3 di data/raw/{berisik,non-berisik} menjadi CSV fitur.

python src/extract_dataset.py                 ekstraksi penuh (47 fitur) dari seluruh audio mentah
python src/extract_dataset.py --tambah-fitur  hanya menghitung 15 fitur tambahan dan menggabungkannya ke CSV yang
                                              sudah ada (32 fitur dasar tidak dihitung ulang, jauh lebih cepat)
"""
import sys
import pandas as pd
from pathlib import Path
from config import RAW_DIR, CSV_PATH, FEATURE_MENTAH, FEATURE_TAMBAHAN, SR, CHUNK_DETIK
from features import load_audio, potong_audio, features_from_signal, _fitur_tambahan

LABEL_DIRS = {"berisik": "berisik", "tidak_berisik": "non-berisik"}

def tambah_fitur():
    """Hitung fitur tambahan dari audio mentah dan gabungkan ke CSV berdasarkan nama file."""
    df = pd.read_csv(CSV_PATH).drop(columns=[c for c in FEATURE_TAMBAHAN if c in pd.read_csv(CSV_PATH, nrows=0).columns])
    folder = {"berisik": "berisik", "tidak_berisik": "non-berisik"}
    baris = []
    for no, r in enumerate(df.itertuples(), 1):
        path = RAW_DIR / folder[r.label] / r.filename
        y, sr = load_audio(path, SR, max_seconds=None)
        baris.append(_fitur_tambahan(y, sr))
        if no % 25 == 0:
            print(f"[{no}/{len(df)}] fitur tambahan dihitung")
    df = pd.concat([df, pd.DataFrame(baris, index=df.index)], axis=1)
    df = df[["filename", "chunk", "speaker_id"] + FEATURE_MENTAH + ["label"]]
    df.to_csv(CSV_PATH, index=False)
    print(f"CSV diperbarui: {CSV_PATH} ({len(df)} baris, {len(FEATURE_MENTAH)} fitur)")


def main():
    if "--tambah-fitur" in sys.argv:
        return tambah_fitur()
    rows = []
    for label, folder in LABEL_DIRS.items():
        lokasi = RAW_DIR / folder
        if not lokasi.exists():
            print(f"Folder dilewati (tidak ditemukan): {lokasi}")
            continue
        files = sorted(p for p in lokasi.rglob("*") if p.suffix.lower() in {".mp3", ".wav", ".ogg", ".flac", ".m4a"})
        for no, path in enumerate(files, 1):
            try:
                y, sr = load_audio(path, SR, max_seconds=None)
                speaker = path.stem.split()[0].split("_")[0]
                for i, chunk in enumerate(potong_audio(y, sr, CHUNK_DETIK)):
                    feat = features_from_signal(chunk, sr)
                    feat.update({"filename": path.name, "chunk": i, "speaker_id": speaker, "label": label})
                    rows.append(feat)
                print(f"[{no}/{len(files)}] {path.name}")
            except Exception as e:
                print(f"GAGAL {path.name}: {e}")
    if not rows:
        raise RuntimeError("Tidak ada audio yang berhasil diekstrak.")
    df = pd.DataFrame(rows)
    # Susunan kolom sesuai kontrak train.py.
    df = df[["filename", "chunk", "speaker_id"] + FEATURE_MENTAH + ["label"]]
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV_PATH, index=False)
    print(f"CSV tersimpan: {CSV_PATH} ({len(df)} potongan)")

if __name__ == "__main__":
    main()
