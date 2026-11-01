"""Prediksi dari file audio lewat terminal, dengan kalibrasi dari satu atau lebih file ruangan tenang.

python predict_cli.py rekaman.wav --tenang ruangan_tenang.wav [ruangan_tenang2.wav ...]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from config import SR
from features import load_audio
import inference as inf


def main():
    ap = argparse.ArgumentParser(description="Prediksi kebisingan dari file audio")
    ap.add_argument("audio", help="file audio yang diprediksi (dipakai 10 detik pertama)")
    ap.add_argument("--tenang", nargs="+", required=True, help="satu atau lebih file rekaman ruangan tenang dari mikrofon yang sama")
    a = ap.parse_args()
    bundle = inf.load_bundle()
    ref = []
    for f in a.tenang:
        y, sr = load_audio(f, SR, max_seconds=10)
        ref.append(inf.kalibrasi_klip(y, sr)["referensi"])
    y, sr = load_audio(a.audio, SR, max_seconds=10)
    h = inf.prediksi(y, sr, ref, bundle)
    print(f"Model: {bundle['model_name']}")
    print(f"Hasil: {h['label'].upper()} (peluang berisik {h['peluang_berisik']:.1%}, {h['rel_rms_db']:+.1f} dB dari ruangan tenang)")


if __name__ == "__main__":
    main()
