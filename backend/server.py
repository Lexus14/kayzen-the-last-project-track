#!/usr/bin/env python3
"""Kayzen-the-last-project-track: real-time device tracking backend.

Stdlib-only on purpose: it runs on Termux / any machine with Python 3.10+
without installing dependencies.

Tracking rule (important): a location is accepted ONLY from a device that
was registered by its owner and sends its own device token, or from the
device owner / an admin using a user session. This is NOT a lookup by
phone number or national ID, and it never infers a location from those.
"""
import hashlib
import json
import os
import queue
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data", "track.db"))
ONLINE_WINDOW_SECONDS = int(os.environ.get("ONLINE_WINDOW_SECONDS", "300"))
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "24"))

# Live-update subscribers for Server-Sent Events (SSE). Each connected
# browser gets a queue; ingesting a location wakes every queue, so the
# browser refetches the device list immediately instead of polling.
# SSE is used instead of WebSockets here to stay stdlib-only.
_SUBSCRIBERS = set()
_SUB_LOCK = threading.Lock()


def notify_subscribers():
    with _SUB_LOCK:
        for q in list(_SUBSCRIBERS):
            try:
                q.put_nowait("update")
            except queue.Full:
                pass


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    if not value:
        return now_iso()
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("timestamp must be ISO 8601, e.g. 2026-10-26T10:00:00Z")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def hash_secret(secret, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 200_000).hex()
    return salt, digest


def check_secret(secret, salt, digest):
    _, calc = hash_secret(secret, salt)
    return secrets.compare_digest(calc, digest)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def parse_range_bound(value, is_end=False):
    """Accept a date (YYYY-MM-DD) or a full ISO timestamp.

    A date-only `from` means the start of that day (UTC); a date-only `to`
    means the end of that day, so filtering by one calendar day works.
    """
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        day = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        if is_end:
            day = day + timedelta(days=1)
        return day.isoformat().replace("+00:00", "Z")
    return parse_iso(text)


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_salt TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
                accuracy REAL,
                recorded_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT UNIQUE NOT NULL,
                created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                device_name TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def audit(conn, user_id, action, detail=""):
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (user_id, action, detail, now_iso()),
    )


def auth_user_by_token(token):
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?",
            (token_hash(token),),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < now_iso():
            return None
        return dict(row)


def auth_user(headers):
    """Session user from a Bearer header (EventSource can't send headers,
    so the SSE endpoint instead passes the same token as ?token=)."""
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth_user_by_token(auth[7:].strip())


def device_by_token(headers):
    token = headers.get("X-Device-Token", "")
    if not token:
        auth = headers.get("Authorization", "")
        if auth.startswith("Device "):
            token = auth[7:].strip()
    if not token:
        return None
    with connect() as conn:
        for row in conn.execute("SELECT * FROM devices"):
            # Device tokens are stored as PBKDF2 + salt, like passwords.
            if check_secret(token, row["token_salt"], row["token_hash"]):
                return dict(row)
    return None


