"use strict";
/* Halaman front: hanya menampilkan gelombang audio dan memutar peringatan. Tidak ada kontrol streaming maupun suara.
   Streaming dan suara peringatan diatur admin; data diambil dari /api/status beberapa kali per detik. */
const $ = id => document.getElementById(id);
const POLL_MS = 400, SLOT = 160, LAJU = 11.7;          // SLOT titik gelombang yang tampil; LAJU = titik per detik dari mikrofon admin
const embed = document.body.classList.contains("embed");
const reduce = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
const FASE = {siaga: "Siaga", kalibrasi: "Kalibrasi", rekam: "Merekam", proses: "Memproses", jeda: "Jeda"};
const fmt = (x, d = 1) => Number(x).toLocaleString("id-ID", {minimumFractionDigits: d, maximumFractionDigits: d});
let seq = 0, hasilTerakhir = 0, levelDb = -90, status = null, gagal = 0, bannerTimer = null;

// ---------- suara peringatan: diaktifkan admin lewat dashboard; di sini hanya diputar bila admin mengizinkan
const peringatan = new Audio("/api/peringatan"); peringatan.preload = "auto";
let suaraFront = true, terbuka = !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
function catatan() { $("fCatatan").hidden = embed || !suaraFront || terbuka; }
async function bukaKunci() {          // ketukan/tombol apa saja di halaman ini cukup agar browser mengizinkan suara otomatis
  if (terbuka || embed) return;
  peringatan.muted = true;
  try { await peringatan.play(); peringatan.pause(); peringatan.currentTime = 0; terbuka = true; } catch (e) { terbuka = false; }
  peringatan.muted = false; catatan();
}
["pointerdown", "keydown", "touchend"].forEach(e => window.addEventListener(e, bukaKunci, {passive: true}));
function putar() {
  if (embed || !suaraFront) return;   // di dashboard admin, suara dikendalikan dari dashboard itu sendiri
  try { peringatan.currentTime = 0; peringatan.play().then(() => { terbuka = true; catatan(); }).catch(() => { terbuka = false; catatan(); }); } catch (e) { /* abaikan */ }
}

// ---------- gelombang: gaya sama dengan monitor admin (petak, warna menurut kerasnya sinyal, cahaya, mode Gelombang/Batang)
const kanvas = $("fGel"), c = kanvas.getContext("2d");
let W = 0, H = 0, mode = "garis", rmsHalus = 0, refPk = 0, riak = 0, aktif = false;
let jendela = [], antri = [], acc = 1, tFrame = performance.now(), tLevel = 0;
try { const m = localStorage.getItem("gel_mode"); if (m === "batang" || m === "garis") mode = m; } catch (e) { /* abaikan */ }

const lerp = (a, b, t) => a + (b - a) * t;
function warna(t, a) {                // cyan (pelan) → kuning → oranye (keras)
  const A = [34, 211, 238], B = [255, 200, 87], C = [255, 106, 77];
  const [x, y] = t < 0.6 ? [A, B] : [B, C], u = t < 0.6 ? t / 0.6 : (t - 0.6) / 0.4;
  return `rgba(${Math.round(lerp(x[0], y[0], u))},${Math.round(lerp(x[1], y[1], u))},${Math.round(lerp(x[2], y[2], u))},${a})`;
}
function ukur() {
  const r = kanvas.getBoundingClientRect(); W = r.width; H = r.height;
  if (!W || !H) return;
  const d = Math.min(window.devicePixelRatio || 1, 2);
  kanvas.width = Math.round(W * d); kanvas.height = Math.round(H * d);
  c.setTransform(d, 0, 0, d, 0, 0);
}
if (window.ResizeObserver) new ResizeObserver(ukur).observe(kanvas); else window.addEventListener("resize", ukur);
ukur();

