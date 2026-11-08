/*
  Noise Node: perangkat Arduino (ESP32) + mikrofon I2S INMP441 sebagai sumber audio untuk Sound Lab.
  STATUS: contoh kode, BELUM diuji pada perangkat keras. Sesuaikan WiFi, alamat server, token, dan pin.

  Alur: perangkat bertanya ke server tiap 2 detik (GET /api/arduino/perintah).
    - kalibrasi > 0  : rekam 10 detik ruangan tenang, kirim ke /api/arduino/kalibrasi
    - rekam = true   : rekam 10 detik, kirim ke /api/arduino/prediksi, lalu jeda sesuai perintah server (bawaan 2 detik), ulangi
  Hanya admin yang dapat memulai/menghentikan (tombol di dashboard admin). Perangkat hanya mengikuti perintah.

  Audio dikirim sebagai PCM int16 mono 16 kHz mentah (320000 byte untuk 10 detik) langsung dari I2S ke jaringan
  tanpa buffer besar di RAM. Server menghitung fitur dan prediksi dengan model yang sama seperti browser.

  Perlu board dengan I2S: ESP32 DevKit atau Arduino Nano ESP32 (ESP32-S3). Arduino Uno tidak memiliki I2S.
  Koneksi INMP441:  VDD -> 3V3 | GND -> GND | L/R -> GND | SCK -> PIN_SCK | WS -> PIN_WS | SD -> PIN_SD
*/
#include <WiFi.h>
#include <driver/i2s.h>

// ---- pengaturan (ganti sesuai jaringan Anda)
const char* WIFI_SSID    = "NAMA_WIFI";
const char* WIFI_PASS    = "PASSWORD_WIFI";
const char* SERVER_HOST  = "192.168.1.10";          // IP komputer yang menjalankan Docker (satu jaringan Wi-Fi)
const uint16_t SERVER_PORT = 8021;                  // port host di docker-compose.yml
const char* DEVICE_TOKEN = "SAMA_DENGAN_ARDUINO_TOKEN";

// Pin I2S sebagai nomor GPIO. Bawaan untuk ESP32 DevKit. Untuk Arduino Nano ESP32, ganti dengan nomor GPIO sesuai
// pinout resmi (misalnya D2 = GPIO5).
const int PIN_SCK = 14, PIN_WS = 15, PIN_SD = 32;

const i2s_port_t I2S_PORT = I2S_NUM_0;
const int SAMPLE_RATE = 16000;
const int DETIK = 10;
const int GESER_BIT = 14;                           // 32-bit I2S -> int16 dengan penguatan; hasil dijenuhkan (clipping aman)

int32_t blokI2S[512];
int16_t blokPCM[512];

void mulaiI2S() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;   // pin L/R dihubungkan ke GND
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 512;                            // buffer DMA lebih besar mengurangi sampel hilang saat Wi-Fi tersendat
  i2s_pin_config_t pin = {};
  pin.mck_io_num = I2S_PIN_NO_CHANGE;
  pin.bck_io_num = PIN_SCK;
  pin.ws_io_num = PIN_WS;
  pin.data_out_num = I2S_PIN_NO_CHANGE;
  pin.data_in_num = PIN_SD;
  i2s_driver_install(I2S_PORT, &cfg, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin);
}

void sambungWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Menyambung WiFi");
  while (WiFi.status() != WL_CONNECTED) { delay(400); Serial.print("."); }
  Serial.print("\nTerhubung. IP: "); Serial.println(WiFi.localIP());
}

// Baca status HTTP dan isi respons (sederhana). Mengembalikan kode status, isi disimpan di `isi`.
int bacaRespons(WiFiClient& c, String& isi) {
  unsigned long t0 = millis();
  while (c.connected() && !c.available() && millis() - t0 < 20000) delay(5);
  String baris = c.readStringUntil('\n');
  int kode = baris.length() > 9 ? baris.substring(9, 12).toInt() : -1;
  while (c.connected()) { String h = c.readStringUntil('\n'); if (h == "\r" || h.length() == 0) break; }
  isi = "";
  t0 = millis();
  while ((c.connected() || c.available()) && millis() - t0 < 5000) { while (c.available()) isi += (char)c.read(); delay(2); }
  return kode;
}

