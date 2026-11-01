"""Tahap Data Understanding, Modelling, Evaluation.

Alur: baca fitur_audio.csv, bersihkan data (src/pembersihan.py), hitung fitur relatif terhadap ruangan tenang
(src/kalibrasi.py), bagi data 80:20 per perekam, lalu bandingkan 5 algoritma pada 10 kombinasi fitur.
Standardisasi dan seleksi fitur (SelectKBest) berada DI DALAM pipeline, jadi pada setiap lipatan cross-validation
keduanya hanya belajar dari data latih (tanpa kebocoran data). Model terbaik dipilih dari skor cross-validation
pada data latih; data uji hanya dipakai untuk melaporkan hasil. Model yang disimpan dilatih hanya dari data latih.

Jalankan:  python src/train.py
Output   : reports/*.png, reports/perbandingan_model.csv, reports/hasil_evaluasi.json,
           reports/pembersihan_data.csv, models/model_kebisingan.joblib
"""
import json
import sys
import warnings
from pathlib import Path

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import SelectKBest, f_classif
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, confusion_matrix, ConfusionMatrixDisplay, f1_score,
                             precision_score, recall_score)
from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PowerTransformer, StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")
from config import (CSV_PATH, MODEL_PATH, REPORT_DIR, FEATURE_COLS, FEATURE_MENTAH, FEATURE_RELATIF, SR,
                    CHUNK_DETIK, RMS_HENING, N_MFCC, FITUR_VERSI, REF_KEYS)
from kalibrasi import bangun_relatif
from pembersihan import bersihkan

SEED = 42
POS = "berisik"
REPORT_DIR.mkdir(exist_ok=True)
MODEL_PATH.parent.mkdir(exist_ok=True)

# ------------------------------------------------------------------ kombinasi fitur
_LEVEL = ["rel_rms_db", "rel_level_db_p10", "rel_level_db_p50", "rel_level_db_p90"]
_PITA = [f"rel_banda_{i}_db" for i in range(1, 7)]
_SPEKTRAL = [f"{n}_{s}" for n in ("zcr", "centroid", "rolloff", "bandwidth", "flatness") for s in ("mean", "std")]
_MFCC = [f"mfcc_{i}_{s}" for i in range(2, N_MFCC + 1) for s in ("mean", "std")]   # MFCC 1 (energi) sudah ada di fitur relatif
KOMBINASI_FITUR = {
    "Level relatif": _LEVEL,
    "Level+pita frekuensi relatif": _LEVEL + _PITA,
    "Level relatif lengkap": list(FEATURE_RELATIF),
    "Level lengkap+ZCR+Centroid": FEATURE_RELATIF + ["zcr_mean", "zcr_std", "centroid_mean", "centroid_std"],
    "Level lengkap+spektral": FEATURE_RELATIF + _SPEKTRAL,
    "Level lengkap+MFCC": FEATURE_RELATIF + _MFCC,
    "Semua fitur relatif": FEATURE_RELATIF + _SPEKTRAL + _MFCC,
    "Spektral+MFCC tanpa level": _SPEKTRAL + _MFCC,
    # pembanding tanpa kalibrasi (fitur level mentah, bergantung pada gain mikrofon)
    "Tanpa kalibrasi: RMS+Centroid+ZCR": ["rms_mean", "rms_std", "centroid_mean", "centroid_std", "zcr_mean", "zcr_std"],
    "Tanpa kalibrasi: semua fitur mentah": list(FEATURE_MENTAH),
}

# ------------------------------------------------------------------ algoritma dan parameter yang dicari
ALGORITMA = {
    "SVM": (SVC(probability=True, random_state=SEED),
            {"clf__C": [0.3, 1, 10], "clf__kernel": ["rbf", "linear"]}),
    "Random Forest": (RandomForestClassifier(random_state=SEED),
                      {"clf__n_estimators": [100], "clf__max_depth": [None, 6]}),
    "KNN": (KNeighborsClassifier(),
            {"clf__n_neighbors": [3, 5, 9], "clf__weights": ["uniform", "distance"]}),
    "Decision Tree": (DecisionTreeClassifier(random_state=SEED),
                      {"clf__max_depth": [None, 4, 8]}),
    "Logistic Regression": (LogisticRegression(max_iter=2000, random_state=SEED),
                            {"clf__C": [0.1, 1, 10]}),
}