def latest_location(conn, device_id):
    row = conn.execute(
        "SELECT * FROM locations WHERE device_id = ? ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    return dict(row) if row else None


def invite_status(invite):
    """Single-use invite state: used beats expired, so a redeemed link
    never looks merely expired, and an expired link can never be redeemed."""
    if invite["used_at"]:
        return "used"
    if invite["expires_at"] < now_iso():
        return "expired"
    return "active"


def invite_view(invite):
    return {
        "id": invite["id"],
        "device_name": invite["device_name"],
        "inviter": invite.get("inviter"),
        "status": invite_status(invite),
        "expires_at": invite["expires_at"],
        "used_at": invite["used_at"],
        "device_id": invite["device_id"],
        "created_at": invite["created_at"],
    }


def device_view(conn, device):
    loc = latest_location(conn, device["id"])
    online = False
    if loc:
        try:
            recorded = datetime.fromisoformat(loc["recorded_at"].replace("Z", "+00:00"))
            online = (datetime.now(timezone.utc) - recorded).total_seconds() <= ONLINE_WINDOW_SECONDS
        except ValueError:
            online = False
    return {
        "id": device["id"],
        "name": device["name"],
        "user_id": device["user_id"],
        "created_at": device["created_at"],
        "online": online,
        "last_location": loc,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep logs clean; audit_logs hold app events
        sys.stderr.write("%s - %s\n" % (self.address_string(), args[0] % args[1:]))

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("body must be valid JSON")

    def send_static(self, path):
        safe = os.path.normpath(os.path.join(FRONTEND_DIR, path.lstrip("/")))
        if not safe.startswith(FRONTEND_DIR) or not os.path.isfile(safe):
            return self.send_json(404, {"error": "not found"})
        ctype = "text/html"
        if safe.endswith(".js"):
            ctype = "text/javascript"
        elif safe.endswith(".css"):
            ctype = "text/css"
        data = open(safe, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.send_static("index.html")
        if path in ("/app.js", "/style.css", "/join.js"):
            return self.send_static(path)
        if path == "/join" or path == "/join.html" or path.startswith("/join/"):
            # /join/<code> is a page, not an API route: the join script
            # reads the code from the URL path itself.
            return self.send_static("join.html")
        if path == "/api/health":
            return self.send_json(200, {"status": "ok", "time": now_iso()})
        if path == "/api/devices/stream":
            # EventSource cannot send an Authorization header, so the
            # session token is passed as ?token= (same token as login).
            # The token is checked here and never written to audit logs.
            user = auth_user_by_token(parse_qs(parsed.query).get("token", [""])[0])
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            q = queue.Queue(maxsize=10)
            with _SUB_LOCK:
                _SUBSCRIBERS.add(q)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps({'type': 'connected', 'time': now_iso()})}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        q.get(timeout=15)
                        payload = {"type": "update", "time": now_iso()}
                    except queue.Empty:
                        payload = {"type": "heartbeat", "time": now_iso()}
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with _SUB_LOCK:
                    _SUBSCRIBERS.discard(q)
            return None
        if path == "/api/auth/me":
            user = auth_user(self.headers)
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            return self.send_json(200, {"id": user["id"], "username": user["username"], "role": user["role"]})
        if path == "/api/invites":
            user = auth_user(self.headers)
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            with connect() as conn:
                sql = ("SELECT i.*, u.username AS inviter FROM invites i "
                       "JOIN users u ON u.id = i.created_by ")
                if user["role"] == "admin":
                    rows = conn.execute(sql + "ORDER BY i.id DESC").fetchall()
                else:
                    rows = conn.execute(sql + "WHERE i.created_by = ? ORDER BY i.id DESC", (user["id"],)).fetchall()
                return self.send_json(200, {"invites": [invite_view(dict(r)) for r in rows]})
        if path.startswith("/api/invites/") and path.endswith("/info"):
            try:
                code = path.split("/")[3]
            except IndexError:
                return self.send_json(400, {"error": "invalid invite code"})
            with connect() as conn:
                row = conn.execute(
                    "SELECT i.*, u.username AS inviter FROM invites i "
                    "JOIN users u ON u.id = i.created_by WHERE i.code_hash = ?",
                    (token_hash(code),),
                ).fetchone()
                if not row:
                    return self.send_json(404, {"error": "invite not found"})
                return self.send_json(200, invite_view(dict(row)))
        if path == "/api/devices":
            user = auth_user(self.headers)
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            with connect() as conn:
                if user["role"] == "admin":
                    rows = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
                else:
                    rows = conn.execute("SELECT * FROM devices WHERE user_id = ? ORDER BY id", (user["id"],)).fetchall()
                return self.send_json(200, {"devices": [device_view(conn, dict(r)) for r in rows]})
        if path.startswith("/api/devices/") and path.endswith("/locations"):
            user = auth_user(self.headers)
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            try:
                device_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self.send_json(400, {"error": "invalid device id"})
            qs = parse_qs(parsed.query)
            limit = int(qs.get("limit", ["100"])[0])
            limit = max(1, min(limit, 500))
            try:
                date_from = parse_range_bound(qs.get("from", [""])[0], is_end=False)
                date_to = parse_range_bound(qs.get("to", [""])[0], is_end=True)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})
            with connect() as conn:
                dev = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
                if not dev:
                    return self.send_json(404, {"error": "device not found"})
                if user["role"] != "admin" and dev["user_id"] != user["id"]:
                    return self.send_json(403, {"error": "forbidden"})
                # Oldest first: a route reads naturally in time order.
                sql = "SELECT * FROM locations WHERE device_id = ?"
                params = [device_id]
                if date_from:
                    sql += " AND recorded_at >= ?"
                    params.append(date_from)
                if date_to:
                    sql += " AND recorded_at < ?"
                    params.append(date_to)
                sql += " ORDER BY recorded_at ASC, id ASC LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
                return self.send_json(200, {"locations": [dict(r) for r in rows]})
        return self.send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self.read_json()
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})

        if path == "/api/auth/register":
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            if len(username) < 3 or len(password) < 8:
                return self.send_json(400, {"error": "username min 3 chars, password min 8 chars"})
            with connect() as conn:
                count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
                role = "admin" if count == 0 else "user"
                salt, digest = hash_secret(password)
                try:
                    cur = conn.execute(
                        "INSERT INTO users (username, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                        (username, salt, digest, role, now_iso()),
                    )
                    audit(conn, cur.lastrowid, "register", f"role={role}")
                    conn.commit()
                except sqlite3.IntegrityError:
                    return self.send_json(409, {"error": "username already exists"})
                return self.send_json(201, {"id": cur.lastrowid, "username": username, "role": role})

        if path == "/api/auth/login":
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            with connect() as conn:
                row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                if not row or not check_secret(password, row["password_salt"], row["password_hash"]):
                    return self.send_json(401, {"error": "invalid username or password"})
                token = secrets.token_urlsafe(32)
                expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat().replace("+00:00", "Z")
                conn.execute(
                    "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (token_hash(token), row["id"], expires, now_iso()),
                )
                audit(conn, row["id"], "login")
                conn.commit()
                return self.send_json(200, {"token": token, "username": row["username"], "role": row["role"]})

        if path == "/api/invites":
            user = auth_user(self.headers)
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            name = str(body.get("device_name", "")).strip() or "Perangkat undangan"
            code = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
            with connect() as conn:
                cur = conn.execute(
                    "INSERT INTO invites (code_hash, created_by, device_name, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (token_hash(code), user["id"], name, expires, now_iso()),
                )
                audit(conn, user["id"], "invite_create", f"invite_id={cur.lastrowid}")
                conn.commit()
                return self.send_json(201, {
                    "id": cur.lastrowid, "device_name": name,
                    "code": code, "join_path": "/join/" + code,
                    "expires_at": expires,
                    "note": "Link ini sekali pakai dan berlaku 24 jam. Kode hanya ditampilkan sekali.",
                })

        if path.startswith("/api/invites/") and path.endswith("/redeem"):
            try:
                code = path.split("/")[3]
            except IndexError:
                return self.send_json(400, {"error": "invalid invite code"})
            with connect() as conn:
                row = conn.execute("SELECT * FROM invites WHERE code_hash = ?", (token_hash(code),)).fetchone()
                if not row:
                    return self.send_json(404, {"error": "invite not found"})
                invite = dict(row)
                if invite["used_at"]:
                    return self.send_json(410, {"error": "invite already used"})
                if invite["expires_at"] < now_iso():
                    return self.send_json(410, {"error": "invite expired"})
                name = str(body.get("device_name", "")).strip() or invite["device_name"]
                device_token = secrets.token_urlsafe(32)
                salt, digest = hash_secret(device_token)
                cur = conn.execute(
                    "INSERT INTO devices (user_id, name, token_salt, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (invite["created_by"], name, salt, digest, now_iso()),
                )
                # Claim the invite only if it is still unused, so two
                # simultaneous redeems cannot both succeed.
                claimed = conn.execute(
                    "UPDATE invites SET used_at = ?, device_id = ? WHERE id = ? AND used_at IS NULL",
                    (now_iso(), cur.lastrowid, invite["id"]),
                )
                if claimed.rowcount != 1:
                    conn.rollback()
                    return self.send_json(410, {"error": "invite already used"})
                audit(conn, invite["created_by"], "invite_redeem", f"invite_id={invite['id']} device_id={cur.lastrowid}")
                conn.commit()
                return self.send_json(201, {
                    "device_id": cur.lastrowid, "device_name": name,
                    "device_token": device_token,
                    "note": "Token perangkat ini disimpan otomatis di browser ini. Jangan bagikan link undangan yang sudah dipakai.",
                })

        if path == "/api/devices":
            user = auth_user(self.headers)
            if not user:
                return self.send_json(401, {"error": "unauthorized"})
            name = str(body.get("name", "")).strip()
            if not name:
                return self.send_json(400, {"error": "name is required"})
            device_token = secrets.token_urlsafe(32)
            salt, digest = hash_secret(device_token)
            with connect() as conn:
                cur = conn.execute(
                    "INSERT INTO devices (user_id, name, token_salt, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], name, salt, digest, now_iso()),
                )
                audit(conn, user["id"], "device_create", f"device_id={cur.lastrowid}")
                conn.commit()
                return self.send_json(201, {
                    "id": cur.lastrowid, "name": name,
                    "device_token": device_token,
                    "note": "Save this token now. It is shown only once.",
                })

        if path.startswith("/api/devices/") and path.endswith("/locations"):
            try:
                device_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self.send_json(400, {"error": "invalid device id"})
            user = auth_user(self.headers)
            device = device_by_token(self.headers)
            with connect() as conn:
                dev = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
                if not dev:
                    return self.send_json(404, {"error": "device not found"})
                allowed = False
                actor = None
                if device and device["id"] == device_id:
                    allowed = True
                elif user and (user["role"] == "admin" or dev["user_id"] == user["id"]):
                    allowed = True
                    actor = user["id"]
                if not allowed:
                    return self.send_json(401, {"error": "unauthorized: device token or owner session required"})
                try:
                    lat = float(body["latitude"])
                    lon = float(body["longitude"])
                    recorded = parse_iso(body.get("timestamp"))
                except (KeyError, TypeError, ValueError) as exc:
                    return self.send_json(400, {"error": f"invalid location: {exc}"})
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    return self.send_json(400, {"error": "latitude/longitude out of range"})
                accuracy = body.get("accuracy")
                if accuracy is not None:
                    try:
                        accuracy = float(accuracy)
                    except (TypeError, ValueError):
                        return self.send_json(400, {"error": "accuracy must be a number"})
                cur = conn.execute(
                    "INSERT INTO locations (device_id, latitude, longitude, accuracy, recorded_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (device_id, lat, lon, accuracy, recorded, now_iso()),
                )
                audit(conn, actor, "location_ingest", f"device_id={device_id}")
                conn.commit()
                notify_subscribers()
                return self.send_json(201, {"id": cur.lastrowid, "device_id": device_id,
                                            "latitude": lat, "longitude": lon,
                                            "accuracy": accuracy, "recorded_at": recorded})
        return self.send_json(404, {"error": "not found"})


if __name__ == "__main__":
    init_db()
    print(f"Kayzen-the-last-project-track listening on http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
