"""Ekstraksi 32 fitur audio yang konsisten untuk training dan inferensi."""
import shutil
import subprocess
from pathlib import Path

import librosa
import numpy as np

from config import SR, N_MFCC, RMS_HENING, PAKAI_CHUNKING, MIN_CHUNK_DETIK, BAND_HZ, FEATURE_TAMBAHAN


def _ffmpeg_exe():
    """Cari ffmpeg sistem atau ffmpeg yang dibundel imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("FFmpeg tidak ditemukan. Pasang ffmpeg atau imageio-ffmpeg.") from exc


def _decode_ffmpeg(path, sr, max_seconds):
    """Cadangan decoder: mono dari RATA-RATA kanal (L+R)/2, sama dengan librosa.

    `ffmpeg -ac 1` memakai 0,707*(L+R) sehingga file stereo menjadi sekitar 3 dB lebih keras daripada hasil
    librosa (dan CSV). Karena itu audio didecode 2 kanal (mono diduplikasi tanpa perubahan penguatan) lalu dirata-ratakan.
    """
    cmd = [_ffmpeg_exe(), "-v", "error", "-i", str(path)]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-ac", "2", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // 8 * 8
    return np.frombuffer(raw[:n], dtype=np.float32).reshape(-1, 2).mean(axis=1).astype(np.float32)


def load_audio(path, sr=SR, max_seconds=60):
    """Baca audio ke mono float32, resample, dan batasi durasi analisis."""
    path = str(path)
    try:
        y, actual_sr = librosa.load(path, sr=sr, mono=True, duration=max_seconds)
    except Exception:
        # Fallback decoder ffmpeg untuk file MP3 dengan header/metadata tidak standar.
        y = _decode_ffmpeg(path, sr, max_seconds)
        actual_sr = sr
    if y.size == 0:
        raise ValueError("Audio kosong atau formatnya tidak dapat dibaca.")
    return np.asarray(y, dtype=np.float32), sr


def potong_audio(y, sr=SR, chunk_detik=10):
    """Potong audio menjadi chunk (bila config.PAKAI_CHUNKING) atau kembalikan audio utuh sebagai satu sampel.

    Dengan chunking, sisa di akhir dipakai hanya jika >= MIN_CHUNK_DETIK; bila tidak ada yang memenuhi,
    seluruh audio dipakai sebagai satu chunk."""
    if not PAKAI_CHUNKING:
        return [y]
    ukuran = int(sr * chunk_detik)
    minimum = int(sr * min(MIN_CHUNK_DETIK, chunk_detik))
    hasil = [y[i:i+ukuran] for i in range(0, len(y), ukuran) if len(y[i:i+ukuran]) >= minimum]
    return hasil or [y]


def _fitur_tambahan(y, sr=SR):
    """15 fitur tambahan murni numpy (tidak bergantung versi librosa): persentil level suara (dB), roll-off 85%,
    bandwidth, flatness, dan energi rata-rata enam pita frekuensi (dB). Frame hening di tepi dibuang
    dengan ambang RMS_HENING yang sama. Dipakai identik saat training maupun streaming."""
    y = np.asarray(y, dtype=np.float64).flatten()
    n_fft, hop = 2048, 512
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    n = 1 + (y.size - n_fft) // hop
    bingkai = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop][:n]
    rms = np.sqrt((bingkai ** 2).mean(axis=1))
    aktif = np.where(rms >= RMS_HENING)[0]
    if aktif.size and aktif.size < n:
        potong = bingkai[aktif[0]:aktif[-1] + 1]
        if len(potong) >= 1:
            bingkai, rms = potong, rms[aktif[0]:aktif[-1] + 1]
    mag = np.abs(np.fft.rfft(bingkai * np.hanning(n_fft), axis=1))
    daya = mag ** 2 + 1e-12
    freq = np.fft.rfftfreq(n_fft, 1.0 / sr)
    total = mag.sum(axis=1) + 1e-12
    pusat = (mag * freq).sum(axis=1) / total
    rolloff = freq[np.argmax(np.cumsum(mag, axis=1) >= 0.85 * total[:, None], axis=1)]
    bandwidth = np.sqrt((mag * (freq - pusat[:, None]) ** 2).sum(axis=1) / total)
    flatness = np.exp(np.log(daya).mean(axis=1)) / daya.mean(axis=1)
    level = 20 * np.log10(rms + 1e-8)
    hasil = {"level_db_p10": np.percentile(level, 10), "level_db_p50": np.percentile(level, 50),
             "level_db_p90": np.percentile(level, 90)}
    for nama, v in (("rolloff", rolloff), ("bandwidth", bandwidth), ("flatness", flatness)):
        hasil[f"{nama}_mean"], hasil[f"{nama}_std"] = v.mean(), v.std()
    for i, (lo, hi) in enumerate(BAND_HZ, 1):
        m = (freq >= lo) & (freq < hi)
        hasil[f"banda_{i}_db"] = (10 * np.log10(daya[:, m].sum(axis=1) / n_fft ** 2 + 1e-12)).mean()
    return {k: float(hasil[k]) for k in FEATURE_TAMBAHAN}


def features_from_signal(y, sr=SR):
    """Menghasilkan dictionary 47 fitur: 32 fitur dasar (mean/std RMS, centroid, ZCR, 13 MFCC) + 15 fitur tambahan."""
    y = np.asarray(y, dtype=np.float32).flatten()
    if y.size == 0:
        raise ValueError("Sinyal audio kosong.")
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
        sr = SR

    sinyal_asli = y
    # Buang frame hening sesuai ambang yang dipakai saat pembuatan dataset.
    frame = 2048
    hop = 512
    rms_frame = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    aktif = rms_frame >= RMS_HENING
    if aktif.any() and not aktif.all():
        indeks = np.where(aktif)[0]
        awal = max(0, int(indeks[0] * hop))
        akhir = min(len(y), int((indeks[-1] + 1) * hop))
        y_aktif = y[awal:akhir]
        if y_aktif.size >= frame:
            y = y_aktif

    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=frame, hop_length=hop)[0]
    zcr = librosa.feature.zero_crossing_rate(y, frame_length=frame, hop_length=hop)[0]
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC, n_fft=frame, hop_length=hop)
    data = {"rms": rms, "centroid": centroid, "zcr": zcr}
    data.update({f"mfcc_{i+1}": mfcc[i] for i in range(N_MFCC)})
    hasil = {}
    for nama, nilai in data.items():
        hasil[f"{nama}_mean"] = float(np.mean(nilai))
        hasil[f"{nama}_std"] = float(np.std(nilai))
    hasil.update(_fitur_tambahan(sinyal_asli, sr))
    return hasil
