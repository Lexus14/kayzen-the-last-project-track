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

## Update dan jalankan ulang (Termux)

Kalau server lama sudah ditutup, atau setelah ada commit baru di GitHub,
jalankan dari folder project yang benar (bukan dari home `~` saja):

```bash
cd ~/kayzen-the-last-project-track
git pull
python3 backend/server.py
```

Catatan:
- Kalau server lama masih jalan di sesi lain, hentikan dulu (Ctrl+C)
  sebelum menjalankan perintah di atas, supaya port 8000 tidak bentrok
  (`Address already in use`).
- Data akun dan perangkat tidak hilang saat restart, karena tersimpan di
  `data/track.db` di folder project yang sama.

## Alur pakai

1. Daftar akun di halaman web. Akun pertama yang terdaftar menjadi `admin`,
   akun berikutnya menjadi `user` biasa. Setelah daftar, kamu langsung
   masuk otomatis — tombolnya menampilkan "Memproses…" selama request
   berjalan, jadi tidak terlihat seperti tidak terjadi apa-apa.
2. Masuk, lalu daftarkan perangkat. **Simpan token perangkat yang muncul
   sekali itu** — token tidak ditampilkan lagi.
3. Perangkat mengirim lokasi dengan token itu:

```bash
curl -X POST http://127.0.0.1:8000/api/devices/1/locations \
  -H "X-Device-Token: TOKEN_PERANGKAT" \
  -H "Content-Type: application/json" \
  -d '{"latitude": -3.123, "longitude": 102.123}'
```

4. Lihat perangkat dan peta di halaman web. Peta diperbarui secara live:
   server mendorong event SSE setiap ada lokasi baru, dan browser langsung
   memuat ulang daftar perangkat (bukan polling tiap 15 detik).
5. Untuk melihat perjalanan: di bagian Riwayat & Rute, pilih perangkat dan
   tanggal, klik Tampilkan rute. Rute digambar sebagai garis di peta,
   lengkap dengan jarak total, lalu bisa digeser atau diputar ulang
   titik demi titik.

## Pengirim lokasi otomatis (Termux)

Daripada isi form uji manual, perangkat bisa mengirim GPS-nya sendiri
dengan izin pengguna, memakai Termux:API. Skripnya stdlib saja:
`tools/send_location.py`. Token perangkat **tidak** ditulis di repository
— token hanya disimpan di file lokal `~/.kltp-sender.env` di HP.

```bash
# 1. Di Termux, pasang aplikasi Termux:API + paketnya, beri izin lokasi,
#    lalu uji GPS sekali:
pkg install -y termux-api
termux-location -p gps -r once

# 2. Buat file konfigurasi lokal (jangan di-commit, jangan dibagikan):
cp tools/sender.env.example ~/.kltp-sender.env
nano ~/.kltp-sender.env
#    isi SERVER_URL, DEVICE_ID, DEVICE_TOKEN, INTERVAL_SECONDS
#    SERVER_URL bisa http://127.0.0.1:8000 atau URL Cloudflare Tunnel

# 3. Jalankan di sesi Termux sendiri (server tetap jalan di sesi lain):
python3 tools/send_location.py
#    Sekali kirim saja (uji):  python3 tools/send_location.py --once
#    Uji logika tanpa GPS:     python3 tools/send_location.py --once --mock
```

Perilaku penting:
- Interval default 30 detik, minimal 5 detik (hemat baterai).
- Kalau jaringan/server tidak terjangkau, titik disimpan ke antrean
  offline (`~/.cache/kltp_sender_queue.jsonl`) dan dikirim ulang duluan
  pada pengiriman berikutnya, sesuai urutan waktu.
- Error token salah / perangkat tidak ada (401/404/400) tidak diantrekan,
  karena mengulanginya tidak akan membantu — periksa token/perangkatnya.
- Token tidak pernah dicetak ke log.
- Tekan Ctrl+C untuk berhenti; antrean offline tetap tersimpan.

## Link undangan sekali pakai (tanpa daftar akun / salin token)

Cara paling tidak ribet untuk perangkat keluarga:

1. Login sebagai admin (atau user), lalu di bagian “Link undangan
   (sekali pakai)” isi nama perangkat, mis. `HP Ibu`, dan tekan
   “Buat link undangan”. Link yang muncul hanya ditampilkan sekali —
   salin dan kirim secara pribadi ke pemilik perangkat itu.
2. Pemilik perangkat membuka link itu di browser HP-nya (harus lewat
   HTTPS, mis. alamat tunnel permanen, atau localhost untuk pengujian).
   Halaman join menjelaskan siapa yang akan melihat lokasi, lalu ia
   menekan “Saya Izinkan”.
3. Server otomatis membuat perangkat milik pembuat undangan dan
   menyimpan token perangkat itu di browser tersebut — tidak ada
   pendaftaran akun, tidak ada penyalinan token, tidak ada Termux.
