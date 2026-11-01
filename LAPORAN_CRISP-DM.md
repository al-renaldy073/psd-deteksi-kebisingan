# Laporan CRISP-DM: Deteksi Suara Berisik

## 1. Business Understanding
**Tujuan bisnis.** Sistem yang memantau suara ruangan lewat mikrofon, memutar peringatan suara manusia "Jangan rame!" saat
terdeteksi berisik, dan tidak memberi peringatan saat tidak berisik. Sistem dapat dipakai dari laptop maupun HP lewat browser.

**Revisi dosen yang dipenuhi.**
1. Perekaman streaming otomatis: rekam 10 detik, modelling, jeda 5 detik, berulang sampai dihentikan.
2. Pengujian beberapa algoritma (SVM, Random Forest, KNN, Decision Tree, Logistic Regression) dan pemilihan model terbaik.
3. Kombinasi fitur untuk meningkatkan akurasi (10 kombinasi).
4. Seleksi fitur sebelum modelling (SelectKBest di dalam pipeline, hanya dari data latih).
5. Perbandingan akurasi berbagai kombinasi fitur dan algoritma (Accuracy, Precision, Recall, F1-Score).
6. Implementasi kombinasi fitur dan model terbaik ke sistem (aplikasi web Flask).

**Tujuan data mining.** Mengklasifikasikan rekaman 10 detik menjadi berisik atau tidak berisik.
**Kriteria sukses.** Akurasi cross-validation per perekam minimal 0,85 dan prediksi streaming benar pada mikrofon pengguna.
Kriteria pertama terpenuhi (0,894, lihat bagian Evaluation); kriteria kedua masih perlu dikonfirmasi dengan data dari mikrofon pengguna.

## 2. Data Understanding
* 200 file mp3 (100 berisik, 100 tidak berisik) dari 35 kode perekam, durasi sekitar 30 sampai 48 detik.
* Masalah kualitas data yang ditemukan:
  1. 41 file berada dalam 18 kelompok audio identik (hanya 177 file unik).
  2. `24-034 berisik 1` dan `24-034 non-berisik 1` berisi audio yang sama tetapi berlabel berbeda.
  3. Kode `24-168` diduga berlabel terbalik: rekaman berisik 13,8 dB lebih pelan daripada tidak berisik, satu-satunya dari
     35 perekam yang berlawanan arah.
* **Temuan kunci:** kerasnya suara mentah bergantung pada gain mikrofon dan perangkat. Tiap perekam punya tingkat ruangan tenang
  berbeda, sehingga ambang RMS dari satu perekam tidak berlaku untuk perekam lain. Selisih dB terhadap ruangan tenang
  perekam yang sama jauh lebih konsisten antar perekam (`reports/01_boxplot_level.png`). Inilah yang membuat prediksi
  pada mikrofon baru sering meleset bila memakai level mentah.

## 3. Data Preparation
Urutan proses backend sebelum modelling (juga dijelaskan pada halaman Evaluasi model di website):
1. **Pengumpulan dataset audio.** 200 file di `data/raw/berisik` dan `data/raw/non-berisik`; kode perekam di awal nama file dipakai sebagai kelompok.
2. **Preprocessing audio.** Audio dimuat mono 16 kHz (librosa, ffmpeg sebagai cadangan) dan dibatasi 60 detik. Satu file utuh menjadi
   satu baris (tanpa chunking); saat streaming, satu rekaman 10 detik adalah satu sampel dengan fungsi fitur yang sama. Frame hening
   di tepi rekaman (RMS di bawah 0,001) dibuang.
3. **Ekstraksi fitur (Librosa, NumPy, FFT).** 47 fitur mentah: 32 fitur dasar librosa (mean dan std RMS, centroid, ZCR, 13 MFCC) dan
   15 fitur tambahan numpy dengan FFT (`rfft`, jendela Hann 2048, hop 512): persentil level suara dB, roll-off, bandwidth, flatness, dan
   energi enam pita frekuensi dB.
4. **Data cleaning** (`src/pembersihan.py`, file mentah tidak diubah): kode `24-168` dikeluarkan (6 file), salinan identik
   berlabel sama dipertahankan satu (22 file dikeluarkan), pasangan identik berlabel bertentangan dibuang (2 file).
   Hasil 170 file bersih. Daftar file yang dikeluarkan ada di `reports/pembersihan_data.csv`.