def buat_pipeline(kolom, clf):
    """Pilih kolom kombinasi, skala, seleksi fitur (SelectKBest), classifier. Menerima DataFrame berisi FEATURE_COLS."""
    return Pipeline([("kombinasi", ColumnTransformer([("pilih", "passthrough", kolom)], remainder="drop")),
                     ("scaler", StandardScaler()),
                     ("seleksi", SelectKBest(f_classif)),
                     ("clf", clf)])


def lipatan(y, groups, ulang, seed=SEED):
    """Daftar (latih, validasi) cross-validation per perekam, diulang `ulang` kali dengan pengacakan berbeda."""
    out = []
    for r in range(ulang):
        out += list(StratifiedGroupKFold(5, shuffle=True, random_state=seed + r).split(np.zeros(len(y)), y, groups))
    return out


def cv_per_perekam(model, X, y, groups, ulang=5):
    """Akurasi cross-validation per perekam (perekam di data uji tidak pernah ada di data latih)."""
    hasil = []
    for r in range(ulang):
        akur = []
        for tr, te in StratifiedGroupKFold(5, shuffle=True, random_state=1000 + r).split(X, y, groups):
            m = clone(model).fit(X.iloc[tr], y.iloc[tr])
            akur.append((m.predict(X.iloc[te]) == y.iloc[te].values).mean())
        hasil.append(np.mean(akur))
    return float(np.mean(hasil)), float(np.std(hasil))


