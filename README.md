# Kayzen-the-last-project-track

Tracking real-time untuk perangkat yang **memberi izin**.

Perangkat yang terdaftar mengirim GPS-nya sendiri secara berkala ke server.
Server menyimpan koordinat + timestamp, lalu menampilkannya di peta dengan
status perangkat, lokasi terakhir, timestamp, dan status online/offline.

**Bukan** alat untuk mengetahui lokasi seseorang hanya dari nomor HP/NIK.
Validasi nomor tidak sama dengan pelacakan GPS real-time, dan lokasi tidak
pernah disimpulkan dari nomor HP/NIK tanpa provider resmi dan otorisasi.

## Menjalankan (lokal / Termux)

Tidak perlu install dependency — backend memakai Python standar saja.

```bash
python3 backend/server.py
```

Lalu buka: http://127.0.0.1:8000

Variabel lingkungan (lihat `.env.example`):

| Variabel | Default | Keterangan |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Alamat listen |
| `PORT` | `8000` | Port server |
| `DATABASE_PATH` | `data/track.db` | Lokasi database SQLite |
| `ONLINE_WINDOW_SECONDS` | `300` | Perangkat dianggap online jika lokasi terakhir dalam 5 menit |
| `SESSION_HOURS` | `24` | Masa berlaku sesi login |

## Alur pakai

1. Daftar akun di halaman web. Akun pertama yang terdaftar menjadi `admin`,
   akun berikutnya menjadi `user` biasa.
2. Masuk, lalu daftarkan perangkat. **Simpan token perangkat yang muncul
   sekali itu** — token tidak ditampilkan lagi.
3. Perangkat mengirim lokasi dengan token itu:

```bash
curl -X POST http://127.0.0.1:8000/api/devices/1/locations \
  -H "X-Device-Token: TOKEN_PERANGKAT" \
  -H "Content-Type: application/json" \
  -d '{"latitude": -3.123, "longitude": 102.123}'
```

4. Lihat perangkat dan peta di halaman web. Peta memuat ulang tiap 15 detik.

## API

- `GET /api/health`
- `POST /api/auth/register` — `{username, password}` (password min 8 karakter)
- `POST /api/auth/login` — mengembalikan token sesi (`Authorization: Bearer <token>`)
- `GET /api/auth/me`
- `POST /api/devices` — butuh sesi login, mengembalikan token perangkat sekali pakai
- `GET /api/devices` — admin melihat semua perangkat, user hanya miliknya
- `GET /api/devices/{id}/locations?limit=100`
- `POST /api/devices/{id}/locations` — butuh `X-Device-Token` atau sesi pemilik/admin

## Struktur

```
backend/server.py   Backend REST (Python stdlib) + SQLite
frontend/           Halaman peta (Leaflet + OpenStreetMap)
data/               Database SQLite (tidak di-commit, lihat .gitignore)
tests/test_api.py   Tes end-to-end terhadap server yang sedang berjalan
```

## Keamanan

- Password disimpan sebagai hash PBKDF2 + salt, bukan plaintext.
- Token perangkat disimpan sebagai hash dan hanya ditampilkan sekali.
- Jangan commit file `.env` atau database. Gunakan `.env.example` sebagai contoh.
- Perintah berisiko (hapus data, force push) selalu cek dulu: `git status`,
  `git remote -v`, `git branch`, `git log --oneline -n 5`.

## Rencana berikutnya

- WebSocket/SSE untuk update peta benar-benar real-time (saat ini polling 15 detik).
- PostgreSQL sebagai opsi database selain SQLite.
- Aplikasi / halaman kirim lokasi di perangkat, bukan hanya form uji.
