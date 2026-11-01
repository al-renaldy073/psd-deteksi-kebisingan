"use strict";
/* Sound Lab · Deteksi Kebisingan (frontend).
   Alur audio TIDAK berubah dari versi sebelumnya: rekam 10 detik di browser (tanpa echo cancellation, noise suppression,
   auto gain), kirim PCM float32 mentah ke Flask, server menghitung fitur + baseline + prediksi. */
const $ = id => document.getElementById(id);
const KUNCI = "kalibrasi_kebisingan";
const DETIK = 10, JEDA = 5, MAKS_POTONGAN = 6, MAKS_FILE_MB = 25;
let kirimAbort = null, mic = null, jalan = false, ctxAudio = null, kunciLayar = null, AMBANG = 0.5;
const peringatan = new Audio("/api/peringatan");
const tunggu = ms => new Promise(r => setTimeout(r, ms));
const fmt = (x, d = 1) => Number(x).toLocaleString("id-ID", {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = (x, d = 1) => fmt(100 * x, d) + "%";
const bar = p => { $("bar").style.width = (100 * p) + "%"; };
const nol = n => String(n).padStart(2, "0");
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

// ---------- navigasi (hash: #stream, #prediksi, #eval, #proyek)
const TABS = ["stream", "prediksi", "eval", "proyek"];
function pilihTab(nama) {
  if (!TABS.includes(nama)) nama = "stream";
  document.querySelectorAll(".tab").forEach(t => {
    const on = t.dataset.tab === nama;
    t.classList.toggle("aktif", on);
    if (on) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  });
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("aktif", p.id === nama));
  if (nama === "eval") muatEval();
  if (nama === "prediksi") gambarFile();
  window.scrollTo(0, 0);
}
document.addEventListener("click", e => {
  const a = e.target.closest('a[href^="#"]');
  if (!a) return;
  const nama = a.getAttribute("href").slice(1);
  if (!TABS.includes(nama)) return;
  e.preventDefault();
  if (location.hash !== "#" + nama) history.pushState(null, "", "#" + nama);
  pilihTab(nama);
});
window.addEventListener("popstate", () => pilihTab(location.hash.slice(1)));

// ---------- konteks aman (HTTPS) dan izin mikrofon
function pesanKonteks() {
  if (window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) return null;
  if (!window.isSecureContext)
    return "<b>Mikrofon diblokir karena halaman ini tidak memakai HTTPS.</b> Alamat saat ini: <code>" + esc(location.origin) + "</code>. " +
      "Browser di HP maupun laptop hanya mengizinkan mikrofon pada alamat <code>https://</code> (atau <code>localhost</code> di komputer yang sama). " +
      "Buka aplikasi lewat domain HTTPS (lihat README, bagian “Akses dari HP dan laptop lain (HTTPS)”). Fitur Prediksi file dan Tentang model tetap dapat dipakai.";
  return "<b>Browser ini tidak mendukung akses mikrofon.</b> Gunakan Chrome, Edge, Firefox, atau Safari versi terbaru. " +
    "Bila membuka dari dalam aplikasi lain (WhatsApp, Instagram, dsb.), buka lewat browser bawaan HP.";
}
function cekKonteks() {
  const pesan = pesanKonteks(), p = $("peringatanMic");
  p.hidden = !pesan;
  if (pesan) p.innerHTML = pesan;
  return !pesan;
}
function pesanGalatMic(e) {
  switch (e && e.name) {
    case "NotAllowedError": case "PermissionDeniedError":
      return "Izin mikrofon ditolak. Buka pengaturan situs di browser (ikon gembok atau tanda pengaturan di bilah alamat), ubah Mikrofon menjadi Izinkan, lalu muat ulang halaman.";
    case "NotFoundError": case "DevicesNotFoundError":
      return "Mikrofon tidak ditemukan. Pastikan perangkat memiliki mikrofon dan tidak sedang dinonaktifkan.";
    case "NotReadableError": case "TrackStartError": case "AbortError":
      return "Mikrofon tidak dapat dibuka. Tutup aplikasi lain yang sedang memakai mikrofon (panggilan, perekam suara), lalu coba lagi.";
    case "SecurityError":
      return "Browser menolak akses mikrofon karena alasan keamanan. Pastikan alamat memakai HTTPS.";
    default:
      return "Mikrofon gagal dibuka (" + ((e && e.name) || "galat tidak dikenal") + "). Periksa izin mikrofon di browser.";
  }
}

// ---------- audio context bersama: dibuat pada klik pengguna agar tidak diblokir di iOS/Android
function ambilCtx() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) throw new Error("Browser ini tidak mendukung Web Audio.");
  if (!ctxAudio || ctxAudio.state === "closed") ctxAudio = new AC();
  return ctxAudio;
}
async function siapkanAudio() {
  const ctx = ambilCtx();
  if (ctx.state !== "running") { try { await Promise.race([ctx.resume(), tunggu(1500)]); } catch (e) { /* diperiksa di bawah */ } }
  if (ctx.state !== "running") throw new Error("Audio browser belum aktif. Ketuk tombol sekali lagi (browser HP butuh sentuhan langsung untuk mengaktifkan audio).");
  return ctx;
}

// ---------- mikrofon: tanpa echo cancellation, noise suppression, dan auto gain agar level asli terjaga
function setPillMic(aktif) {
  const p = $("pillMic");
  p.textContent = aktif ? "● Mikrofon aktif" : "Mikrofon belum aktif";
  p.className = "pill" + (aktif ? " live" : "");
}
async function bukaMic() {
  if (!cekKonteks()) throw new Error("Mikrofon tidak tersedia: halaman perlu dibuka lewat HTTPS.");
  if (mic && mic.active) return mic;
  const mentah = {echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: {ideal: 1}};
  try {
    try { mic = await navigator.mediaDevices.getUserMedia({audio: mentah}); }
    catch (e) {
      if (e && e.name === "OverconstrainedError") mic = await navigator.mediaDevices.getUserMedia({audio: true});   // perangkat menolak batasan
      else throw e;
    }
  } catch (e) { throw new Error(pesanGalatMic(e)); }
  const tr = mic.getAudioTracks()[0], s = tr.getSettings ? tr.getSettings() : {};
  const aktif = s.echoCancellation || s.noiseSuppression || s.autoGainControl;
  $("infoMic").textContent = `Mikrofon: ${tr.label || "(tanpa nama)"}. Echo cancellation ${s.echoCancellation ? "AKTIF" : "mati"}, noise suppression ${s.noiseSuppression ? "AKTIF" : "mati"}, auto gain ${s.autoGainControl ? "AKTIF" : "mati"}.` +
    (aktif ? " Fitur yang aktif dapat mengubah level suara; matikan di pengaturan suara perangkat bila hasil kurang tepat." : "");
  tr.addEventListener("ended", () => { mic = null; setPillMic(false); });
  setPillMic(true);
  return mic;
}
function labelMic() { return mic && mic.getAudioTracks().length ? mic.getAudioTracks()[0].label : ""; }
function tutupMic() { if (mic) { mic.getTracks().forEach(t => t.stop()); mic = null; } setPillMic(false); }