4. Browser meminta izin lokasi secara terpisah. Lokasi dikirim kira-kira
   tiap 30 detik **selama halaman join tetap terbuka**. Menutup halaman
   atau menekan “Berhenti berbagi” menghentikan pengiriman.

Aturan dan batasnya, sengaja dibuat ketat:
- Satu link hanya bisa dipakai **sekali**, dan berlaku **24 jam**.
- Kode undangan disimpan sebagai hash SHA-256 di server, bukan plaintext,
  dan tidak pernah ditampilkan lagi di daftar undangan.
- Link yang sudah dipakai atau kadaluarsa ditolak dengan 410.
- Geolocation browser tidak bisa berjalan di latar dari tab yang
  tertutup, jadi link undangan **bukan** pelacakan 24 jam nonstop.
  Untuk pengiriman latar yang terus berjalan, pakai pengirim Termux di
  atas atau location sharing bawaan (Google Maps / Find My).
- Jangan posting link undangan secara publik: siapa pun yang memegangnya
  sebelum dipakai bisa mengklaimnya.

API undangan:
- `POST /api/invites` — butuh sesi login, mengembalikan kode + `join_path` sekali pakai
- `GET /api/invites` — daftar undangan milikmu (admin: semua), tanpa kode
- `GET /api/invites/{kode}/info` — publik, hanya nama perangkat, pengundang, dan status
- `POST /api/invites/{kode}/redeem` — publik, sekali pakai, membuat perangkat dan mengembalikan token perangkat

## Tunnel permanen (Named Tunnel, Cloudflare)

Quick Tunnel (`cloudflared tunnel --url http://127.0.0.1:8000`) itu
sementara: URL-nya berubah tiap restart dan tidak ada garansi uptime.
Tunnel permanen memakai nama tetap dan hostname tetap, tapi butuh satu
prasyarat: **sebuah domain yang sudah ditambahkan ke akun Cloudflare dan
DNS-nya dikelola Cloudflare**. Tanpa domain, langkah `route dns` tidak
bisa membuat hostname publik.

Template konfigurasinya ada di `tools/cloudflared-config.example.yml`.
Langkahnya, dengan server tetap jalan di sesi lain:

```bash
# 1. Hentikan Quick Tunnel dulu (Ctrl+C di sesinya) supaya tidak bingung.
# 2. Login cloudflared (membuka browser untuk otorisasi akun):
cloudflared tunnel login
#    File cert.pem tersimpan di ~/.cloudflared/ — jangan dibagikan.

# 3. Buat tunnel bernama, mis. kayzen-track:
cloudflared tunnel create kayzen-track
#    Catat Tunnel UUID yang muncul; file credentials JSON-nya ada di
#    ~/.cloudflared/<TUNNEL-UUID>.json — jangan dibagikan.

# 4. Salin template dan isi UUID + hostname:
cp tools/cloudflared-config.example.yml ~/.cloudflared/config.yml
nano ~/.cloudflared/config.yml

# 5. Buat DNS-nya (membuat CNAME ke <UUID>.cfargotunnel.com):
cloudflared tunnel route dns kayzen-track track.domain-kamu.com

# 6. Jalankan tunnel permanen (biarkan sesi ini jalan):
cloudflared tunnel run kayzen-track
```

Buka `https://track.domain-kamu.com` di browser, lalu login lagi dengan
akun yang sama (token login localhost / Quick Tunnel tidak terbawa ke
hostname baru). Setiap restart, jalankan lagi langkah 6 dengan nama yang
sama — hostname-nya tetap, tidak berubah seperti Quick Tunnel.

## API

- `GET /api/health`
- `POST /api/auth/register` — `{username, password}` (password min 8 karakter)
- `POST /api/auth/login` — mengembalikan token sesi (`Authorization: Bearer <token>`)
- `GET /api/auth/me`
- `POST /api/devices` — butuh sesi login, mengembalikan token perangkat sekali pakai
- `GET /api/devices` — admin melihat semua perangkat, user hanya miliknya
- `GET /api/devices/{id}/locations?limit=100&from=2026-10-26&to=2026-10-26`
  — riwayat urut waktu menaik; `from`/`to` bisa tanggal (`YYYY-MM-DD`)
  atau timestamp ISO penuh
- `POST /api/devices/{id}/locations` — butuh `X-Device-Token` atau sesi pemilik/admin
- `GET /api/devices/stream?token=<token_sesi>` — Server-Sent Events; server
  mengirim event `update` setiap ada lokasi baru (dan heartbeat tiap 15 detik)
- Undangan sekali pakai: `POST /api/invites`, `GET /api/invites`,
  `GET /api/invites/{kode}/info`, `POST /api/invites/{kode}/redeem`
  (lihat bagian Link undangan di atas)

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

- PostgreSQL sebagai opsi database selain SQLite.
- WebSocket sebagai alternatif SSE bila dibutuhkan komunikasi dua arah.
- Manajemen perangkat: hapus perangkat, cabut/putar token perangkat, dan
  logout server-side yang benar-benar menghapus sesi.
