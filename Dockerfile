# Image aplikasi Deteksi Suara Berisik (Flask). Seluruh langkah menjalankan ada di sini:
# pasang dependensi sistem, pasang paket Python, salin proyek, lalu jalankan server saat container start.
FROM python:3.11-slim

# ffmpeg dan libsndfile1 dibutuhkan librosa untuk membaca audio (mp3/wav)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Salin requirements dulu agar lapisan pip ter-cache bila kode saja yang berubah
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Satu port saja yang dibuka (kelompok 6: 8006). HOST=0.0.0.0 agar dapat diakses dari luar container.
ENV HOST=0.0.0.0 \
    PORT=8006 \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba
EXPOSE 8006

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8006'))"

CMD ["python", "app.py"]