5. **Kalibrasi dan fitur relatif** (`src/kalibrasi.py`): fitur level (RMS, persentil level, energi pita, MFCC 1) dinyatakan sebagai selisih
   terhadap baseline ruangan tenang perekam yang sama, sehingga diperoleh 12 fitur relatif (total 59 kolom). Saat training, baseline sebuah file
   adalah median file tidak berisik LAIN dari perekam yang sama (file itu sendiri tidak dihitung). 6 file dari perekam
   tanpa klip tenang lain dikeluarkan, tersisa 164 file dari 32 perekam. Saat dipakai, baseline adalah median 1 sampai 3 klip kalibrasi.
6. **Pembagian data.** 80:20 **per perekam** (semua file satu perekam masuk ke satu sisi, `StratifiedGroupKFold`, seed 42):
   latih 132 file (64 berisik, 68 tidak berisik), uji 32 file (17 berisik, 15 tidak berisik).
7. **Standardisasi dan seleksi fitur.** Penskala (StandardScaler atau PowerTransformer) dan seleksi fitur SelectKBest berada di dalam
   pipeline, jadi hanya belajar dari data latih (tanpa kebocoran data).

## 4. Modelling
Lima algoritma diuji pada 10 kombinasi fitur (50 percobaan): delapan kombinasi dengan fitur relatif dan dua pembanding tanpa
kalibrasi. Parameter dicari dengan GridSearchCV dan cross-validation per perekam 2 x 5 lipatan pada data latih. Model terbaik
dipilih dari skor CV data latih, bukan dari data uji.

Hasil terbaik tiap algoritma (menurut CV data latih):

| Algoritma | Kombinasi fitur terbaik | CV akurasi | Accuracy uji | Precision | Recall | F1-Score |
|---|---|---|---|---|---|---|
| SVM | Semua fitur relatif | 0,892 | 0,906 | 0,850 | 1,000 | 0,919 |
| Logistic Regression | Semua fitur relatif | 0,883 | 0,938 | 0,895 | 1,000 | 0,944 |
| KNN | Semua fitur relatif | 0,864 | 0,875 | 0,842 | 0,941 | 0,889 |
| Random Forest | Level lengkap+spektral | 0,855 | 1,000 | 1,000 | 1,000 | 1,000 |
| Decision Tree | Level relatif | 0,814 | 0,781 | 0,812 | 0,765 | 0,788 |

Hasil terbaik tiap kombinasi fitur:

| Kombinasi fitur | Jumlah fitur | Algoritma | CV akurasi | Accuracy uji |
|---|---|---|---|---|
| Semua fitur relatif | 46 | SVM | 0,892 | 0,906 |
| Level lengkap+MFCC | 36 | SVM | 0,888 | 0,906 |
| Level lengkap+spektral | 22 | SVM | 0,883 | 0,969 |
| Level relatif lengkap | 12 | SVM | 0,875 | 0,938 |
| Level+pita frekuensi relatif | 10 | SVM | 0,871 | 0,938 |
| Level lengkap+ZCR+Centroid | 16 | SVM | 0,868 | 1,000 |
| Level relatif | 4 | SVM | 0,850 | 0,906 |
| Spektral+MFCC tanpa level | 34 | SVM | 0,828 | 0,625 |
| Tanpa kalibrasi: semua fitur mentah | 47 | Logistic Regression | 0,823 | 0,750 |
| Tanpa kalibrasi: RMS+Centroid+ZCR | 6 | SVM | 0,777 | 0,875 |

Tabel lengkap 50 percobaan: `reports/perbandingan_model.csv`. Model terpilih: **SVM (Semua fitur relatif)**, parameter
{'C': 0.3, 'kernel': 'linear', 'scaler': 'PowerTransformer'}, 23 dari 46 fitur lolos seleksi.

Fitur terpilih: `rel_rms_db`, `rms_variasi`, `rel_level_db_p10`, `rel_level_db_p50`, `rel_level_db_p90`, `rel_banda_1_db` sampai `rel_banda_6_db`,
`rel_mfcc_1_mean`, `zcr_mean`, `centroid_mean`, `rolloff_std`, `bandwidth_std`, `mfcc_3_mean`, `mfcc_5_mean`, `mfcc_7_mean`, `mfcc_8_mean`,
`mfcc_11_mean`, `mfcc_12_std`, `mfcc_13_std`.

## 5. Evaluation
Hold-out (32 file dari perekam yang tidak ada di data latih):

| | Prediksi berisik | Prediksi tidak berisik |
|---|---|---|
| **Aktual berisik** | 17 | 0 |
| **Aktual tidak berisik** | 3 | 12 |