function kotak(x, y, w, h, r) { c.beginPath(); if (c.roundRect) c.roundRect(x, y, w, h, r); else c.rect(x, y, w, h); c.fill(); }
function petak() {
  c.lineWidth = 1; c.strokeStyle = "rgba(148,163,184,.08)"; c.beginPath();
  for (let i = 1; i < 10; i++) { const x = Math.round(W * i / 10) + .5; c.moveTo(x, 0); c.lineTo(x, H); }
  for (const f of [.25, .75]) { const y = Math.round(H * f) + .5; c.moveTo(0, y); c.lineTo(W, y); }
  c.stroke();
  c.strokeStyle = "rgba(148,163,184,.2)"; c.beginPath(); c.moveTo(0, Math.round(H / 2) + .5); c.lineTo(W, Math.round(H / 2) + .5); c.stroke();
}
function nilaiJendela() {             // SLOT titik terbaru, rata-rata 3 titik agar tepi gelombang halus; kosong di kiri = 0
  const v = new Array(SLOT).fill(0), n = jendela.length, off = SLOT - n;
  for (let i = 0; i < n; i++) { const a = jendela[Math.max(0, i - 1)], b = jendela[i], d = jendela[Math.min(n - 1, i + 1)]; v[off + i] = (a + 2 * b + d) / 4; }
  return v;
}
function garis(v) {
  const mid = H / 2, maksY = H * 0.36, t = Math.min(1, rmsHalus / 0.2), gain = maksY / Math.max(refPk, 0.08);
  const dx = W / (SLOT - 1), geser = aktif ? dx * (1 - Math.min(1, acc)) : 0, atas = [], bawah = [];
  for (let j = 0; j < SLOT; j++) {
    let a;
    if (aktif) a = Math.min(maksY * 1.2, v[j] * gain);
    else a = reduce ? 0 : Math.abs(Math.sin(j / SLOT * 18.85 + riak)) * H * 0.014 * Math.sin(j / SLOT * Math.PI);
    atas.push([j * dx + geser, mid - a]); bawah.push([j * dx + geser, mid + a]);
  }
  if (!aktif && !reduce) riak += 0.025;
  const lengkung = (p, p0) => {        // kurva halus lewat titik tengah, sama seperti garis admin
    const j = new Path2D(); j.moveTo(p[0][0], p[0][1]);
    for (let k = 1; k < p.length - 1; k++) j.quadraticCurveTo(p[k][0], p[k][1], (p[k][0] + p[k + 1][0]) / 2, (p[k][1] + p[k + 1][1]) / 2);
    j.lineTo(p[p.length - 1][0], p[p.length - 1][1]); return j;
  };
  const jAtas = lengkung(atas), jBawah = lengkung(bawah);
  const isi = new Path2D(jAtas); isi.lineTo(bawah[SLOT - 1][0], bawah[SLOT - 1][1]);
  for (let k = SLOT - 2; k >= 0; k--) isi.lineTo(bawah[k][0], bawah[k][1]);
  isi.closePath();
  const gi = c.createLinearGradient(0, mid - maksY, 0, mid + maksY);
  gi.addColorStop(0, warna(t, .24)); gi.addColorStop(.5, warna(t, .03)); gi.addColorStop(1, warna(t, .24));
  c.fillStyle = gi; c.fill(isi);
  const al = aktif ? 1 : .5, g = c.createLinearGradient(0, 0, W, 0);
  g.addColorStop(0, warna(t, 0)); g.addColorStop(.1, warna(t, al)); g.addColorStop(.9, warna(t, al)); g.addColorStop(1, warna(t, 0));
  c.lineJoin = "round"; c.lineCap = "round"; c.strokeStyle = g; c.lineWidth = 2.2;
  c.shadowColor = warna(t, .9); c.shadowBlur = aktif ? 16 : 0; c.stroke(jAtas); c.stroke(jBawah); c.shadowBlur = 0;
}
function batang(v) {
  const sw = W / SLOT, bw = Math.max(2, sw * 0.62), mid = H / 2, maksH = H * 0.72, geser = aktif ? sw * (1 - Math.min(1, acc)) : 0, n = jendela.length;
  let ref = 0.08; for (let i = 0; i < n; i++) if (jendela[i] > ref) ref = jendela[i];
  for (let i = 0; i < SLOT; i++) {
    const x = i * sw + (sw - bw) / 2 + geser;
    if (i >= SLOT - n) { const p = v[i], h = Math.max(3, Math.min(1, p / ref) * maksH); c.fillStyle = warna(Math.min(1, p / 0.5), .95); kotak(x, mid - h / 2, bw, h, bw / 2); }
    else { c.fillStyle = "rgba(148,163,184,.16)"; kotak(x, mid - 1.5, bw, 3, 1.5); }
  }
}
function gambar(now) {
  requestAnimationFrame(gambar);
  if (!W || !H) return;
  const dt = Math.min(0.25, (now - tFrame) / 1000); tFrame = now;
  // lepas titik dari antrean dengan laju tetap agar gelombang menggulir halus (antrean menumpuk → dikejar lebih cepat)
  if (aktif) {
    acc += dt * LAJU * (1 + Math.max(0, antri.length - 6) / 6);
    while (acc >= 1 && antri.length) { jendela.push(antri.shift()); acc -= 1; }
    if (acc > 1) acc = 1;
    while (jendela.length > SLOT) jendela.shift();
  }
  const pk = jendela.length && aktif ? jendela[jendela.length - 1] : 0;
  refPk = Math.max(pk, refPk * 0.965);
  const rmsNow = aktif && levelDb > -89 ? Math.pow(10, levelDb / 20) : 0;
  rmsHalus += (rmsNow - rmsHalus) * 0.2;
  c.clearRect(0, 0, W, H); petak();
  const v = nilaiJendela();
  if (mode === "batang") batang(v); else garis(v);
}
requestAnimationFrame(gambar);