async function tahanLayar() {
  try { if (navigator.wakeLock && !kunciLayar) { kunciLayar = await navigator.wakeLock.request("screen"); kunciLayar.addEventListener("release", () => { kunciLayar = null; }); } } catch (e) { /* tidak wajib */ }
}
function lepasLayar() { try { if (kunciLayar) kunciLayar.release(); } catch (e) { /* abaikan */ } kunciLayar = null; }
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && jalan) tahanLayar(); });

// ---------- visualisasi gelombang suara (hanya tampilan; tidak mengubah audio yang direkam dan dikirim)
const Gel = (() => {
  const cv = $("gelombang"), c = cv.getContext("2d"), SLOT = 160;
  const reduce = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  let W = 0, H = 0, an = null, buf = new Float32Array(2048), kecil = null, mode = "garis";
  let puncak = new Float32Array(SLOT), terisi = 0, mulaiMs = 0, durasi = DETIK, aktifData = false;
  let rmsHalus = 0, refPk = 0, tLevel = 0, riak = 0;
  try { const m = localStorage.getItem("gel_mode"); if (m === "batang" || m === "garis") mode = m; } catch (e) { /* abaikan */ }

  // warna mengikuti kerasnya sinyal: cyan (pelan) → kuning → oranye (keras)
  const lerp = (a, b, t) => a + (b - a) * t;
  function warna(t, a) {
    const A = [34, 211, 238], B = [255, 200, 87], C = [255, 106, 77];
    const [x, y] = t < 0.6 ? [A, B] : [B, C], u = t < 0.6 ? t / 0.6 : (t - 0.6) / 0.4;
    return `rgba(${Math.round(lerp(x[0], y[0], u))},${Math.round(lerp(x[1], y[1], u))},${Math.round(lerp(x[2], y[2], u))},${a})`;
  }
  function ukur() {
    const r = cv.getBoundingClientRect(); W = r.width; H = r.height;
    if (!W || !H) return;
    const d = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.round(W * d); cv.height = Math.round(H * d);
    c.setTransform(d, 0, 0, d, 0, 0);
  }
  if (window.ResizeObserver) new ResizeObserver(ukur).observe(cv); else window.addEventListener("resize", ukur);
  ukur();

  function bacaData() {
    if (an.getFloatTimeDomainData) an.getFloatTimeDomainData(buf);
    else { if (!kecil || kecil.length !== buf.length) kecil = new Uint8Array(buf.length); an.getByteTimeDomainData(kecil); for (let i = 0; i < kecil.length; i++) buf[i] = (kecil[i] - 128) / 128; }
    let s = 0, pk = 0;
    for (let i = 0; i < buf.length; i++) { const v = buf[i], a = v < 0 ? -v : v; s += v * v; if (a > pk) pk = a; }
    return {rms: Math.sqrt(s / buf.length), pk};
  }
  function kotak(x, y, w, h, r) { c.beginPath(); if (c.roundRect) c.roundRect(x, y, w, h, r); else c.rect(x, y, w, h); c.fill(); }
  function petak() {
    c.lineWidth = 1; c.strokeStyle = "rgba(148,163,184,.08)"; c.beginPath();
    for (let i = 1; i < 10; i++) { const x = Math.round(W * i / 10) + .5; c.moveTo(x, 0); c.lineTo(x, H); }
    for (const f of [.25, .75]) { const y = Math.round(H * f) + .5; c.moveTo(0, y); c.lineTo(W, y); }
    c.stroke();
    c.strokeStyle = "rgba(148,163,184,.2)"; c.beginPath(); c.moveTo(0, Math.round(H / 2) + .5); c.lineTo(W, Math.round(H / 2) + .5); c.stroke();
  }
  function garis() {
    const mid = H / 2, n = Math.max(48, Math.floor(W / 3)), maksY = H * 0.36, t = Math.min(1, rmsHalus / 0.2);
    const gain = maksY / Math.max(refPk, 0.08), ys = new Array(n);
    if (an && aktifData) {
      const len = buf.length >> 1; let st = 0;
      for (let i = 1; i < len; i++) if (buf[i - 1] < 0 && buf[i] >= 0) { st = i; break; }   // sinkron di titik nol agar gelombang stabil
      for (let k = 0; k < n; k++) ys[k] = Math.max(-maksY * 1.2, Math.min(maksY * 1.2, buf[st + Math.floor(k / (n - 1) * (len - 1))] * gain));
    } else {
      if (!reduce) riak += 0.025;
      for (let k = 0; k < n; k++) ys[k] = reduce ? 0 : Math.sin(k / n * 18.85 + riak) * H * 0.014 * Math.sin(k / n * Math.PI);
    }
    const dx = W / (n - 1), jalur = new Path2D();
    jalur.moveTo(0, mid - ys[0]);
    for (let k = 1; k < n - 1; k++) { const x = k * dx, y = mid - ys[k]; jalur.quadraticCurveTo(x, y, (x + (k + 1) * dx) / 2, (y + mid - ys[k + 1]) / 2); }
    jalur.lineTo(W, mid - ys[n - 1]);
    const isi = new Path2D(jalur); isi.lineTo(W, mid); isi.lineTo(0, mid); isi.closePath();
    const gi = c.createLinearGradient(0, mid - maksY, 0, mid + maksY);
    gi.addColorStop(0, warna(t, .24)); gi.addColorStop(.5, warna(t, .02)); gi.addColorStop(1, warna(t, .24));
    c.fillStyle = gi; c.fill(isi);
    const a = aktifData ? 1 : .5, g = c.createLinearGradient(0, 0, W, 0);
    g.addColorStop(0, warna(t, 0)); g.addColorStop(.1, warna(t, a)); g.addColorStop(.9, warna(t, a)); g.addColorStop(1, warna(t, 0));
    c.lineJoin = "round"; c.lineCap = "round"; c.strokeStyle = g; c.lineWidth = 2.2;
    c.shadowColor = warna(t, .9); c.shadowBlur = aktifData ? 16 : 0; c.stroke(jalur); c.shadowBlur = 0;
  }
  function batang() {
    const sw = W / SLOT, bw = Math.max(2, sw * 0.62), mid = H / 2, maksH = H * 0.72;
    let ref = 0.08; for (let i = 0; i < terisi; i++) if (puncak[i] > ref) ref = puncak[i];
    for (let i = 0; i < SLOT; i++) {
      const x = i * sw + (sw - bw) / 2;
      if (i < terisi) { const p = puncak[i], h = Math.max(3, Math.min(1, p / ref) * maksH); c.fillStyle = warna(Math.min(1, p / 0.5), .95); kotak(x, mid - h / 2, bw, h, bw / 2); }
      else { c.fillStyle = "rgba(148,163,184,.16)"; kotak(x, mid - 1.5, bw, 3, 1.5); }
    }
    if (aktifData && terisi < SLOT) { c.fillStyle = "rgba(255,255,255,.55)"; c.fillRect(Math.round(terisi * sw) - 1, H * 0.1, 2, H * 0.8); }
  }
  function level() {
    const db = rmsHalus > 1e-5 ? 20 * Math.log10(rmsHalus) : -100;
    $("levelTeks").textContent = !aktifData ? "—" : db <= -90 ? "−∞ dBFS" : fmt(db, 0).replace("-", "−") + " dBFS";
    $("levelBar").style.width = aktifData ? Math.max(0, Math.min(100, (db + 70) / 70 * 100)) + "%" : "0%";
  }
  function gambar(now) {
    requestAnimationFrame(gambar);
    if (!W || !H) return;                                    // panel sedang tersembunyi
    let pk = 0, rmsNow = 0;
    if (an && aktifData) { const d = bacaData(); pk = d.pk; rmsNow = d.rms; }
    rmsHalus += (rmsNow - rmsHalus) * 0.2;
    refPk = Math.max(pk, refPk * 0.965);
    if (aktifData) {
      const idx = Math.min(SLOT - 1, Math.floor((performance.now() - mulaiMs) / 1000 / durasi * SLOT));
      for (let k = Math.max(0, terisi); k <= idx; k++) puncak[k] = Math.max(puncak[k], pk);
      terisi = Math.max(terisi, idx + 1);
    }
    if (now - tLevel > 80) { tLevel = now; level(); }
    c.clearRect(0, 0, W, H);
    petak();
    if (mode === "batang") batang(); else garis();
  }
  requestAnimationFrame(gambar);

  function kosong() { $("scopeKosong").hidden = jalan || aktifData || (mode === "batang" && terisi > 0); }
  function setMode(m) {
    mode = m; try { localStorage.setItem("gel_mode", m); } catch (e) { /* abaikan */ }
    document.querySelectorAll(".seg button").forEach(b => { const on = b.dataset.mode === m; b.classList.toggle("aktif", on); b.setAttribute("aria-pressed", on); });
    kosong();
  }
  document.querySelectorAll(".seg button").forEach(b => { b.onclick = () => setMode(b.dataset.mode); });
  setMode(mode);
  return {
    mulai(ctx, src, detik) {
      try { an = ctx.createAnalyser(); an.fftSize = 2048; an.smoothingTimeConstant = 0; buf = new Float32Array(an.fftSize); src.connect(an); }
      catch (e) { an = null; }
      aktifData = !!an; durasi = detik; mulaiMs = performance.now(); puncak = new Float32Array(SLOT); terisi = 0; refPk = 0; kosong();
    },
    selesai() { aktifData = false; if (an) { try { an.disconnect(); } catch (e) { /* abaikan */ } } an = null; kosong(); },
    kosong, ukur
  };
})();

