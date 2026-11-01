"""Pembersihan data sebelum training dan evaluasi (tanpa mengubah file mentah).

Aturan, berurutan:
1. Kode perekam di config.KODE_DIABAIKAN dikeluarkan (label diduga terbalik).
2. File dengan 32 fitur dasar identik adalah salinan audio yang sama. Bila labelnya BERBEDA (kontradiksi), semua
   salinannya dibuang; bila labelnya sama, hanya satu yang disimpan.
Alasan: salinan identik yang tersebar di data latih dan uji membuat akurasi tampak lebih tinggi dari sebenarnya
(kebocoran data), dan label yang kontradiktif atau terbalik hanya menjadi derau bagi model.
"""
import pandas as pd

from config import FEATURE_DASAR, KODE_DIABAIKAN


def bersihkan(df):
    """Mengembalikan (df_bersih, log) dengan log berisi setiap file yang dikeluarkan beserta alasannya."""
    df = df.reset_index(drop=True)
    log = []
    keluar = df["speaker_id"].isin(KODE_DIABAIKAN)
    for _, r in df[keluar].iterrows():
        log.append({"filename": r["filename"], "label": r["label"], "alasan": "kode diabaikan: " + KODE_DIABAIKAN[r["speaker_id"]]})
    sisa = df[~keluar]
    kunci = sisa[FEATURE_DASAR].round(5).astype(str).agg("|".join, axis=1)
    for _, grp in sisa.groupby(kunci):
        if len(grp) < 2:
            continue
        if grp["label"].nunique() > 1:
            for _, r in grp.iterrows():
                log.append({"filename": r["filename"], "label": r["label"], "alasan": "audio identik dengan label berbeda (kontradiksi)"})
            keluar.loc[grp.index] = True
        else:
            urut = grp.sort_values("filename")
            for _, r in urut.iloc[1:].iterrows():
                log.append({"filename": r["filename"], "label": r["label"], "alasan": "salinan identik dari " + urut.iloc[0]["filename"]})
            keluar.loc[urut.index[1:]] = True
    return df[~keluar].reset_index(drop=True), pd.DataFrame(log, columns=["filename", "label", "alasan"])