int kirimAudio(const char* path, String& isi) {
  WiFiClient c;
  if (!c.connect(SERVER_HOST, SERVER_PORT)) { isi = "gagal terhubung"; return -1; }
  const uint32_t total = (uint32_t)SAMPLE_RATE * DETIK;
  c.printf("POST %s HTTP/1.1\r\nHost: %s:%u\r\nX-Device-Token: %s\r\nX-Sample-Rate: %d\r\n", path, SERVER_HOST, SERVER_PORT, DEVICE_TOKEN, SAMPLE_RATE);
  c.printf("Content-Type: application/octet-stream\r\nContent-Length: %u\r\nConnection: close\r\n\r\n", (unsigned)(total * 2));
  i2s_zero_dma_buffer(I2S_PORT);
  uint32_t terkirim = 0; float xPrev = 0, yPrev = 0;          // penapis DC sederhana (INMP441 memiliki offset DC kecil)
  while (terkirim < total) {
    size_t byteBaca = 0;
    i2s_read(I2S_PORT, blokI2S, sizeof(blokI2S), &byteBaca, portMAX_DELAY);
    int n = byteBaca / 4; if (n > (int)(total - terkirim)) n = total - terkirim;
    for (int i = 0; i < n; i++) {
      float x = (float)(blokI2S[i] >> GESER_BIT);
      float y = x - xPrev + 0.995f * yPrev; xPrev = x; yPrev = y;
      if (y > 32767) y = 32767; if (y < -32768) y = -32768;
      blokPCM[i] = (int16_t)y;
    }
    c.write((uint8_t*)blokPCM, n * 2);
    terkirim += n;
  }
  return bacaRespons(c, isi);
}

void setup() {
  Serial.begin(115200);
  mulaiI2S();
  sambungWiFi();
}

void loop() {
  sambungWiFi();
  WiFiClient c;
  String isi;
  if (!c.connect(SERVER_HOST, SERVER_PORT)) { Serial.println("Server tidak terjangkau"); delay(3000); return; }
  c.printf("GET /api/arduino/perintah HTTP/1.1\r\nHost: %s:%u\r\nX-Device-Token: %s\r\nConnection: close\r\n\r\n", SERVER_HOST, SERVER_PORT, DEVICE_TOKEN);
  int kode = bacaRespons(c, isi);
  if (kode != 200) { Serial.printf("Perintah ditolak (%d): %s\n", kode, isi.c_str()); delay(3000); return; }

  int idx = isi.indexOf("\"kalibrasi\":");
  int kalibrasi = idx >= 0 ? isi.substring(idx + 12).toInt() : 0;
  bool rekam = isi.indexOf("\"rekam\":true") >= 0;
  int jdx = isi.indexOf("\"jeda\":");                    // jeda antar rekaman ditentukan server (config.JEDA_DETIK)
  int jeda = jdx >= 0 ? isi.substring(jdx + 7).toInt() : 2;
  if (jeda < 0 || jeda > 60) jeda = 2;

  if (kalibrasi > 0) {
    Serial.println("Kalibrasi: merekam ruangan tenang 10 detik ...");
    kode = kirimAudio("/api/arduino/kalibrasi", isi);
    Serial.printf("Kalibrasi -> %d %s\n", kode, isi.c_str());
  } else if (rekam) {
    Serial.println("Merekam 10 detik ...");
    kode = kirimAudio("/api/arduino/prediksi", isi);
    Serial.printf("Prediksi -> %d %s\n", kode, isi.c_str());
    delay(jeda * 1000UL);                           // jeda antar rekaman (peringatan diputar di halaman front)
  } else {
    delay(2000);
  }
}