// ---------- tahapan siklus dan penunjuk waktu di layar gelombang
const FASE = {siaga: "Siaga", kalibrasi: "Kalibrasi", rekam: "Merekam", proses: "Memproses", jeda: "Jeda"};
const URUT = ["kalibrasi", "rekam", "proses", "jeda"];
let sedangKal = false;
function setFase(f) {
  const h = $("hudFase"), idx = URUT.indexOf(f);
  h.dataset.fase = f; h.textContent = FASE[f] || f;
  document.querySelectorAll("#fase li").forEach(li => {
    const i = URUT.indexOf(li.dataset.fase);
    li.classList.toggle("aktif", i === idx);
    li.classList.toggle("selesai", idx > -1 && i < idx);
    if (li.dataset.fase === "kalibrasi") li.hidden = !sedangKal;
  });
}
function hudWaktu(d) { $("hudWaktu").textContent = `${fmt(d, 1)} / ${DETIK} dtk`; }
const tick = p => { bar(p); hudWaktu(p * DETIK); };
function aturStatus(teks, aktif) { $("status").textContent = teks; $("statusDot").classList.toggle("aktif", !!aktif); }

// Rekam `detik` detik; mengembalikan {pcm, sr, lengkap}. Pemrosesan audio berhenti begitu selesai.
async function rekam(detik, onTick, aktif) {
  const stream = await bukaMic();
  let ctx = await siapkanAudio();
  let src;
  try { src = ctx.createMediaStreamSource(stream); }
  catch (e) {   // Firefox: sample rate mikrofon harus sama dengan AudioContext
    const rate = stream.getAudioTracks()[0].getSettings().sampleRate;
    try { await ctx.close(); } catch (e2) { /* abaikan */ }
    ctxAudio = null;
    const AC = window.AudioContext || window.webkitAudioContext;
    ctxAudio = ctx = rate ? new AC({sampleRate: rate}) : new AC();
    await siapkanAudio();
    src = ctx.createMediaStreamSource(stream);
  }
  const sr = ctx.sampleRate, target = Math.round(detik * sr), buf = new Float32Array(target);
  let n = 0, terakhir = Date.now();
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = e => {
    const d = e.inputBuffer.getChannelData(0), m = Math.min(d.length, target - n);
    if (m > 0) { buf.set(d.subarray(0, m), n); n += m; }
    terakhir = Date.now();
  };
  src.connect(proc); proc.connect(ctx.destination);
  Gel.mulai(ctx, src, detik);                  // hanya tampilan: analyser membaca sinyal, tidak mengubah audio yang direkam
  let macet = false;
  while (n < target && aktif()) {
    if (Date.now() - terakhir > 4000) { macet = true; break; }
    onTick && onTick(n / target);
    await tunggu(100);
  }
  proc.onaudioprocess = null; proc.disconnect(); src.disconnect();
  Gel.selesai();
  if (macet) throw new Error("Mikrofon tidak mengirim suara. Pastikan izin mikrofon aktif dan layar tidak terkunci, lalu coba lagi.");
  return {pcm: buf, sr, lengkap: n >= target};
}
function kirim(url, rek, tambahan) {
  const f = new FormData();
  f.append("audio", new Blob([rek.pcm.buffer], {type: "application/octet-stream"}), "a.f32");
  f.append("sr", rek.sr);
  Object.entries(tambahan || {}).forEach(([k, v]) => f.append(k, v));
  const ac = new AbortController(), batas = setTimeout(() => ac.abort(), 90000);
  kirimAbort = ac;
  return fetch(url, {method: "POST", body: f, signal: ac.signal}).then(async r => {
    let j = {}; try { j = await r.json(); } catch (e) { /* bukan JSON */ }
    if (!r.ok) throw new Error(j.galat || "Galat server (" + r.status + ")");
    return j;
  }, e => { throw new Error(e.name === "AbortError" ? (jalan ? "Server terlalu lama menjawab." : "Dibatalkan.") : "Tidak dapat terhubung ke server. Periksa koneksi internet."); })
    .finally(() => { clearTimeout(batas); if (kirimAbort === ac) kirimAbort = null; });
}
// ---------- kalibrasi ruangan tenang (disimpan per browser/perangkat)
function kalibrasiTersimpan() {
  try { const k = JSON.parse(localStorage.getItem(KUNCI)); return k && Array.isArray(k.klip) && k.klip.length ? k : null; } catch (e) { return null; }
}
function simpanKalibrasi(k) { try { localStorage.setItem(KUNCI, JSON.stringify(k)); } catch (e) { /* mode privat: kalibrasi hanya berlaku selama halaman terbuka */ } memKal = k; }
let memKal = null;
function ambilKalibrasi() { return kalibrasiTersimpan() || memKal; }
function tampilKal() {
  const k = ambilKalibrasi();
  const rms = k ? fmt(k.klip.reduce((a, r) => a + r.rms_mean, 0) / k.klip.length, 4) : "";
  $("infoKal").textContent = k ? `Kalibrasi tersimpan di perangkat ini (${k.klip.length} klip, RMS ruangan ${rms}, ${k.waktu}).` : "Belum ada kalibrasi; akan direkam saat Mulai ditekan.";
  const fk = $("fKal"), strip = fk.closest(".info-strip");
  fk.textContent = k ? `Memakai baseline ruangan tenang dari kalibrasi di perangkat ini (${k.klip.length} klip, ${k.waktu}).`
                     : "Belum ada kalibrasi di perangkat ini. Lakukan kalibrasi sekali di tab Streaming (2 × 10 detik di ruangan tenang), lalu kembali ke sini.";
  strip.classList.toggle("warn", !k);
  $("linkKal").hidden = !!k;
}
async function lakukanKalibrasi(n) {
  const klip = [], pesan = [];
  for (let i = 1; i <= n; i++) {
    for (let s = 3; s >= 1; s--) { if (!jalan) return null; setFase("kalibrasi"); aturStatus(`Kalibrasi ${i}/${n}: mulai dalam ${s} detik. Jaga ruangan tenang!`, true); await tunggu(1000); }
    aturStatus(`Kalibrasi ${i}/${n}: merekam ruangan tenang ...`, true);
    const r = await rekam(DETIK, tick, () => jalan);
    if (!r.lengkap) return null;
    setFase("proses"); aturStatus(`Kalibrasi ${i}/${n}: memproses ...`, true);
    const h = await kirim("/api/kalibrasi", r);
    klip.push(h.referensi); if (h.peringatan) pesan.push(h.peringatan);
  }
  bar(0); hudWaktu(0);
  const k = {klip, waktu: new Date().toLocaleString("id-ID"), mic: labelMic()};
  simpanKalibrasi(k);
  if (pesan.length) aturStatus(pesan[0], false);
  tampilKal();
  return k;
}
function aturTombol(sibuk) {
  $("btnMulai").disabled = sibuk; $("btnKal").disabled = sibuk; $("btnStop").disabled = !sibuk;
}

