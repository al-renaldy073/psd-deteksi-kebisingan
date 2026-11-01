# Deteksi Suara Berisik (Noise Detection) - Kelompok 6

**Kelompok 6**

| Nama Anggota | NIM |
|---|---|
| Rachelia Andini Tendean | 240411100007 |
| Ahmad Syahrul Farihin | 240411100031 |
| Rian Renaldy | 240411100053 |

Aplikasi web (Flask) yang merekam mikrofon otomatis dengan pola **rekam 10 detik, proses model, peringatan suara
"Jangan rame!" bila berisik, jeda 5 detik, ulang** sampai dihentikan. Kebisingan dinilai **relatif terhadap ruangan tenang**
dari mikrofon yang sama (kalibrasi), sehingga hasilnya tidak bergantung pada gain atau merek mikrofon.
Tampilan memakai tema gelap bergaya Sound Lab, responsif untuk HP, tablet, dan laptop.

## Fitur website
| Halaman | Fungsi |
|---|---|
| **Streaming mikrofon** | Kalibrasi ruangan tenang (2 x 10 detik), lalu deteksi berulang dengan peringatan suara dan riwayat hasil. |
| **Prediksi file** | Unggah satu file audio; dipotong per 10 detik (maksimal 6 potongan) dan tiap potongan diprediksi. Baseline memakai kalibrasi tersimpan di perangkat. |
| **Kumpulkan data** | Rekam klip 10 detik berlabel ke `data/raw` untuk melatih ulang model. |
| **Evaluasi model** | Kartu Accuracy, Precision, Recall, F1-score, informasi cross-validation dan jumlah dataset, fitur terpilih, confusion matrix, perbandingan algoritma, alur proses backend sebelum modelling, dan grafik hasil pelatihan. Semua angka dibaca dari `reports/` dan model tersimpan, tidak ditulis manual. |

Di HP, menu pindah ke bilah navigasi bawah; di laptop berada di bagian atas. Alamat halaman dapat dibuka langsung lewat
`#stream`, `#prediksi`, `#data`, dan `#eval`.

## Cara menjalankan (lokal)
```bash
python -m venv .venv
.venv\Scripts\activate                  # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt --default-timeout=300 --retries 10
python src/extract_dataset.py            # opsional: hitung ulang fitur memakai librosa di komputer Anda
python src/train.py                      # opsional: latih ulang dan buat tabel perbandingan (sekitar 30 menit)
python app.py
```
Buka **http://localhost:5000** di komputer yang sama. Model hasil penelitian sudah tersedia di `models/`, jadi
`extract_dataset.py` dan `train.py` hanya perlu dijalankan bila dataset atau definisi fitur berubah.

Variabel lingkungan opsional: `HOST` (bawaan `0.0.0.0`), `PORT` (bawaan `5000`), `TRUST_PROXY` (bawaan `1`, percaya header
`X-Forwarded-*` dari reverse proxy; isi `0` bila Flask diakses langsung tanpa proxy).

## Akses dari HP dan laptop lain (HTTPS)
Browser **hanya mengizinkan mikrofon pada HTTPS** (atau `localhost` di perangkat yang sama). Membuka `http://192.168.x.x:5000`
dari HP akan membuat tombol mikrofon ditolak; aplikasi menampilkan pesan penjelasan bila itu terjadi. Fitur Prediksi file dan
Evaluasi model tetap berjalan lewat HTTP.