Accuracy 90,6%, precision 85,0%, recall 100,0%, F1 91,9%.

| Protokol | Akurasi |
|---|---|
| CV per perekam 5 x 5, model terbaik (dengan kalibrasi) | 89,4% |
| CV per perekam 5 x 5, pembanding terbaik tanpa kalibrasi (Logistic Regression, semua fitur mentah) | 80,1% |

* Kalibrasi menaikkan akurasi per perekam sekitar 9 poin. Ini hasil pengukuran, bukan penyetelan: kedua angka memakai
  protokol yang sama.
* Data uji hanya 32 file, sehingga satu file menggeser accuracy sekitar 3 poin. Angka cross-validation lebih stabil.
  Beberapa algoritma lain (Logistic Regression, Random Forest) mendapat accuracy uji lebih tinggi daripada SVM, tetapi model dipilih dari
  skor CV data latih, bukan dari data uji, agar pemilihan tidak bias terhadap 32 file uji.
* Batasan yang jujur: (1) kelas tidak berisik berarti sama tenangnya dengan ruangan kalibrasi, jadi kalibrasi harus dilakukan saat
  ruangan tenang; (2) parameter dipilih dari CV pada data yang sama dengan sebagian estimasi, sehingga angka CV sedikit optimistis;
  (3) data kecil dan hanya 32 perekam; (4) akurasi pada mikrofon pengguna perlu dikonfirmasi dengan merekam data sendiri.
* Peningkatan lanjutan: rekam data berlabel dari mikrofon yang dipakai (halaman Kumpulkan data), tambah perekam dan ruangan,
  dan tambah fitur spektral.

## 6. Deployment
* Aplikasi web **Flask** (`app.py`) dengan antarmuka di browser, responsif untuk HP, tablet, dan laptop (navigasi bawah di HP).
  Mikrofon direkam di browser dengan echo cancellation, noise suppression, dan auto gain dimatikan agar level asli terjaga, lalu
  dikirim per 10 detik ke server sebagai PCM float32.
* Streaming: klik Mulai, kalibrasi ruangan tenang otomatis bila perlu, lalu rekam 10 detik, proses model, putar peringatan
  "Jangan rame!" bila berisik, jeda 5 detik, ulang sampai Berhenti. Pemrosesan audio browser berhenti selama peringatan
  diputar sehingga suara peringatan tidak ikut terekam. Prediksi memakai fungsi fitur, urutan kolom, dan pipeline yang
  sama dengan training.
* **Akses HP dan laptop.** Browser hanya mengizinkan mikrofon pada HTTPS (atau localhost), sehingga aplikasi disediakan di belakang
  reverse proxy HTTPS (Caddy, `docker-compose.https.yml`, `Caddyfile`) dengan sertifikat otomatis. Flask memakai `ProxyFix` untuk membaca
  header `X-Forwarded-*`, mengirim header `Permissions-Policy: microphone=(self)`, dan antarmuka menampilkan pesan yang jelas bila halaman
  tidak aman atau izin mikrofon ditolak. AudioContext dibuat saat pengguna menekan tombol agar tidak diblokir browser HP, dan Wake Lock
  meminta layar tetap menyala selama streaming bila didukung.
* Halaman **Prediksi file** hanya meminta file audio yang ingin dideteksi. Baseline ruangan tenang diambil dari kalibrasi tersimpan di perangkat,
  sehingga perhitungan fitur relatif dan model SVM tidak berubah.
* Halaman **Evaluasi model** menampilkan kartu Accuracy, Precision, Recall, F1-score, informasi cross-validation dan dataset, fitur terpilih,
  confusion matrix, perbandingan algoritma, dan penjelasan alur proses backend sebelum modelling. Angkanya dibaca dari `reports/hasil_evaluasi.json`,
  `reports/perbandingan_model.csv`, dan model tersimpan.
* Halaman Kumpulkan data menyimpan klip berlabel ke `data/raw`.
* Docker tersedia (`Dockerfile`, `docker-compose.yml`); karena mikrofon dan speaker dipakai dari browser, aplikasi berjalan juga di dalam container.
* Pemeliharaan: tambah data, jalankan `python src/extract_dataset.py` lalu `python src/train.py`.
* Revisi tampilan, akses mikrofon, dan dokumentasi tidak mengubah dataset, fitur, algoritma, parameter, model SVM, maupun hasil evaluasi di atas.