// ---------- suara peringatan
async function bukaKunciAudio() { peringatan.muted = true; try { await peringatan.play(); peringatan.pause(); peringatan.currentTime = 0; } catch (e) { /* diabaikan */ } peringatan.muted = false; }
let selesaiPutar = null;
function putarPeringatan() {
  return new Promise(res => {
    const t = setTimeout(() => akhir(), 15000);   // pengaman: bila audio macet, alur tetap lanjut
    const akhir = () => { clearTimeout(t); selesaiPutar = null; peringatan.onended = peringatan.onerror = null; res(); };
    selesaiPutar = akhir; peringatan.currentTime = 0; peringatan.onended = akhir; peringatan.onerror = akhir;
    peringatan.play().catch(akhir);
  });
}
function hentikanPeringatan() { try { peringatan.pause(); } catch (e) { /* abaikan */ } if (selesaiPutar) selesaiPutar(); }
$("btnSuara").onclick = async () => { await bukaKunciAudio(); await putarPeringatan(); };

// ---------- streaming: [kalibrasi bila perlu] lalu rekam 10 detik, proses, peringatan bila berisik, jeda 5 detik, ulang
async function streaming(paksaKalibrasi) {
  jalan = true; aturTombol(true); $("hasil").className = "hasil"; Gel.kosong();
  const siap = siapkanAudio();                 // dibuat pada klik pengguna (wajib untuk iOS/Android)
  siap.catch(() => {});                        // galatnya ditangkap di bawah lewat await siap
  try {
    await bukaKunciAudio();                    // masih dalam aksi klik: izinkan suara peringatan diputar nanti
    await siap;
    await bukaMic();                           // minta izin mikrofon langsung saat klik
    tahanLayar();
    let kal = ambilKalibrasi();
    const labelBeda = kal && kal.mic && labelMic() && kal.mic !== labelMic();
    if (paksaKalibrasi || !kal || labelBeda) { sedangKal = true; setFase("kalibrasi"); kal = await lakukanKalibrasi(2); sedangKal = false; }
    if (!kal) throw new Error("Kalibrasi dibatalkan.");
    if (paksaKalibrasi === "saja") { aturStatus("Kalibrasi selesai.", false); return; }
    let siklus = 0;
    while (jalan) {
      siklus++; setFase("rekam"); aturStatus(`Siklus ${siklus}: merekam ${DETIK} detik ...`, true);
      const r = await rekam(DETIK, tick, () => jalan);
      if (!jalan || !r.lengkap) break;
      bar(0); setFase("proses"); $("hudWaktu").textContent = "Memproses ..."; aturStatus(`Siklus ${siklus}: memproses ...`, true);
      const h = await kirim("/api/prediksi", r, {baseline: JSON.stringify(kal.klip)});
      tampilHasil(h);
      if (h.label === "berisik") { aturStatus(`Siklus ${siklus}: memutar peringatan`, true); await putarPeringatan(); await tunggu(300); }
      setFase("jeda");
      for (let s = JEDA; s >= 1 && jalan; s--) { aturStatus(`Jeda ${s} detik sebelum rekaman berikutnya`, true); $("hudWaktu").textContent = `Berikutnya ${s} dtk`; bar((JEDA - s) / JEDA); await tunggu(1000); }
      bar(0);
    }
    aturStatus("Dihentikan", false);
  } catch (e) { aturStatus(jalan ? "Berhenti: " + e.message : "Dihentikan", false); }
  finally {
    jalan = false; sedangKal = false; setFase("siaga"); hudWaktu(0);
    tutupMic(); lepasLayar(); bar(0); aturTombol(false); tampilKal(); Gel.selesai(); Gel.kosong();
  }
}
$("btnMulai").onclick = () => streaming(false);
$("btnKal").onclick = () => streaming("saja");
$("btnStop").onclick = () => {
  jalan = false; aturStatus("Menghentikan ...", true);
  hentikanPeringatan();                          // suara peringatan yang sedang diputar langsung dihentikan
  if (kirimAbort) kirimAbort.abort();            // permintaan ke server yang sedang menunggu dibatalkan
};