function setMode(m) {
  mode = m; try { localStorage.setItem("gel_mode", m); } catch (e) { /* abaikan */ }
  document.querySelectorAll(".seg button").forEach(b => { const on = b.dataset.mode === m; b.classList.toggle("aktif", on); b.setAttribute("aria-pressed", on); });
}
document.querySelectorAll(".seg button").forEach(b => { b.onclick = () => setMode(b.dataset.mode); });
setMode(mode);

// ---------- tampilan status dan hasil
function tampilBanner(h) {
  const b = $("fBanner"); clearTimeout(bannerTimer);
  if (h.label === "berisik") {
    b.hidden = false; b.textContent = `BERISIK terdeteksi (${fmt(100 * h.peluang)}%). Jangan rame!`;
    b.style.animation = "none"; void b.offsetWidth; b.style.animation = "";
    bannerTimer = setTimeout(() => { b.hidden = true; }, 8000);
  } else b.hidden = true;
  const el = $("fHasil"); el.className = "hasil " + (h.label === "berisik" ? "berisik" : "tenang");
  el.textContent = h.label === "berisik" ? `Berisik · peluang ${fmt(100 * h.peluang)}% · ${h.waktu}` : `Tidak berisik · peluang berisik ${fmt(100 * h.peluang)}% · ${h.waktu}`;
}
function render(s) {
  status = s;
  const putus = s.jalan && s.umur_data != null && s.umur_data > 20;
  aktif = s.jalan && !putus;
  $("fKosong").hidden = aktif;
  $("fPill").textContent = !s.jalan ? "Streaming berhenti" : putus ? "Sumber tidak mengirim data" : "Streaming berjalan";
  $("fPill").className = "pill" + (s.jalan && !putus ? " ok" : "");
  $("fSumber").textContent = !s.jalan ? "Menunggu admin memulai streaming." : putus
    ? "Tidak ada data baru. Pastikan dashboard admin tetap terbuka; menutupnya atau memindahkannya ke halaman lain menghentikan pemrosesan."
    : `Sumber audio: ${s.sumber === "arduino" ? "perangkat Arduino" : "mikrofon admin"}.`;
  $("fFase").textContent = s.jalan ? (FASE[s.fase] || s.fase) : "Siaga"; $("fFase").dataset.fase = s.jalan ? s.fase : "siaga";
  $("fWaktu").textContent = s.jalan && s.fase === "rekam" ? `${fmt(Math.min(s.fase_detik, s.durasi), 1)} / ${s.durasi} dtk` : s.jalan && s.fase === "jeda" ? `Jeda ${fmt(Math.max(0, s.jeda - s.fase_detik), 0)} dtk` : "—";
  const lv = s.jalan ? Math.max(0, Math.min(1, (levelDb + 70) / 70)) : 0;
  $("fLevelBar").style.width = (100 * lv) + "%";
  $("fLevel").textContent = s.jalan && levelDb > -89 ? (levelDb <= -90 ? "−∞ dBFS" : fmt(levelDb, 0).replace("-", "−") + " dBFS") : "—";
}
async function poll() {
  try {
    const r = await fetch("/api/status?sejak=" + seq, {cache: "no-store"});
    if (r.status === 401) { location.href = "/login"; return; }
    const s = await r.json(); gagal = 0;
    if (s.seq < seq) { seq = 0; jendela = []; antri = []; }                      // server dimulai ulang
    suaraFront = s.suara_front !== false; catatan();
    s.gel.forEach(g => { g.v.forEach(v => antri.push(v)); levelDb = g.db; seq = g.s; });
    while (antri.length > SLOT) antri.shift();
    if (!s.jalan) { levelDb = -90; antri = []; jendela = []; refPk = 0; }
    if (s.hasil && s.hasil.id !== hasilTerakhir) {
      const baru = hasilTerakhir !== 0 || (s.umur_data != null && s.umur_data < 15);   // jangan membunyikan hasil lama saat halaman baru dibuka
      hasilTerakhir = s.hasil.id;
      if (baru) { tampilBanner(s.hasil); if (s.hasil.label === "berisik") putar(); }
    }
    $("fModel").textContent = s.model || $("fModel").textContent;
    render(s);
  } catch (e) {
    gagal++; if (gagal > 3) { $("fPill").textContent = "Server tidak terjangkau"; $("fPill").className = "pill"; }
  }
}
setInterval(poll, POLL_MS); poll();
