// Invite-link join page: no account and no manual token copy.
// Clicking "Saya Izinkan" redeems the single-use invite once; the server
// creates the device and returns its token, which this page stores in
// this browser only (localStorage) and uses to send browser GPS locations.
// Sending stops when the user presses "Berhenti berbagi" or closes the
// page, because the browser geolocation API cannot run in the background
// from a closed tab. HTTPS (or localhost) is required for geolocation.
const parts = window.location.pathname.split("/").filter(Boolean);
const code = parts.length >= 2 && parts[0] === "join" ? parts[1] : "";
const infoEl = document.getElementById("invite-info");
const errorEl = document.getElementById("join-error");
const consentEl = document.getElementById("consent");
const sharingEl = document.getElementById("sharing");
const statusEl = document.getElementById("share-status");
let watchId = null, timerId = null, deviceId = null, deviceToken = null;

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
function stopSending() {
  if (watchId !== null) navigator.geolocation.clearWatch(watchId);
  if (timerId) clearInterval(timerId);
  watchId = null; timerId = null;
}
async function sendPosition(pos) {
  if (!deviceId || !deviceToken) return;
  try {
    const d = await api("/api/devices/" + deviceId + "/locations", {
      method: "POST",
      headers: { "X-Device-Token": deviceToken, "Content-Type": "application/json" },
      body: JSON.stringify({
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
        timestamp: new Date(pos.timestamp).toISOString(),
      }),
    });
    statusEl.textContent = "Terkirim: " + d.latitude + ", " + d.longitude + " pada " + d.recorded_at;
  } catch (e) {
    statusEl.textContent = "Gagal mengirim: " + e.message;
  }
}
function startSending() {
  if (!("geolocation" in navigator)) {
    errorEl.textContent = "Browser ini tidak mendukung geolocation. Gunakan browser modern lewat HTTPS atau localhost.";
    return;
  }
  const onError = (e) => {
    statusEl.textContent = "Izin lokasi ditolak atau GPS tidak tersedia (" + (e.message || e.code) + "). Pengiriman berhenti sampai izin diberikan dan halaman dimuat ulang.";
    stopSending();
  };
  // watchPosition for movement, plus a 30s poll so a point is sent even
  // when the device barely moves and watchPosition stays quiet.
  watchId = navigator.geolocation.watchPosition(sendPosition, onError, { enableHighAccuracy: true, maximumAge: 10000 });
  timerId = setInterval(() => {
    navigator.geolocation.getCurrentPosition(sendPosition, onError, { enableHighAccuracy: true, maximumAge: 10000 });
  }, 30000);
  navigator.geolocation.getCurrentPosition(sendPosition, onError, { enableHighAccuracy: true });
  statusEl.textContent = "Menunggu izin lokasi browser…";
}

async function init() {
  if (!code) {
    infoEl.textContent = "Link undangan tidak lengkap.";
    return;
  }
  // If this browser already redeemed an invite, resume it instead of
  // redeeming again (a redeemed invite cannot be redeemed a second time).
  const saved = JSON.parse(localStorage.getItem("kltp_invite") || "null");
  if (saved && saved.code === code && saved.device_id && saved.device_token) {
    deviceId = saved.device_id; deviceToken = saved.device_token;
    infoEl.textContent = "Perangkat ini sudah terhubung. Tekan “Saya Izinkan” untuk mulai berbagi lagi, atau “Berhenti berbagi” untuk berhenti.";
    consentEl.style.display = "";
    document.getElementById("device-name").value = saved.device_name || "";
    return;
  }
  try {
    const info = await api("/api/invites/" + encodeURIComponent(code) + "/info");
    if (info.status !== "active") {
      infoEl.textContent = "Undangan ini sudah " + (info.status === "used" ? "dipakai" : "kadaluarsa") + " dan tidak bisa dipakai lagi.";
      return;
    }
    infoEl.textContent = "Undangan dari " + info.inviter + " untuk perangkat “" + info.device_name + "”. Berlaku sampai " + info.expires_at + ".";
    document.getElementById("device-name").value = info.device_name;
    consentEl.style.display = "";
  } catch (e) {
    infoEl.textContent = "Undangan tidak ditemukan atau sudah tidak berlaku: " + e.message;
  }
}

document.getElementById("btn-allow").onclick = async () => {
  errorEl.textContent = "";
  try {
    if (!deviceId) {
      const name = document.getElementById("device-name").value.trim();
      const d = await api("/api/invites/" + encodeURIComponent(code) + "/redeem", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_name: name }),
      });
      deviceId = d.device_id; deviceToken = d.device_token;
      localStorage.setItem("kltp_invite", JSON.stringify({ code, device_id: deviceId, device_token: deviceToken, device_name: d.device_name }));
      infoEl.textContent = "Perangkat “" + d.device_name + "” (ID " + deviceId + ") terhubung.";
    }
    consentEl.style.display = "none";
    sharingEl.style.display = "";
    startSending();
  } catch (e) {
    errorEl.textContent = "Gagal: " + e.message;
  }
};
document.getElementById("btn-stop").onclick = () => {
  stopSending();
  sharingEl.style.display = "none";
  consentEl.style.display = "";
  statusEl.textContent = "";
  infoEl.textContent = "Berbagi dihentikan. Tekan “Saya Izinkan” untuk mulai lagi dari browser ini.";
};
window.addEventListener("beforeunload", stopSending);
init();