def eksperimen(Xtr, ytr, gtr, Xte, yte):
    cvs = lipatan(ytr, gtr, ulang=2)
    baris, modelnya = [], {}
    total = len(KOMBINASI_FITUR) * len(ALGORITMA)
    n = 0
    for nama_komb, kolom in KOMBINASI_FITUR.items():
        k_opsi = sorted({min(8, len(kolom)), max(2, len(kolom) // 2), len(kolom)})
        for nama_algo, (clf, grid) in ALGORITMA.items():
            n += 1
            g = {"seleksi__k": k_opsi, "scaler": [StandardScaler(), PowerTransformer()], **grid}
            gs = GridSearchCV(buat_pipeline(kolom, clf), g, cv=cvs, scoring="accuracy", n_jobs=-1)
            gs.fit(Xtr, ytr)
            pred = gs.best_estimator_.predict(Xte)
            baris.append({
                "Kombinasi fitur": nama_komb, "Jumlah fitur": len(kolom), "Algoritma": nama_algo,
                "Fitur terpilih (k)": gs.best_params_["seleksi__k"],
                "CV Akurasi (latih)": round(float(gs.best_score_), 4),
                "Accuracy": round(accuracy_score(yte, pred), 4),
                "Precision": round(precision_score(yte, pred, pos_label=POS, zero_division=0), 4),
                "Recall": round(recall_score(yte, pred, pos_label=POS, zero_division=0), 4),
                "F1-Score": round(f1_score(yte, pred, pos_label=POS, zero_division=0), 4),
                "Parameter": {k.replace("clf__", ""): (type(v).__name__ if k == "scaler" else v)
                              for k, v in gs.best_params_.items() if k != "seleksi__k"},
            })
            modelnya[len(baris) - 1] = gs.best_estimator_
            print(f"[{n:2d}/{total}] {nama_komb:36s} {nama_algo:20s} CV={gs.best_score_:.3f}  "
                  f"uji: acc={baris[-1]['Accuracy']:.3f} f1={baris[-1]['F1-Score']:.3f}", flush=True)
    return pd.DataFrame(baris), modelnya


def main():
    raw = pd.read_csv(CSV_PATH)
    kurang = [c for c in FEATURE_MENTAH + ["filename", "speaker_id", "label"] if c not in raw.columns]
    if kurang:
        raise SystemExit(f"CSV belum sesuai versi fitur ({kurang[:3]} ...). Jalankan dulu: python src/extract_dataset.py")
    n_mentah = len(raw)
    bersih, log = bersihkan(raw)
    log.to_csv(REPORT_DIR / "pembersihan_data.csv", index=False)
    print(f"Pembersihan data: {n_mentah} file mentah menjadi {len(bersih)} file bersih ({len(log)} dikeluarkan):")
    print(log["alasan"].str.replace(r"dari .*", "dari file lain", regex=True).value_counts().to_string())
    df, n_tanpa_baseline = bangun_relatif(bersih)
    print(f"Fitur relatif: {len(df)} file memiliki baseline ruangan tenang ({n_tanpa_baseline} file perekam tanpa klip tenang lain dikeluarkan)")

    X, y, groups = df[FEATURE_COLS], df["label"], df["speaker_id"]
    print(f"Data akhir: {len(df)} file dari {groups.nunique()} perekam | kelas: {y.value_counts().to_dict()}")

    # Visualisasi dasar: kerasnya suara mentah vs relatif per kelas
    fig, ax = plt.subplots(1, 2, figsize=(9, 4))
    sns.boxplot(data=df, x="label", y="rms_mean", ax=ax[0]); ax[0].set_yscale("log"); ax[0].set_title("RMS mentah (skala log)")
    sns.boxplot(data=df, x="label", y="rel_rms_db", ax=ax[1]); ax[1].set_title("Level relatif ruangan tenang (dB)")
    plt.tight_layout(); plt.savefig(REPORT_DIR / "01_boxplot_level.png", dpi=130); plt.close()

    # Pembagian 80:20 per perekam: semua file satu perekam masuk ke satu sisi saja
    tr, te = next(StratifiedGroupKFold(5, shuffle=True, random_state=SEED).split(X, y, groups))
    Xtr, Xte, ytr, yte, gtr = X.iloc[tr], X.iloc[te], y.iloc[tr], y.iloc[te], groups.iloc[tr]
    print(f"\nLatih={len(tr)} file ({gtr.nunique()} perekam)  Uji={len(te)} file ({groups.iloc[te].nunique()} perekam)")
    print("Kelas latih:", ytr.value_counts().to_dict(), "| kelas uji:", yte.value_counts().to_dict())

    print("\n" + "=" * 60 + "\nMODELLING: 5 algoritma x 10 kombinasi fitur (seleksi fitur di dalam pipeline)\n" + "=" * 60)
    tabel, modelnya = eksperimen(Xtr, ytr, gtr, Xte, yte)

    # Model terbaik dipilih HANYA dari CV data latih; seri dimenangkan oleh fitur lebih sedikit.
    urut = tabel.sort_values(["CV Akurasi (latih)", "Jumlah fitur"], ascending=[False, True])
    idx = int(urut.index[0])
    best, b = modelnya[idx], tabel.loc[idx]
    best_name = f"{b['Algoritma']} ({b['Kombinasi fitur']})"
    print(f"\nMODEL TERBAIK (berdasar CV data latih): {best_name}  CV={b['CV Akurasi (latih)']:.3f}  "
          f"k={b['Fitur terpilih (k)']}  params={b['Parameter']}")

    tampil = urut.reset_index(drop=True)
    tampil.insert(0, "Peringkat", range(1, len(tampil) + 1))
    tampil["Parameter"] = tampil["Parameter"].astype(str)
    tampil.to_csv(REPORT_DIR / "perbandingan_model.csv", index=False)
    pv = tabel.pivot(index="Kombinasi fitur", columns="Algoritma", values="Accuracy").loc[list(KOMBINASI_FITUR), list(ALGORITMA)]
    plt.figure(figsize=(10, 6)); sns.heatmap(pv, annot=True, fmt=".3f", cmap="YlGnBu", vmin=.5, vmax=1)
    plt.title("Accuracy data uji per kombinasi fitur dan algoritma"); plt.tight_layout()
    plt.savefig(REPORT_DIR / "05_perbandingan_model.png", dpi=130); plt.close()
    print("\nTABEL PERBANDINGAN (10 teratas menurut CV; lengkap di reports/perbandingan_model.csv):")
    print(tampil.drop(columns=["Parameter"]).head(10).to_string(index=False))

    # ------------------------------------------------------------ evaluasi
    print("\n" + "=" * 60 + "\nEVALUATION (hold-out, perekam uji tidak ada di data latih)\n" + "=" * 60)
    pred = best.predict(Xte)
    cm = confusion_matrix(yte, pred, labels=["berisik", "tidak_berisik"])
    ConfusionMatrixDisplay(cm, display_labels=["berisik", "tidak_berisik"]).plot(cmap="Blues")
    plt.title(f"Confusion Matrix (data uji) - {b['Algoritma']}"); plt.tight_layout()
    plt.savefig(REPORT_DIR / "04_confusion_matrix.png", dpi=130); plt.close()
    uji = {"accuracy": accuracy_score(yte, pred), "precision": precision_score(yte, pred, pos_label=POS, zero_division=0),
           "recall": recall_score(yte, pred, pos_label=POS, zero_division=0), "f1": f1_score(yte, pred, pos_label=POS, zero_division=0)}
    print({k: round(v, 3) for k, v in uji.items()}, "\n", cm)

    cv_b, sd_b = cv_per_perekam(best, X, y, groups)
    print(f"\nCV per perekam 5x5 pada SELURUH data, model terbaik ({best_name}): {cv_b:.1%} (std {sd_b:.1%})")
    tk = tabel[tabel["Kombinasi fitur"].str.startswith("Tanpa kalibrasi")]
    ik = int(tk.sort_values(["CV Akurasi (latih)", "Jumlah fitur"], ascending=[False, True]).index[0])
    cv_k, sd_k = cv_per_perekam(modelnya[ik], X, y, groups)
    nama_k = f"{tabel.loc[ik, 'Algoritma']} ({tabel.loc[ik, 'Kombinasi fitur']})"
    print(f"CV per perekam 5x5, pembanding terbaik TANPA kalibrasi ({nama_k}): {cv_k:.1%} (std {sd_k:.1%})")

    kolom_dipakai = KOMBINASI_FITUR[b["Kombinasi fitur"]]
    seleksi = best.named_steps["seleksi"]
    skor = pd.Series(seleksi.scores_, index=kolom_dipakai)
    terpilih = [c for c, m in zip(kolom_dipakai, seleksi.get_support()) if m]
    print(f"Fitur terpilih ({len(terpilih)}/{len(kolom_dipakai)}):", terpilih)
    plt.figure(figsize=(7, 5)); skor.nlargest(12)[::-1].plot(kind="barh")
    plt.title("12 fitur dengan skor ANOVA-F tertinggi (data latih)"); plt.tight_layout()
    plt.savefig(REPORT_DIR / "06_feature_importance.png", dpi=130); plt.close()

    # ------------------------------------------------------------ simpan
    joblib.dump({"model": best, "features": FEATURE_COLS, "classes": list(best.classes_), "model_name": best_name,
                 "algoritma": b["Algoritma"], "kombinasi_fitur": b["Kombinasi fitur"], "fitur_kombinasi": kolom_dipakai,
                 "fitur_terpilih": terpilih, "parameter": b["Parameter"], "fitur_versi": FITUR_VERSI,
                 "pakai_kalibrasi": not b["Kombinasi fitur"].startswith("Tanpa kalibrasi"),
                 "config": {"sr": SR, "chunk_detik": CHUNK_DETIK, "rms_hening": RMS_HENING, "n_mfcc": N_MFCC,
                            "ref_keys": REF_KEYS}}, MODEL_PATH)
    ringkas = {"model": best_name, "n_file_mentah": n_mentah, "n_file_bersih": len(bersih), "n_file_dipakai": len(df),
               "n_perekam": int(groups.nunique()), "n_train": len(tr), "n_test": len(te),
               "pembersihan": log["alasan"].str.replace(r"dari .*", "dari file lain", regex=True).value_counts().to_dict(),
               "tanpa_baseline": n_tanpa_baseline,
               "test": {k: round(float(v), 4) for k, v in uji.items()},
               "confusion_matrix(berisik,tidak_berisik)": cm.tolist(),
               "cv_per_perekam": {"model_terbaik": round(cv_b, 4), "std": round(sd_b, 4),
                                  "pembanding_tanpa_kalibrasi": round(cv_k, 4), "std_pembanding": round(sd_k, 4),
                                  "nama_pembanding": nama_k},
               "model_terbaik": {"algoritma": b["Algoritma"], "kombinasi_fitur": b["Kombinasi fitur"],
                                 "fitur_terpilih": terpilih, "params": b["Parameter"],
                                 "cv_akurasi_latih": float(b["CV Akurasi (latih)"])},
               "sample_rate": SR, "chunk_detik": CHUNK_DETIK}
    json.dump(ringkas, open(REPORT_DIR / "hasil_evaluasi.json", "w"), indent=2, default=str)
    print("\nModel tersimpan:", MODEL_PATH)


if __name__ == "__main__":
    main()