function barisHasil(tb, kolom, akhir) {
  const tr = document.createElement("tr");
  kolom.forEach(t => {
    const td = document.createElement("td");
    if (t && typeof t === "object") { const s = document.createElement("span"); s.className = "tg " + t.kelas; s.textContent = t.teks; td.appendChild(s); }
    else td.textContent = t;
    tr.appendChild(td);
  });
  if (akhir) tb.appendChild(tr); else tb.prepend(tr);
  return tr;
}
const tagHasil = ber => ({kelas: ber ? "n" : "q", teks: ber ? "BERISIK" : "tidak berisik"});
const dB = x => (x >= 0 ? "+" : "") + fmt(x) + " dB";

// kartu hasil bersama (streaming dan file): judul, angka peluang, dan meter dengan penanda ambang
function renderHasil(el, ber, peluang, sub) {
  const p = Math.max(0, Math.min(1, peluang));
  el.className = "hasil " + (ber ? "berisik" : "tenang");
  el.innerHTML = `<div class="hasil-atas"><span class="hasil-titik"></span><div><b class="hasil-judul">${ber ? "Berisik" : "Tidak berisik"}</b><span class="hasil-sub">${esc(sub)}</span></div><span class="hasil-angka">${pct(p)}</span></div>` +
    `<div class="meter" role="img" aria-label="Peluang berisik ${pct(p)}, ambang ${pct(AMBANG, 0)}"><span class="meter-isi" style="width:${(p * 100).toFixed(1)}%"></span><span class="meter-ambang" style="left:${(AMBANG * 100).toFixed(1)}%"></span></div>` +
    `<div class="meter-ket"><span>Peluang berisik</span><span>Ambang ${pct(AMBANG, 0)}</span></div>`;
  el.classList.remove("baru"); void el.offsetWidth; el.classList.add("baru");
}
let cacahBer = 0, cacahTenang = 0;
function tampilHasil(h) {
  const ber = h.label === "berisik", waktu = new Date().toLocaleTimeString("id-ID");
  renderHasil($("hasil"), ber, h.peluang_berisik, ber ? "Jangan rame!" : "Kondisi ruangan terkendali");
  const tb = document.querySelector("#riwayat tbody");
  barisHasil(tb, [waktu, tagHasil(ber), pct(h.peluang_berisik), dB(h.rel_rms_db), fmt(h.rms, 4)]);
  while (tb.rows.length > 30) tb.deleteRow(-1);
  $("riwayatKosong").hidden = true;
  const st = $("riwayatStrip"), i = document.createElement("i");
  if (ber) i.className = "n";
  i.title = `${waktu} · ${ber ? "berisik" : "tidak berisik"} ${pct(h.peluang_berisik)}`;
  st.appendChild(i);
  while (st.children.length > 24) st.removeChild(st.firstChild);
  if (ber) cacahBer++; else cacahTenang++;
  $("ringkasRiwayat").textContent = `${cacahBer + cacahTenang} deteksi · ${cacahBer} berisik · ${cacahTenang} tenang`;
}

// ---------- prediksi dari file (baseline = kalibrasi tersimpan; tidak perlu unggah file ruangan tenang)
async function bacaFile(file) {
  const ctx = ambilCtx();
  const data = await file.arrayBuffer();
  let ab;
  try { ab = await new Promise((ok, gagal) => { const p = ctx.decodeAudioData(data, ok, gagal); if (p && p.catch) p.catch(gagal); }); }
  catch (e) { throw new Error("Format audio tidak dapat dibaca oleh browser ini. Coba ubah file ke MP3 atau WAV."); }
  const mono = new Float32Array(ab.length);
  for (let c = 0; c < ab.numberOfChannels; c++) { const d = ab.getChannelData(c); for (let i = 0; i < ab.length; i++) mono[i] += d[i] / ab.numberOfChannels; }
  return {pcm: mono, sr: ab.sampleRate};
}
function potong(a, mulai, detik) { const awal = Math.round(mulai * a.sr), n = Math.round(detik * a.sr); return {pcm: a.pcm.slice(awal, awal + n), sr: a.sr, lengkap: true}; }
const KB = b => b >= 1048576 ? fmt(b / 1048576, 1) + " MB" : fmt(b / 1024, 0) + " KB";
const jumlahPotongan = total => { let n = 0; while (n < MAKS_POTONGAN && n * DETIK + 3 <= total) n++; return n; };
let fCache = null, fSeq = 0, fHasilSeg = [];

