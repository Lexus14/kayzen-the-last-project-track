let map, markers = [];
let eventSource = null;
let token = localStorage.getItem("kltp_token") || "";
let username = localStorage.getItem("kltp_user") || "";

function startStream() {
  if (eventSource) eventSource.close();
  if (!token) return;
  // Live updates: the server pushes an event on every new location,
  // and the browser refetches the device list right then.
  eventSource = new EventSource("/api/devices/stream?token=" + encodeURIComponent(token));
  eventSource.onmessage = () => refresh().catch(() => {});
  eventSource.onerror = () => { /* browser auto-reconnects */ };
}
function stopStream() {
  if (eventSource) eventSource.close();
  eventSource = null;
}

function authHeaders() {
  return token ? { "Authorization": "Bearer " + token, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}
function showAuth() {
  document.getElementById("auth-status").textContent = token ? ("Masuk sebagai " + username) : "Belum masuk.";
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
async function refresh() {
  if (!token) { showAuth(); return; }
  showAuth();
  const data = await api("/api/devices", { headers: authHeaders() });
  const tbody = document.getElementById("device-rows");
  tbody.innerHTML = "";
  markers.forEach(m => m.remove()); markers = [];
  let first = null;
  data.devices.forEach(d => {
    const loc = d.last_location;
    const tr = document.createElement("tr");
    tr.innerHTML = "<td>" + d.id + "</td><td>" + d.name + "</td><td>" + (d.online ? "online" : "offline") + "</td><td>" + (loc ? loc.latitude + ", " + loc.longitude : "-") + "</td><td>" + (loc ? loc.recorded_at : "-") + "</td>";
    tbody.appendChild(tr);
    if (loc) {
      const m = L.marker([loc.latitude, loc.longitude]).addTo(map).bindPopup(d.name + "<br>" + loc.latitude + ", " + loc.longitude);
      markers.push(m);
      if (!first) first = [loc.latitude, loc.longitude];
    }
  });
  if (first) map.setView(first, 13);
}
document.getElementById("btn-register").onclick = async () => {
  try {
    const u = document.getElementById("username").value, p = document.getElementById("password").value;
    const d = await api("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
    document.getElementById("auth-status").textContent = "Terdaftar sebagai " + d.username + " (" + d.role + "). Sekarang masuk.";
  } catch (e) { document.getElementById("auth-status").textContent = "Gagal: " + e.message; }
};
document.getElementById("btn-login").onclick = async () => {
  try {
    const u = document.getElementById("username").value, p = document.getElementById("password").value;
    const d = await api("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
    token = d.token; username = d.username;
    localStorage.setItem("kltp_token", token); localStorage.setItem("kltp_user", username);
    startStream();
    await refresh();
  } catch (e) { document.getElementById("auth-status").textContent = "Gagal: " + e.message; }
};
document.getElementById("btn-logout").onclick = () => {
  stopStream();
  token = ""; username = ""; localStorage.removeItem("kltp_token"); localStorage.removeItem("kltp_user"); showAuth();
};
document.getElementById("btn-device").onclick = async () => {
  try {
    const name = document.getElementById("device-name").value;
    const d = await api("/api/devices", { method: "POST", headers: authHeaders(), body: JSON.stringify({ name }) });
    document.getElementById("device-token").textContent = "Perangkat #" + d.id + " — token (simpan sekarang, hanya muncul sekali): " + d.device_token;
    await refresh();
  } catch (e) { document.getElementById("device-token").textContent = "Gagal: " + e.message; }
};
document.getElementById("btn-refresh").onclick = () => refresh().catch(e => alert(e.message));
document.getElementById("btn-loc").onclick = async () => {
  try {
    const id = document.getElementById("loc-device").value;
    const d = await api("/api/devices/" + id + "/locations", { method: "POST", headers: { "X-Device-Token": document.getElementById("loc-token").value, "Content-Type": "application/json" }, body: JSON.stringify({ latitude: parseFloat(document.getElementById("loc-lat").value), longitude: parseFloat(document.getElementById("loc-lon").value) }) });
    document.getElementById("loc-status").textContent = "Terkirim: " + d.latitude + ", " + d.longitude + " pada " + d.recorded_at;
    await refresh();
  } catch (e) { document.getElementById("loc-status").textContent = "Gagal: " + e.message; }
};
map = L.map("map").setView([-2.99, 104.75], 5);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
showAuth();
if (token) { startStream(); refresh().catch(() => {}); }