Cara yang disediakan di project ini adalah **Docker + Caddy** (sertifikat Let's Encrypt otomatis):
```bash
DOMAIN=nama-domain-kamu.example docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```
Syarat: domain sudah mengarah (DNS A/AAAA) ke IP publik server, port TCP 80 dan 443 terbuka. Setelah itu buka
`https://nama-domain-kamu.example` dari HP maupun laptop. Caddy meneruskan permintaan ke container aplikasi dan menambahkan
header `X-Forwarded-Proto`, yang dibaca Flask lewat `ProxyFix`. Untuk server yang hanya melayani HTTPS, port `8006` pada
`docker-compose.yml` bisa dibatasi ke `127.0.0.1:8006:8006` agar tidak terbuka sebagai HTTP.

Alternatif lain yang juga menghasilkan alamat HTTPS adalah tunnel (mis. Cloudflare Tunnel atau ngrok) yang diarahkan ke
`http://localhost:5000` atau `:8006`. Alternatif ini tidak disertakan konfigurasinya di project.

### Bila mikrofon tidak bisa dipakai
* Pesan "tidak memakai HTTPS": buka lewat alamat `https://`, bukan IP atau `http://`.
* Pesan "izin mikrofon ditolak": ubah izin Mikrofon untuk situs ini di pengaturan browser, lalu muat ulang halaman.
* Pesan "mikrofon tidak dapat dibuka": tutup aplikasi lain yang memakai mikrofon (panggilan, perekam suara).
* Di dalam aplikasi lain (WhatsApp, Instagram), buka tautan lewat browser bawaan HP.
* Di HP, biarkan layar menyala selama streaming. Aplikasi meminta Wake Lock agar layar tidak mati, tetapi tidak semua browser mendukungnya.
* Kalibrasi disimpan per browser/perangkat. HP dan laptop memiliki mikrofon berbeda, jadi masing-masing perlu kalibrasi sendiri.

## Memakai streaming
1. Buka halaman Streaming, klik **Mulai streaming**, lalu izinkan mikrofon di browser.
2. Saat pertama kali (atau mikrofon berganti) aplikasi merekam kalibrasi 2 x 10 detik. **Jaga ruangan tenang** selama itu.
3. Streaming berjalan sendiri. Bila berisik, suara peringatan diputar di speaker; mikrofon tidak memproses audio saat itu,
   jadi suara peringatan tidak ikut terekam. Klik **Berhenti** untuk menghentikan.
4. Pindah ruangan atau mengubah volume mikrofon? Klik **Kalibrasi ulang**.
5. Gunakan **Tes suara** untuk memastikan speaker berbunyi. Suara peringatan ada di `assets/jangan_rame.mp3`
   (atau `.wav`); ganti dengan rekaman suara Anda sendiri bila perlu.

## Memakai Prediksi file
1. Lakukan kalibrasi sekali di halaman Streaming (tombol **Kalibrasi ulang**). Baseline ruangan tenang tersimpan di browser.
2. Buka **Prediksi file**, pilih file audio (maksimal 25 MB), lalu klik **Analisis audio**.
3. Hasil tiap potongan 10 detik muncul di tabel, dan kesimpulan memakai rata-rata peluang berisik.

Halaman ini tidak lagi menyediakan unggah file "Ruangan tenang". Mekanisme baseline tetap dipakai karena model SVM bekerja pada
fitur relatif terhadap ruangan tenang; bedanya baseline diambil dari kalibrasi yang sudah ada. Bila belum ada kalibrasi, halaman
menampilkan petunjuk dan tombol menuju Streaming.

## Docker (Kelompok 6, port 8006)
File yang dibutuhkan ada di root: `Dockerfile`, `docker-compose.yml`, `requirements.txt`.
```bash
docker compose up --build        # build image lalu jalankan
docker compose up -d --build     # di latar belakang
docker compose down              # hentikan
```
Atau tanpa compose:
```bash
docker build -t noise-detection:kelompok6 .
docker run -p 8006:8006 noise-detection:kelompok6
```
Buka **http://localhost:8006** di komputer yang sama. Mikrofon dan speaker dipakai dari browser, bukan dari dalam container,
sehingga tidak perlu akses perangkat audio ke Docker. Untuk HP dan laptop lain gunakan HTTPS (bagian di atas).
Rekaman baru dari halaman Kumpulkan data tersimpan di folder `data/` lewat volume pada `docker-compose.yml`.

## Push ke GitHub
```bash
git init
git add .
git commit -m "Deteksi suara berisik: Flask + Docker (kelompok 6)"
git branch -M main
git remote add origin https://github.com/<username>/<nama-repo>.git
git push -u origin main
```

## Endpoint
| Endpoint | Metode | Keterangan |
|---|---|---|
| `/` | GET | Halaman web. |
| `/healthz` | GET | Pemeriksaan sederhana status server dan keberadaan model. |
| `/api/info` | GET | Model, fitur terpilih, hasil evaluasi, tabel perbandingan, jumlah data, dan konfigurasi. |
| `/api/kalibrasi` | POST | Satu klip ruangan tenang menjadi nilai referensi baseline. |
| `/api/prediksi` | POST | Prediksi satu rekaman dengan baseline hasil kalibrasi. |
| `/api/simpan` | POST | Simpan klip berlabel ke `data/raw`. |
| `/api/peringatan` | GET | File suara peringatan. |
| `/reports/<nama>` | GET | Grafik dan laporan hasil pelatihan. |

Audio dikirim sebagai PCM float32 mentah (`audio`) bersama `sr` (sample rate) dalam `multipart/form-data`; batas ukuran 40 MB.

## Struktur proyek
```
app.py                  # server Flask dan API
templates/index.html    # halaman web
static/app.js, style.css# antarmuka (rekam browser, streaming, kalibrasi, evaluasi)
assets/jangan_rame.mp3  # suara peringatan
src/config.py           # konstanta dan definisi fitur
src/features.py         # ekstraksi fitur (librosa + fitur tambahan numpy)
src/pembersihan.py      # pembersihan data: salinan identik, label kontradiktif, kode berlabel terbalik
src/kalibrasi.py        # fitur relatif terhadap ruangan tenang
src/extract_dataset.py  # mp3/wav ke data/processed/fitur_audio.csv
src/train.py            # perbandingan 5 algoritma x 10 kombinasi fitur, seleksi fitur, evaluasi, simpan model
src/inference.py        # fitur, kalibrasi, dan prediksi dari sinyal
predict_cli.py          # prediksi dari file lewat terminal
Dockerfile, docker-compose.yml, docker-compose.https.yml, Caddyfile
data/raw, data/processed, models, reports
LAPORAN_CRISP-DM.md     # laporan CRISP-DM
```

## Dependensi
`requirements.txt`: flask, librosa, numpy, pandas, scipy, scikit-learn, matplotlib, seaborn, joblib, imageio-ffmpeg.
Docker memakai Python 3.11 dan memasang `ffmpeg` serta `libsndfile1`. Jika scikit-learn di komputer Anda berbeda versi dari
yang dipakai saat model disimpan dan muncul peringatan versi, jalankan ulang `python src/train.py`.

## Hasil (angka nyata dari `reports/hasil_evaluasi.json`)
* Data: 200 file mentah, 170 setelah pembersihan, 164 dipakai (punya baseline), 32 perekam.
* Model terbaik: **SVM (Semua fitur relatif)**, parameter {'C': 0.3, 'kernel': 'linear', 'scaler': 'PowerTransformer'}, 23 dari 46 fitur terpilih.
* Cross-validation per perekam pada seluruh data: **89,4%** (std 1,5%); pembanding terbaik tanpa kalibrasi: 80,1% (Logistic Regression, semua fitur mentah).
* Data uji (32 file, perekam tidak ada di data latih): accuracy 90,6%, precision 85,0%, recall 100,0%, F1 91,9%.
  Confusion matrix (baris aktual berisik, tidak berisik): [[17, 0], [3, 12]].
* Tabel lengkap: `reports/perbandingan_model.csv`; peta panas: `reports/05_perbandingan_model.png`.

## Meningkatkan akurasi di mikrofon Anda
Buka halaman **Kumpulkan data**, rekam klip berlabel (berisik dan tidak berisik) dengan nama sesi berbeda (mic01, mic02, mic03),
lalu jalankan `python src/extract_dataset.py` dan `python src/train.py`. Label harus sesuai isi klip.

## Pengaturan
`src/config.py`: `KODE_DIABAIKAN` (kode perekam yang dikeluarkan), `KALIBRASI_MAKS_RMS` (batas ruangan dianggap tidak tenang).
Setelah mengubah definisi fitur, naikkan `FITUR_VERSI`, lalu jalankan ulang `extract_dataset.py` dan `train.py`.
