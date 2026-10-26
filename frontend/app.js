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
function setBusy(btn, busy) {
  if (busy) {
    btn.dataset.label = btn.textContent;
    btn.textContent = "Memproses…";
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.label || btn.textContent;
    btn.disabled = false;
  }
}
async function doLogin(u, p) {
  const d = await api("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
  token = d.token; username = d.username;
  localStorage.setItem("kltp_token", token); localStorage.setItem("kltp_user", username);
  startStream();
  await refresh();
  return d;
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
  const sel = document.getElementById("route-device");
  const keep = sel.value;
  sel.innerHTML = '<option value="">Pilih perangkat…</option>';
  data.devices.forEach(d => {
    const o = document.createElement("option");
    o.value = d.id; o.textContent = "#" + d.id + " " + d.name;
    sel.appendChild(o);
  });
  sel.value = keep;
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
document.getElementById("btn-register").onclick = async (ev) => {
  const btn = ev.currentTarget;
  setBusy(btn, true);
  document.getElementById("auth-status").textContent = "Memproses pendaftaran…";
  try {
    const u = document.getElementById("username").value, p = document.getElementById("password").value;
    const d = await api("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: u, password: p }) });
    // Registering used to leave the user logged out, so a press looked
    // like nothing happened. Log in immediately with the same credentials.
    await doLogin(u, p);
    document.getElementById("auth-status").textContent = "Terdaftar dan masuk sebagai " + d.username + " (" + d.role + ").";
  } catch (e) {
    if (e.message.includes("already exists")) {
      document.getElementById("auth-status").textContent = "Username sudah dipakai — tekan Masuk dengan password yang sama.";
    } else {
      document.getElementById("auth-status").textContent = "Gagal: " + e.message;
    }
  } finally { setBusy(btn, false); }
};
document.getElementById("btn-login").onclick = async (ev) => {
  const btn = ev.currentTarget;
  setBusy(btn, true);
  document.getElementById("auth-status").textContent = "Memproses masuk…";
  try {
    const u = document.getElementById("username").value, p = document.getElementById("password").value;
    await doLogin(u, p);
  } catch (e) { document.getElementById("auth-status").textContent = "Gagal: " + e.message; }
  finally { setBusy(btn, false); }
};
document.getElementById("btn-logout").onclick = () => {
  stopStream();
  token = ""; username = ""; localStorage.removeItem("kltp_token"); localStorage.removeItem("kltp_user"); showAuth();
};
document.getElementById("btn-device").onclick = async (ev) => {
  const btn = ev.currentTarget;
  setBusy(btn, true);
  try {
    const name = document.getElementById("device-name").value;
    const d = await api("/api/devices", { method: "POST", headers: authHeaders(), body: JSON.stringify({ name }) });
    document.getElementById("device-token").textContent = "Perangkat #" + d.id + " — token (simpan sekarang, hanya muncul sekali): " + d.device_token;
    await refresh();
  } catch (e) { document.getElementById("device-token").textContent = "Gagal: " + e.message; }
  finally { setBusy(btn, false); }
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
let routePoints = [], routeLine = null, routeStart = null, routeEnd = null, playMarker = null, playTimer = null;
function haversineKm(a, b) {
  const R = 6371, toRad = d => d * Math.PI / 180;
  const dLat = toRad(b.latitude - a.latitude), dLon = toRad(b.longitude - a.longitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function clearRoute() {
  if (playTimer) clearInterval(playTimer);
  playTimer = null;
  [routeLine, routeStart, routeEnd, playMarker].forEach(x => { if (x) x.remove(); });
  routeLine = routeStart = routeEnd = playMarker = null;
  routePoints = [];
  document.getElementById("route-slider").max = 0;
  document.getElementById("route-info").textContent = "";
  document.getElementById("route-point").textContent = "";
}
function showRoutePoint(i) {
  if (!routePoints.length) return;
  i = Math.max(0, Math.min(i, routePoints.length - 1));
  const p = routePoints[i];
  document.getElementById("route-slider").value = i;
  document.getElementById("route-point").textContent = "Titik " + (i + 1) + " dari " + routePoints.length + ": " + p.latitude + ", " + p.longitude + " — " + p.recorded_at;
  if (!playMarker) playMarker = L.circleMarker([p.latitude, p.longitude], { radius: 8, color: "#f59e0b" }).addTo(map);
  else playMarker.setLatLng([p.latitude, p.longitude]);
}
document.getElementById("btn-route").onclick = async () => {
  try {
    const id = document.getElementById("route-device").value;
    if (!id) throw new Error("pilih perangkat dulu");
    const from = document.getElementById("route-from").value, to = document.getElementById("route-to").value;
    let q = "/api/devices/" + id + "/locations?limit=500";
    if (from) q += "&from=" + encodeURIComponent(from);
    if (to) q += "&to=" + encodeURIComponent(to);
    const data = await api(q, { headers: authHeaders() });
    clearRoute();
    routePoints = data.locations;
    if (!routePoints.length) { document.getElementById("route-info").textContent = "Tidak ada lokasi pada rentang itu."; return; }
    const pts = routePoints.map(p => [p.latitude, p.longitude]);
    routeLine = L.polyline(pts, { color: "#2563eb" }).addTo(map);
    routeStart = L.marker(pts[0]).addTo(map).bindPopup("Awal: " + routePoints[0].recorded_at);
    routeEnd = L.marker(pts[pts.length - 1]).addTo(map).bindPopup("Akhir: " + routePoints[routePoints.length - 1].recorded_at);
    let km = 0;
    for (let i = 1; i < routePoints.length; i++) km += haversineKm(routePoints[i - 1], routePoints[i]);
    document.getElementById("route-slider").max = routePoints.length - 1;
    document.getElementById("route-info").textContent = routePoints.length + " titik, jarak sekitar " + km.toFixed(2) + " km, " + routePoints[0].recorded_at + " s.d. " + routePoints[routePoints.length - 1].recorded_at;
    map.fitBounds(routeLine.getBounds());
    showRoutePoint(0);
  } catch (e) { document.getElementById("route-info").textContent = "Gagal: " + e.message; }
};
document.getElementById("btn-route-clear").onclick = clearRoute;
document.getElementById("route-slider").oninput = (e) => showRoutePoint(parseInt(e.target.value, 10));
document.getElementById("btn-play").onclick = () => {
  if (!routePoints.length || playTimer) return;
  let i = parseInt(document.getElementById("route-slider").value, 10) || 0;
  playTimer = setInterval(() => {
    if (i >= routePoints.length - 1) { clearInterval(playTimer); playTimer = null; return; }
    showRoutePoint(++i);
  }, 800);
};
document.getElementById("btn-pause").onclick = () => { if (playTimer) clearInterval(playTimer); playTimer = null; };

map = L.map("map").setView([-2.99, 104.75], 5);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
showAuth();
if (token) { startStream(); refresh().catch(() => {}); }
