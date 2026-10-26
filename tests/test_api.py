#!/usr/bin/env python3
"""End-to-end smoke test against a running server.

Usage:
    python3 backend/server.py &   # in one terminal
    BASE_URL=http://127.0.0.1:8000 python3 tests/test_api.py
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main():
    status, health = call("GET", "/api/health")
    assert status == 200 and health["status"] == "ok", health

    status, reg = call("POST", "/api/auth/register", {"username": "tester", "password": "password123"})
    assert status in (201, 409), reg  # 409 if the test user already exists

    status, login = call("POST", "/api/auth/login", {"username": "tester", "password": "password123"})
    assert status == 200, login
    auth = {"Authorization": "Bearer " + login["token"]}

    status, dev = call("POST", "/api/devices", {"name": "HP Uji"}, auth)
    assert status == 201, dev

    status, loc = call(
        "POST", f"/api/devices/{dev['id']}/locations",
        {"latitude": -3.123, "longitude": 102.123},
        {"X-Device-Token": dev["device_token"]},
    )
    assert status == 201, loc

    status, devices = call("GET", "/api/devices", headers=auth)
    assert status == 200 and devices["devices"], devices
    assert devices["devices"][0]["last_location"] is not None, devices

    status, bad = call(
        "POST", f"/api/devices/{dev['id']}/locations",
        {"latitude": 999, "longitude": 0},
        {"X-Device-Token": dev["device_token"]},
    )
    assert status == 400, bad  # out-of-range coordinates are rejected

    status, unauth = call(
        "POST", f"/api/devices/{dev['id']}/locations",
        {"latitude": -3.0, "longitude": 102.0},
        {"X-Device-Token": "wrong-token"},
    )
    assert status == 401, unauth  # no device token, no location

    print("All smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
