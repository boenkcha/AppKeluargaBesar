# Silsilah Keluarga Besar

Aplikasi web untuk mencatat, menelusuri, dan merawat silsilah keluarga besar — dibuat dengan Flask + SQLite, tampilan pastel yang ramah ponsel (bisa dipasang di layar utama seperti aplikasi).

## Fitur utama

- **Biodata anggota** — nama, jenis kelamin, tanggal/tahun lahir, anak ke-, alamat, WA, pekerjaan, ayah, ibu, foto (bisa digeser & diperbesar dalam lingkaran, dikompres otomatis).
- **Pendaftaran dengan persetujuan admin** dan aktivasi lewat email.
- **Pohon keluarga** leluhur & keturunan (zoom, geser, pinch-zoom), lengkap dengan saudara kandung, pasangan, dan label Ayah/Ibu/Kakek/Nenek.
- **Telusuri hubungan** dua orang: hubungan darah (paman, sepupu dua kali, buyut…), pernikahan (mertua, ipar, besan, ibu sambung, mantan), hingga kerabat jauh lewat jembatan pernikahan, dengan peta jalur bertingkat generasi dan saran sapaan.
- **Riwayat pernikahan** — istri/suami ke-1, ke-2, status menikah/bercerai/berpisah karena wafat.
- **Pencocokan nama cerdas** — gelar & ejaan lama diabaikan, saran "apakah maksud Anda…?", tersambung otomatis bila sangat pasti, deteksi & penggabungan data kembar.
- **Hak kelola** — peran *Anggota / Head Family / Super Family / Admin*, prioritas kerabat terdekat, usulan perubahan, dan riwayat perubahan.
- **Cerita keluarga (mukadimah silsilah)** per garis leluhur, bercabang, tampil di beranda.
- **Antar-silsilah** — tautan ke server silsilah lain dan akses *tamu penjelajah* (baca-saja) dengan persetujuan admin.
- **Mengenang** — tema khusus profil yang telah berpulang: karangan bunga, tabur bunga, buku kenangan & doa, peringatan berpulang.
- **Ulang tahun** — ucapan rahasia yang menjadi kejutan (tiup lilin, balon, konfeti), profil bersuasana pesta selama sepekan, tombol *Rayakan*, topi pesta di pohon.

## Menjalankan

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py            # buka http://localhost:5050
```

**Pendaftar pertama otomatis menjadi admin.** Database (`silsilah.db`), folder foto (`uploads/`), dan kunci sesi (`.secret_key`) dibuat otomatis saat pertama dijalankan, dan **tidak ikut** ke repositori.

## Konfigurasi (variabel lingkungan)

| Variabel | Default | Keterangan |
|---|---|---|
| `SECRET_KEY` | dibuat otomatis di `.secret_key` | kunci sesi & token aktivasi |
| `DB_PATH` | `silsilah.db` | lokasi database SQLite |
| `UPLOAD_DIR` | `uploads/foto` | lokasi penyimpanan foto |
| `PORT` | `5050` | port server |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM` | – | pengiriman email aktivasi & pemberitahuan |

Tanpa SMTP, email dicetak di konsol dan tautan aktivasi ditampilkan di halaman Admin.

Contoh Gmail (gunakan *App Password*):

```bash
export SMTP_HOST=smtp.gmail.com SMTP_PORT=587
export SMTP_USER=alamat@gmail.com SMTP_PASS=app-password-16-digit
export MAIL_FROM="Silsilah Keluarga Besar <alamat@gmail.com>"
```

## Memasang di PythonAnywhere

1. **Bash console:** `git clone https://github.com/boenkcha/AppKeluargaBesar.git`
   lalu `mkvirtualenv --python=python3.11 silsilah` dan `pip install -r AppKeluargaBesar/requirements.txt`.
2. **Tab Web → Add a new web app → Manual configuration → Python 3.11.**
3. **Source code:** `/home/<username>/AppKeluargaBesar` · **Virtualenv:** `/home/<username>/.virtualenvs/silsilah`.
4. **WSGI configuration file:** ganti isinya dengan `pythonanywhere_wsgi.py` (sesuaikan `USERNAME`).
5. **Static files:** URL `/static/` → `/home/<username>/AppKeluargaBesar/static`.
6. Aktifkan **Force HTTPS**, lalu **Reload**.
7. Pembaruan: `cd ~/AppKeluargaBesar && git pull`, lalu **Reload** di tab Web.

Akun gratis hanya dapat mengirim email lewat Gmail (`smtp.gmail.com`) dan web app perlu diperpanjang tiap 3 bulan.

## Catatan keamanan & privasi

- Jangan unggah `silsilah.db`, salinan cadangannya, folder `uploads/`, maupun `.secret_key` — semuanya sudah dikecualikan di `.gitignore`.
- Saat dipasang online gunakan **HTTPS** dan domain yang benar agar link aktivasi & link tamu memakai alamat publik.
- Link tamu bersifat rahasia; cabut dari menu *Admin → Akses Tamu* bila bocor.

## Struktur

```
app.py        rute, hak akses, penyimpanan
family.py     graf silsilah, istilah kekerabatan, jangkauan peran
matching.py   pencocokan nama & prediksi hubungan
templates/    tampilan (Jinja2)
static/       CSS, JS, ikon, Bootstrap lokal
```

© Copyright by UCHAHUNT - 2026
