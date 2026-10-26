#!/usr/bin/env python3
"""Pengirim lokasi otomatis untuk Kayzen-the-last-project-track (Termux).

Mengambil GPS dari perangkat sendiri (yang sudah memberi izin) lewat
Termux:API, lalu mengirimnya berkala ke server. Hanya lokasi perangkat
sendiri yang dikirim, dan hanya dengan token perangkatnya sendiri.

Konfigurasi (urutan menang: argumen CLI > environment > file env lokal):
  SERVER_URL       mis. http://127.0.0.1:8000 atau URL Cloudflare Tunnel
  DEVICE_ID        mis. 1
  DEVICE_TOKEN     token perangkat (JANGAN di-commit, JANGAN dibagikan)
  INTERVAL_SECONDS default 30
  QUEUE_PATH       default ~/.cache/kltp_sender_queue.jsonl
  MOCK_LAT / MOCK_LON  hanya untuk uji tanpa GPS (dengan --mock)

File env lokal (opsional, tidak di-commit):
  ~/.kltp-sender.env berisi baris KEY=VALUE, mis.:
    SERVER_URL=http://127.0.0.1:8000
    DEVICE_ID=1
    DEVICE_TOKEN=token-perangkat-kamu
    INTERVAL_SECONDS=30

Antrean offline: kalau server tidak bisa dihubungi (jaringan mati, server
berhenti, timeout, atau error 5xx), titik disimpan ke QUEUE_PATH dan
dikirim ulang duluan pada pengiriman berikutnya, sesuai urutan waktu.
Error 401/404/400 TIDAK diantrekan: itu artinya token salah, perangkat
tidak ada, atau datanya tidak valid, jadi mengulanginya tidak akan membantu.

Token tidak pernah dicetak ke log.

Pemakaian di Termux (server harus jalan dulu, di sesi lain):
  1. Pasang Termux:API: aplikasi Termux:API + `pkg install -y termux-api`
     dan beri izin lokasi ke Termux:API.
  2. Uji GPS sekali:  termux-location -p gps -r once
  3. Isi ~/.kltp-sender.env (lihat di atas), lalu:
       python3 tools/send_location.py
     Sekali kirim saja (uji):
       python3 tools/send_location.py --once
     Uji logika tanpa GPS:
       python3 tools/send_location.py --once --mock

Hanya memakai Python standar, tanpa dependency tambahan.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ENV_FILE = os.path.expanduser("~/.kltp-sender.env")
DEFAULT_QUEUE = os.path.expanduser("~/.cache/kltp_sender_queue.jsonl")


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def get_location(mock, timeout=30):
    """Kembalikan dict {latitude, longitude, accuracy} atau None."""
    if mock:
        try:
            return {
                "latitude": float(os.environ.get("MOCK_LAT", "-3.123")),
                "longitude": float(os.environ.get("MOCK_LON", "102.123")),
                "accuracy": None,
            }
        except ValueError:
            log("MOCK_LAT/MOCK_LON tidak valid.")
            return None
    try:
        out = subprocess.run(
            ["termux-location", "-p", "gps", "-r", "once"],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        log("termux-location tidak ditemukan. Pasang Termux:API: pkg install -y termux-api")
        return None
    except subprocess.TimeoutExpired:
        log("termux-location timeout (GPS belum dapat fix). Coba lagi interval berikutnya.")
        return None
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip()
        log(f"termux-location gagal (code {out.returncode}): {detail[:200]}")
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        log("Output termux-location bukan JSON yang valid.")
        return None
    # termux-location bisa mengembalikan satu objek atau list.
    if isinstance(data, list):
        data = data[0] if data else {}
    try:
        lat, lon = float(data["latitude"]), float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        log("termux-location tidak mengembalikan latitude/longitude.")
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        log(f"Koordinat di luar jangkauan: {lat}, {lon}. Dilewati.")
        return None
    acc = data.get("accuracy")
    return {"latitude": lat, "longitude": lon, "accuracy": acc}


def post_location(server, device_id, token, point):
    """Kirim satu titik. Kembalikan (status, retryable, pesan)."""
    url = f"{server.rstrip('/')}/api/devices/{device_id}/locations"
    body = {"latitude": point["latitude"], "longitude": point["longitude"]}
    if point.get("accuracy") is not None:
        body["accuracy"] = point["accuracy"]
    if point.get("recorded_at"):
        body["recorded_at"] = point["recorded_at"]
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Device-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, False, f"terkirim {body['latitude']}, {body['longitude']}"
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        retryable = e.code >= 500
        return e.code, retryable, f"HTTP {e.code}: {detail}"
    except Exception as e:  # jaringan mati, server berhenti, timeout, DNS
        return 0, True, f"jaringan/server tidak terjangkau: {e}"


def load_queue(path):
    if not os.path.exists(path):
        return []
    points = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                points.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return points


def save_queue(path, points):
    if not points:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for p in points:
            f.write(json.dumps(p) + "\n")
    os.replace(tmp, path)


def send_once(server, device_id, token, queue_path, mock):
    """Flush antrean lama dulu, lalu kirim titik baru. Kembalikan True jika
    titik baru berhasil terkirim (atau tidak ada titik baru)."""
    queued = load_queue(queue_path)
    remaining = []
    for point in queued:
        status, retryable, msg = post_location(server, device_id, token, point)
        log(f"antrean {point.get('latitude')}, {point.get('longitude')}: {msg}")
        if status == 201:
            continue
        if retryable:
            remaining.append(point)
            break  # server/jaringan masih bermasalah, jangan buang antrean
        # 401/404/400: buang titik antrean ini, jangan macet di situ selamanya
        log("titik antrean dibuang (tidak bisa diulang): " + msg)
    save_queue(queue_path, remaining)
    if remaining:
        log(f"antrean tertunda: {len(remaining)} titik (server belum terjangkau).")
        return False

    point = get_location(mock)
    if point is None:
        return False
    point["recorded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    status, retryable, msg = post_location(server, device_id, token, point)
    log(f"kirim {point['latitude']}, {point['longitude']}: {msg}")
    if status == 201:
        return True
    if retryable:
        queued = load_queue(queue_path)
        queued.append(point)
        save_queue(queue_path, queued)
        log(f"disimpan ke antrean offline ({len(queued)} titik).")
    else:
        log("TIDAK diantrekan. Periksa token/perangkat: " + msg)
    return False


def main():
    load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser(description="Pengirim lokasi otomatis (Termux).")
    parser.add_argument("--server", default=os.environ.get("SERVER_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--device-id", default=os.environ.get("DEVICE_ID", ""))
    parser.add_argument("--token", default=os.environ.get("DEVICE_TOKEN", ""))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL_SECONDS", "30")))
    parser.add_argument("--queue-path", default=os.environ.get("QUEUE_PATH", DEFAULT_QUEUE))
    parser.add_argument("--once", action="store_true", help="kirim sekali lalu berhenti")
    parser.add_argument("--mock", action="store_true", help="pakai koordinat uji, tanpa GPS")
    args = parser.parse_args()

    if not args.device_id or not args.token:
        print("DEVICE_ID dan DEVICE_TOKEN harus diisi.", file=sys.stderr)
        print("Isi ~/.kltp-sender.env atau environment DEVICE_ID/DEVICE_TOKEN.", file=sys.stderr)
        print("Token TIDAK boleh ditulis di repository ini.", file=sys.stderr)
        return 2
    if args.interval < 5:
        print("INTERVAL_SECONDS minimal 5 detik (hemat baterai).", file=sys.stderr)
        return 2

    log(f"pengirim jalan: server={args.server} device={args.device_id} interval={args.interval}s")
    log("Tekan Ctrl+C untuk berhenti. Token tidak pernah dicetak ke log.")
    try:
        while True:
            send_once(args.server, args.device_id, args.token, args.queue_path, args.mock)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("berhenti (Ctrl+C). Antrean offline tetap tersimpan bila ada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
