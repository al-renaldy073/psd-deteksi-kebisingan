# Sumber audio Arduino (opsional)

Folder ini berisi panduan dan contoh kode agar perangkat Arduino/ESP32 dengan mikrofon I2S dapat menjadi sumber audio
Sound Lab. Perangkat merekam terus-menerus sendiri, tanpa browser dan tanpa laptop yang membuka halaman admin.
Hanya admin yang dapat memulai dan menghentikannya (tombol di dashboard admin). Hasil tampil di halaman front.

**Status:** sisi server sudah diuji dengan perangkat tiruan. `noise_node/noise_node.ino` belum diuji pada perangkat keras.

## Komponen yang disarankan
| Komponen | Peran | Catatan |
|---|---|---|
| ESP32 DevKit atau **Arduino Nano ESP32** (ESP32-S3) | Mikrokontroler dengan Wi-Fi dan antarmuka I2S | Arduino Uno tidak memiliki I2S dan tidak punya Wi-Fi bawaan, jadi tidak cocok |
| **INMP441** (mikrofon MEMS I2S) | Menangkap suara, keluaran digital 24-bit | SNR 61 dBA, sensitivitas -26 dBFS, respons 60 Hz sampai 15 kHz, 1,8 sampai 3,3 V |
| Kabel jumper, breadboard, kabel USB | Perakitan dan daya | Daya bisa dari USB komputer atau power bank |

Mengapa INMP441 dan bukan modul mikrofon analog dengan AGC (misalnya MAX9814)? Sistem menilai kebisingan relatif terhadap
ruangan tenang. Penguatan otomatis (AGC) mengubah level suara terus-menerus sehingga mengacaukan baseline kalibrasi.
INMP441 memberi level yang tetap dan apa adanya.

## Pengkabelan (ESP32 DevKit, pin bawaan sketch)
| INMP441 | ESP32 |
|---|---|
| VDD | 3V3 |
| GND | GND |
| L/R | GND (kanal kiri) |
| SCK | GPIO14 |
| WS | GPIO15 |
| SD | GPIO32 |

Pada Arduino Nano ESP32, ubah `PIN_SCK`, `PIN_WS`, `PIN_SD` di sketch menjadi nomor GPIO sesuai pinout resmi.

## Menyalakan di server
1. Di `docker-compose.yml` atur `ARDUINO_TOKEN` (rahasia bebas) dan `ADMIN_PASSWORD`, lalu `docker compose up -d --build`.
2. Buka `noise_node/noise_node.ino` di Arduino IDE, pasang dukungan board ESP32 (atau Arduino Nano ESP32), lalu isi
   `WIFI_SSID`, `WIFI_PASS`, `SERVER_HOST` (IP komputer di jaringan yang sama), `SERVER_PORT` (8021), dan `DEVICE_TOKEN`
   (sama dengan `ARDUINO_TOKEN`). Unggah ke board.
3. Masuk sebagai admin, pada kartu **Sumber Arduino** klik **Kalibrasi Arduino** (ruangan harus tenang, perangkat merekam 2 x 10 detik),
   lalu **Mulai (Arduino)**. Buka `/front` di perangkat lain untuk melihat gelombang dan mendengar peringatan.

## Cara kerja dan batasan
* Perangkat bertanya ke `GET /api/arduino/perintah` tiap 2 detik, lalu mengirim PCM int16 mono 16 kHz mentah ke
  `/api/arduino/kalibrasi` atau `/api/arduino/prediksi` dengan header `X-Device-Token`.
* Baseline kalibrasi Arduino disimpan di server (`data/kalibrasi_arduino.json`), terpisah dari kalibrasi browser admin.
* Hanya satu sumber aktif pada satu waktu (browser admin atau Arduino).
* Koneksi memakai HTTP di jaringan lokal. Untuk melewati HTTPS (misalnya lewat proxy), ganti `WiFiClient` dengan `WiFiClientSecure`.
* Gelombang dari Arduino tampil di halaman front setiap selesai satu rekaman 10 detik (bukan real time), sedangkan dari browser admin hampir real time.
* Konstanta `GESER_BIT` mengatur penguatan digital. Nilainya tidak memengaruhi hasil secara besar karena level dinilai relatif terhadap kalibrasi,
  tetapi kalibrasi dan prediksi harus memakai pengaturan yang sama.