// ringkasan gelombang file (min/maks per ruas) agar penggambaran ulang cepat
function envelop(a) {
  const bins = 1200, n = a.pcm.length, mn = new Float32Array(bins), mx = new Float32Array(bins), langkah = n / bins;
  let puncak = 0.01;
  for (let b = 0; b < bins; b++) {
    let lo = 1, hi = -1;
    const s = Math.floor(b * langkah), e = Math.min(n, Math.floor((b + 1) * langkah)), st = Math.max(1, Math.floor((e - s) / 64));
    for (let i = s; i < e; i += st) { const v = a.pcm[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
    if (lo > hi) { lo = 0; hi = 0; }
    mn[b] = lo; mx[b] = hi; puncak = Math.max(puncak, -lo, hi);
  }
  return {mn, mx, bins, puncak};
}
function gambarFile() {
  if (!fCache || $("fWaveBox").hidden) return;
  const cv = $("fGelombang"), r = cv.getBoundingClientRect();
  if (!r.width || !r.height) return;
  const d = Math.min(window.devicePixelRatio || 1, 2), W = r.width, H = r.height;
  if (cv.width !== Math.round(W * d) || cv.height !== Math.round(H * d)) { cv.width = Math.round(W * d); cv.height = Math.round(H * d); }
  const c = cv.getContext("2d"), {env, total} = fCache, y0 = 24, y1 = H - 18, cy = (y0 + y1) / 2, amp = (y1 - y0) / 2 * 0.92;
  c.setTransform(d, 0, 0, d, 0, 0); c.clearRect(0, 0, W, H);
  const WARNA = {ber: "255,106,77", ten: "45,212,167", biasa: "76,160,255"};
  const segDi = t => fHasilSeg.find(s => s.i === Math.floor(t / DETIK));
  // blok hasil per potongan
  fHasilSeg.forEach(s => {
    const x0 = s.i * DETIK / total * W, x1 = Math.min((s.i + 1) * DETIK, total) / total * W;
    c.fillStyle = `rgba(${s.ber ? WARNA.ber : WARNA.ten},.1)`; c.fillRect(x0, 0, x1 - x0, H);
    c.fillStyle = `rgb(${s.ber ? WARNA.ber : WARNA.ten})`; c.font = "600 11px system-ui, sans-serif"; c.textBaseline = "middle";
    c.fillText(x1 - x0 > 96 ? `${s.i + 1} · ${s.ber ? "Berisik" : "Tenang"} ${pct(s.peluang, 0)}` : String(s.i + 1), x0 + 8, 12);
  });
  // gelombang
  c.strokeStyle = "rgba(148,163,184,.18)"; c.lineWidth = 1; c.beginPath(); c.moveTo(0, Math.round(cy) + .5); c.lineTo(W, Math.round(cy) + .5); c.stroke();
  for (let x = 0; x < W; x += 3) {
    const b = Math.min(env.bins - 1, Math.floor(x / W * env.bins)), s = segDi(x / W * total);
    const top = cy - env.mx[b] / env.puncak * amp, bot = cy - env.mn[b] / env.puncak * amp;
    c.fillStyle = `rgba(${s ? (s.ber ? WARNA.ber : WARNA.ten) : WARNA.biasa},.92)`;
    c.fillRect(x, top, 2, Math.max(2, bot - top));
  }
  // bagian yang tidak dianalisis (lewat 6 potongan)
  const batas = Math.min(total, MAKS_POTONGAN * DETIK);
  if (total > batas + 0.01) {
    const xb = batas / total * W;
    c.fillStyle = "rgba(5,9,20,.62)"; c.fillRect(xb, 0, W - xb, H);
    c.fillStyle = "rgba(154,165,192,.9)"; c.font = "600 11px system-ui, sans-serif"; c.textAlign = "right"; c.fillText("tidak dianalisis", W - 8, 12); c.textAlign = "left";
  }
  // batas potongan dan skala waktu
  c.setLineDash([4, 4]); c.strokeStyle = "rgba(232,236,246,.28)"; c.fillStyle = "rgba(154,165,192,.9)"; c.font = "500 10px system-ui, sans-serif";
  for (let t = DETIK; t < total; t += DETIK) {
    const x = Math.round(t / total * W) + .5;
    c.beginPath(); c.moveTo(x, 20); c.lineTo(x, H - 16); c.stroke();
    c.fillText(`${t} dtk`, x + 4, H - 6);
  }
  c.setLineDash([]); c.fillText("0", 4, H - 6);
}
if (window.ResizeObserver) new ResizeObserver(gambarFile).observe($("fGelombang"));

async function muatFile(f) {
  const seq = ++fSeq;
  fCache = null; fHasilSeg = [];
  document.querySelector("#fTabel tbody").innerHTML = ""; $("fKosong").hidden = false; $("fHasil").className = "hasil";
  $("dropzone").classList.add("terisi"); $("fNama").textContent = f.name; $("fInfo").textContent = KB(f.size);
  if (f.size > MAKS_FILE_MB * 1024 * 1024) { $("fWaveBox").hidden = true; $("fStatus").textContent = `File terlalu besar (maksimal ${MAKS_FILE_MB} MB).`; return; }
  $("fStatus").textContent = "Membaca file audio ...";
  try {
    const a = await bacaFile(f);
    if (seq !== fSeq) return;
    const total = a.pcm.length / a.sr;
    fCache = {file: f, audio: a, total, env: envelop(a)};
    $("fInfo").textContent = `${KB(f.size)} · ${fmt(total, 1)} detik · ${jumlahPotongan(total)} potongan akan dianalisis`;
    $("fWaveBox").hidden = false; gambarFile();
    $("fStatus").textContent = "File siap dianalisis.";
  } catch (e) {
    if (seq !== fSeq) return;
    $("fWaveBox").hidden = true; $("fStatus").textContent = "Gagal membaca file: " + e.message;
  }
}
const zona = $("dropzone");
["dragenter", "dragover"].forEach(ev => zona.addEventListener(ev, e => { e.preventDefault(); zona.classList.add("seret"); }));
["dragleave", "drop"].forEach(ev => zona.addEventListener(ev, e => { e.preventDefault(); zona.classList.remove("seret"); }));
zona.addEventListener("drop", e => {
  const f = e.dataTransfer && e.dataTransfer.files[0];
  if (!f) return;
  try { const dt = new DataTransfer(); dt.items.add(f); $("fAudio").files = dt.files; } catch (err) { /* browser lama: analisis memakai berkas dari cache */ }
  muatFile(f);
});
$("fAudio").onchange = () => { const f = $("fAudio").files[0]; if (f) muatFile(f); };

$("btnFile").onclick = async () => {
  const tb = document.querySelector("#fTabel tbody");
  const kunci = bukaKunciAudio();                // masih dalam aksi klik: izinkan suara peringatan diputar setelah analisis
  if (!fCache) { $("fStatus").textContent = "Pilih file audio dulu."; return; }
  const k = ambilKalibrasi();
  if (!k) { $("fStatus").textContent = "Belum ada kalibrasi di perangkat ini. Lakukan kalibrasi di tab Streaming dulu."; tampilKal(); return; }
  $("btnFile").disabled = true; tb.innerHTML = ""; $("fHasil").className = "hasil"; fHasilSeg = []; gambarFile();
  try {
    const a = fCache.audio, total = fCache.total, p = [];
    for (let i = 0; i < MAKS_POTONGAN && i * DETIK + 3 <= total; i++) {
      $("fStatus").textContent = `Memproses potongan ${i + 1} ...`;
      const h = await kirim("/api/prediksi", potong(a, i * DETIK, Math.min(DETIK, total - i * DETIK)), {baseline: JSON.stringify(k.klip)});
      p.push(h);
      fHasilSeg.push({i, ber: h.label === "berisik", peluang: h.peluang_berisik}); gambarFile();
      barisHasil(tb, [`${i * DETIK} sampai ${Math.round(Math.min((i + 1) * DETIK, total))} detik`, tagHasil(h.label === "berisik"), pct(h.peluang_berisik), dB(h.rel_rms_db)], true);
      $("fKosong").hidden = true;
    }
    if (!p.length) throw new Error("Audio terlalu pendek (minimal 3 detik).");
    const rata = p.reduce((s, h) => s + h.peluang_berisik, 0) / p.length, ber = rata >= 0.5;
    renderHasil($("fHasil"), ber, rata, `Rata-rata dari ${p.length} potongan 10 detik`);
    $("fStatus").textContent = "Selesai.";
    if (ber) { $("fStatus").textContent = "Selesai. Memutar peringatan ..."; await kunci; await putarPeringatan(); $("fStatus").textContent = "Selesai."; }
  } catch (e) { $("fStatus").textContent = "Gagal: " + e.message; }
  $("btnFile").disabled = false;
};

// ---------- evaluasi model (semua angka dibaca dari reports/*.json|csv dan model tersimpan lewat /api/info)
let evalSudah = false, evalSedang = false;
const KOLOM_ALGO = [["Algoritma", "Algoritma"], ["Kombinasi fitur", "Kombinasi fitur"], ["Fitur", "Fitur (k/jumlah)"], ["CV Akurasi (latih)", "CV akurasi (latih)"],
                    ["Accuracy", "Accuracy"], ["Precision", "Precision"], ["Recall", "Recall"], ["F1-Score", "F1-Score"]];
const NUM3 = ["CV Akurasi (latih)", "Accuracy", "Precision", "Recall", "F1-Score"];

function kelompokFitur(nama) {
  if (nama.startsWith("rel_") || nama === "rms_variasi") return "rel";
  if (nama.startsWith("mfcc_")) return "mfcc";
  return "spek";
}
function kvList(baris) { return "<dl class=\"kv\">" + baris.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("") + "</dl>"; }

function renderMetrik(t, nUji) {
  const m = [["Accuracy", t.accuracy, "Proporsi prediksi benar"], ["Precision", t.precision, "Ketepatan kelas berisik"],
             ["Recall", t.recall, "Berisik yang berhasil terdeteksi"], ["F1-score", t.f1, "Harmonik precision dan recall"]];
  $("evalMetrik").innerHTML = m.map(([n, v, ket]) => `<div class="metric"><span>${n}</span><b>${pct(v)}</b><small>${ket}</small></div>`).join("");
  return nUji;
}
function renderCm(cm) {
  if (!cm) { $("evalCm").innerHTML = "<p class=\"muted\">Confusion matrix tidak tersedia.</p>"; return; }
  const [[tp, fn], [fp, tn]] = cm, maks = Math.max(tp, fn, fp, tn, 1);
  const sel = (v, nama, ok) => `<div class="sel ${ok ? "benar" : "salah"}" style="--a:${(v / maks).toFixed(2)}"><b>${v}</b><small>${nama}</small></div>`;
  $("evalCm").innerHTML = `<div class="cm">
    <div></div><div class="h">Prediksi<br>berisik</div><div class="h">Prediksi<br>tidak berisik</div>
    <div class="h baris">Aktual<br>berisik</div>${sel(tp, "benar (TP)", true)}${sel(fn, "terlewat (FN)", false)}
    <div class="h baris">Aktual<br>tidak berisik</div>${sel(fp, "salah alarm (FP)", false)}${sel(tn, "benar (TN)", true)}
  </div><p class="cm-note">Total ${tp + fn + fp + tn} file uji dari perekam yang tidak ada di data latih. Kelas positif: berisik.</p>`;
}
function renderFitur(j) {
  const grup = {rel: [], spek: [], mfcc: []};
  j.fitur_terpilih.forEach(f => grup[kelompokFitur(f)].push(f));
  const judul = {rel: "Fitur relatif terhadap ruangan tenang", spek: "Fitur spektral", mfcc: "Fitur MFCC"};
  $("evalFiturInfo").textContent = `${j.fitur_terpilih.length} dari ${j.n_fitur_kombinasi || "?"} fitur pada kombinasi “${j.kombinasi}” lolos seleksi SelectKBest (ANOVA-F).`;
  $("evalFitur").innerHTML = ["rel", "spek", "mfcc"].filter(g => grup[g].length).map(g =>
    `<div class="chips-group"><h4>${judul[g]} (${grup[g].length})</h4><div class="chips">${grup[g].map(f => `<span class="chip ${g}">${esc(f)}</span>`).join("")}</div></div>`).join("");
}
function barisTabel(r, kolom, terpilih) {
  return `<tr${terpilih ? " class=\"terpilih\"" : ""}>` + kolom.map(([key]) => {
    let v = key === "Fitur" ? `${r["Fitur terpilih (k)"]}/${r["Jumlah fitur"]}` : r[key];
    if (NUM3.includes(key)) return `<td class="num">${fmt(v, 3)}</td>`;
    if (key === "Algoritma" && terpilih) return `<td>${esc(v)} <span class="tg b">terpilih</span></td>`;
    return `<td>${esc(v)}</td>`;
  }).join("") + "</tr>";
}
function renderTabel(j) {
  const t = j.tabel || [], terbaik = t.find(r => r["Peringkat"] === 1) || t[0];
  const sudah = new Set(), perAlgo = [];
  t.forEach(r => { if (!sudah.has(r["Algoritma"])) { sudah.add(r["Algoritma"]); perAlgo.push(r); } });
  const kepala = kol => "<thead><tr>" + kol.map(([k, n]) => `<th${NUM3.includes(k) ? " class=\"num\"" : ""}>${n}</th>`).join("") + "</tr></thead>";
  $("tabelAlgo").innerHTML = kepala(KOLOM_ALGO) + "<tbody>" + perAlgo.map(r => barisTabel(r, KOLOM_ALGO, terbaik && r["Peringkat"] === terbaik["Peringkat"])).join("") + "</tbody>";
  const lengkap = [["Peringkat", "#"], ...KOLOM_ALGO];
  const barisL = r => `<tr${r["Peringkat"] === 1 ? " class=\"terpilih\"" : ""}><td>${r["Peringkat"]}</td>` + barisTabel(r, KOLOM_ALGO, false).replace(/^<tr[^>]*>/, "");
  $("tabelEval").innerHTML = kepala(lengkap) + "<tbody>" + t.map(barisL).join("") + "</tbody>";
  return {perAlgo, terbaik};
}
function renderLangkah(j, perAlgo) {
  const h = j.hasil, k = j.konfigurasi, d = j.jumlah_data, t = h.test || {}, m = h.model_terbaik || {};
  const bersih = (h.pembersihan_ringkas || []).map(x => `<span class="tag">${esc(x.alasan)}: ${x.jumlah} file</span>`).join("");
  const nTotal = (d.berisik || 0) + (d.tidak_berisik || 0);
  const catatanFolder = nTotal !== h.n_file_mentah ? ` Isi folder <code>data/raw</code> saat ini ${nTotal} file (termasuk rekaman baru yang belum dipakai melatih).` : "";
  const langkah = [
    ["Pengumpulan dataset audio", `<p>Dataset penelitian terdiri dari <b>${h.n_file_mentah} file audio</b> hasil rekaman, dikelompokkan ke folder <code>data/raw/berisik</code> dan <code>data/raw/non-berisik</code> menurut labelnya. Kode di awal nama file (mis. <code>24-006</code>) adalah kode perekam, dipakai agar satu perekam tidak muncul di data latih dan data uji sekaligus.${catatanFolder}</p>`],
    ["Preprocessing audio", `<p>Setiap file dibaca menjadi sinyal <b>mono ${fmt(k.sample_rate / 1000, 0)} kHz</b> memakai Librosa (ffmpeg sebagai cadangan untuk MP3 yang tidak standar), dibatasi 60 detik. Satu file utuh menjadi satu baris data (tanpa chunking). Frame di tepi rekaman yang nyaris hening (RMS &lt; ${k.rms_hening}) dibuang. Rekaman dari browser diproses dengan fungsi yang sama dan di-resample polifase ke ${fmt(k.sample_rate / 1000, 0)} kHz.</p>`],
    ["Ekstraksi fitur (Librosa, NumPy, FFT)", `<p><b>Librosa</b> menghitung RMS, spectral centroid, zero-crossing rate, dan ${k.n_mfcc} koefisien MFCC; masing-masing diringkas menjadi rata-rata dan simpangan baku (32 fitur). <b>NumPy dengan FFT</b> (<code>rfft</code>, jendela Hann 2048, hop 512) menghitung persentil level suara dB, roll-off, bandwidth, flatness, dan energi enam pita frekuensi dalam dB (15 fitur). Hasilnya <b>${k.n_fitur_mentah} fitur mentah</b> per file.</p>`],
    ["Data cleaning", `<p>Dari ${h.n_file_mentah} file, ${h.n_file_mentah - h.n_file_bersih} file dikeluarkan sehingga tersisa <b>${h.n_file_bersih} file bersih</b>. Salinan audio identik di data latih dan uji membuat akurasi tampak lebih tinggi (kebocoran data), dan label yang kontradiktif atau terbalik hanya menjadi derau. File mentah tidak diubah; daftar lengkap ada di <code>reports/pembersihan_data.csv</code>.</p><p>${bersih}</p>`],
    ["Kalibrasi dan pembentukan fitur relatif", `<p>Kerasnya rekaman bergantung pada gain mikrofon, sehingga fitur level dinyatakan sebagai <b>selisih terhadap baseline ruangan tenang</b>. Saat training, baseline sebuah file adalah median file “tidak berisik” lain dari perekam yang sama. Dari sini terbentuk <b>${k.n_fitur_relatif} fitur relatif</b> (total ${k.n_kolom} kolom). ${h.tanpa_baseline} file dari perekam tanpa klip tenang lain dikeluarkan, sehingga <b>${h.n_file_dipakai} file</b> dari <b>${h.n_perekam} perekam</b> dipakai. Saat aplikasi dipakai, baseline berasal dari kalibrasi 2 × 10 detik di ruangan tenang.</p>`],
    ["Pembagian data training dan testing", `<p>Data dibagi sekitar 80:20 <b>per perekam</b> (<code>StratifiedGroupKFold</code>, seed 42): <b>${h.n_train} file latih</b> dan <b>${h.n_test} file uji</b>. Perekam di data uji tidak pernah ada di data latih, jadi hasil uji mencerminkan perekam baru.</p>`],
    ["Standardisasi dan seleksi fitur", `<p>Penskala (<code>StandardScaler</code> atau <code>PowerTransformer</code>, dipilih lewat GridSearch) dan seleksi fitur <code>SelectKBest</code> (ANOVA-F) berada <b>di dalam pipeline</b>, sehingga pada setiap lipatan hanya belajar dari data latih. Model terpilih memakai ${m.fitur_terpilih ? m.fitur_terpilih.length : j.fitur_terpilih.length} dari ${j.n_fitur_kombinasi} fitur kombinasinya.</p>`],
    ["Modelling dengan algoritma machine learning", `<p>Lima algoritma (SVM, Random Forest, KNN, Decision Tree, Logistic Regression) diuji pada 10 kombinasi fitur, total <b>${(j.tabel || []).length} percobaan</b>. Parameter dicari dengan <code>GridSearchCV</code> dan cross-validation per perekam 2 × 5 lipatan pada data latih. Model terbaik dipilih dari skor CV data latih, bukan dari data uji: <b>${esc(j.model)}</b>, parameter ${esc(Object.entries(m.params || j.parameter || {}).map(([a, b]) => a + " = " + b).join(", "))}.</p>`],
    ["Evaluasi dan penyimpanan model", `<p>Model dievaluasi pada data uji (accuracy ${pct(t.accuracy)}, F1-score ${pct(t.f1)}) dan dengan cross-validation per perekam pada seluruh data (${pct(h.cv_per_perekam.model_terbaik)}). Model disimpan ke <code>models/model_kebisingan.joblib</code> bersama daftar fitur dan versi fitur. Saat prediksi, Flask memuat model itu, menghitung fitur dari rekaman 10 detik, menambah fitur relatif dengan baseline kalibrasi, lalu memberi label <b>berisik</b> bila peluang berisik ≥ ${fmt(100 * k.ambang, 0)}%.</p>`],
  ];
  $("evalLangkah").innerHTML = langkah.map(([n, isi]) => `<li><div><b>${n}</b>${isi}</div></li>`).join("");
}
async function muatEval() {
  if (evalSudah || evalSedang) return;
  evalSedang = true;
  try {
    const r = await fetch("/api/info"), j = await r.json();
    if (!r.ok) throw new Error(j.galat || "Galat server (" + r.status + ")");
    const h = j.hasil || {}, t = h.test, c = h.cv_per_perekam || {};
    if (!t) throw new Error("File reports/hasil_evaluasi.json belum ada. Jalankan: python src/train.py");
    $("evalModel").textContent = j.model;
    renderMetrik(t);
    const nTotal = (j.jumlah_data.berisik || 0) + (j.jumlah_data.tidak_berisik || 0);
    $("evalKv").innerHTML =
      "<h4 class=\"muted small\">Cross-validation</h4>" + kvList([
        ["CV per perekam 5 × 5 (seluruh data)", `${pct(c.model_terbaik)} <span class="muted">± ${pct(c.std)}</span>`],
        ["Pembanding tanpa kalibrasi", `${pct(c.pembanding_tanpa_kalibrasi)} <span class="muted">± ${pct(c.std_pembanding)}</span>`],
        ["Algoritma pembanding", esc(c.nama_pembanding || "-")],
        ["CV akurasi data latih (GridSearch 2 × 5)", pct((h.model_terbaik || {}).cv_akurasi_latih)]]) +
      "<h4 class=\"muted small\" style=\"margin-top:14px\">Jumlah dataset</h4>" + kvList([
        ["File mentah", h.n_file_mentah],
        ["Setelah pembersihan", h.n_file_bersih],
        ["Dipakai (punya baseline)", `${h.n_file_dipakai} file, ${h.n_perekam} perekam`],
        ["Data latih / data uji", `${h.n_train} / ${h.n_test}`]].concat(nTotal !== h.n_file_mentah ? [["Isi folder data/raw sekarang", nTotal + " file"]] : []));
    renderCm(h.confusion_matrix);
    renderFitur(j);
    const {perAlgo, terbaik} = renderTabel(j);
    if (terbaik && perAlgo.some(a => a["Accuracy"] > terbaik["Accuracy"])) {
      const p = document.createElement("p");
      p.className = "note";
      p.textContent = "Beberapa algoritma memiliki accuracy data uji lebih tinggi, tetapi model dipilih dari skor cross-validation data latih (bukan data uji) agar pemilihan tidak bias terhadap hanya 32 file uji.";
      $("tabelAlgo").parentElement.after(p);
    }
    renderLangkah(j, perAlgo);
    $("evalPesan").hidden = true; $("evalIsi").hidden = false;
    evalSudah = true;
  } catch (e) {
    $("evalPesan").textContent = "Evaluasi belum dapat ditampilkan: " + e.message;
  } finally { evalSedang = false; }
}

// ---------- awal
(async () => {
  cekKonteks(); tampilKal(); setPillMic(false); setFase("siaga");
  pilihTab(location.hash.slice(1));
  try {
    const r = await fetch("/api/info"), j = await r.json();
    $("modelInfo").textContent = r.ok ? j.model : j.galat;
    if (r.ok && j.konfigurasi && j.konfigurasi.ambang > 0) AMBANG = j.konfigurasi.ambang;
  } catch (e) { $("modelInfo").textContent = "Server tidak terjangkau"; }
})();
