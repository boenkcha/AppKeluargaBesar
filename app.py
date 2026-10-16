import json
import os
import re
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, ImageOps
from werkzeug.security import check_password_hash, generate_password_hash

from family import Family, TINGKAT
import matching

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "silsilah.db"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads", "foto"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

APP_NAME = "Silsilah Keluarga Besar"
APP_SHORT = "Silsilah"        # nama pendek untuk ikon di layar utama HP
FOTO_SIZE = 480            # px, persegi
FOTO_QUALITY = 80          # hasil ±30–60 KB per foto
Image.MAX_IMAGE_PIXELS = 40_000_000


def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE_DIR, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app = Flask(__name__)
app.secret_key = _secret_key()
# di belakang proxy (PythonAnywhere, Nginx): pakai skema & host asli agar link yang dibuat memakai https://
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
serializer = URLSafeTimedSerializer(app.secret_key)

# === DB SETUP ===
SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    jenis_kelamin TEXT CHECK (jenis_kelamin IN ('L','P')),
    tahun_lahir INTEGER,
    tanggal_lahir TEXT,                       -- YYYY-MM-DD; tahun_lahir tetap diisi (dari tanggal atau perkiraan)
    urutan_lahir INTEGER,                     -- anak ke-berapa (urutan saudara bila tanggal tak diketahui)
    alamat TEXT,
    wa TEXT,
    pekerjaan TEXT,
    bio TEXT,
    ayah_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    ibu_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    pasangan_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    foto TEXT,
    wafat INTEGER NOT NULL DEFAULT 0,
    tampil_kontak INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_persons_ayah ON persons(ayah_id);

CREATE TABLE IF NOT EXISTS ucapan (        -- ucapan ulang tahun (dirahasiakan sampai harinya tiba)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id INTEGER,
    tahun INTEGER NOT NULL,        -- tahun ulang tahun yang dirayakan
    stiker TEXT,
    pesan TEXT NOT NULL,
    dibaca INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS rayakan (       -- tombol "Rayakan" saat pekan ulang tahun
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    tahun INTEGER NOT NULL,
    tanggal TEXT NOT NULL,
    PRIMARY KEY (person_id, user_id, tanggal)
);

CREATE TABLE IF NOT EXISTS kenangan (      -- buku kenangan & doa untuk yang telah berpulang
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id INTEGER,
    pesan TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS tabur_bunga (
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    tanggal TEXT NOT NULL,
    PRIMARY KEY (person_id, user_id, tanggal)
);

CREATE TABLE IF NOT EXISTS tautan_luar (   -- tautan ke orang yang sama di server silsilah lain
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    url TEXT NOT NULL,
    catatan TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS akses_tamu (    -- akses baca-saja untuk penjelajah dari silsilah lain
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    titik_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    token TEXT NOT NULL UNIQUE,
    asal TEXT,
    catatan TEXT,
    berlaku_sampai TEXT,
    aktif INTEGER NOT NULL DEFAULT 1,
    dibuat_oleh INTEGER,
    terakhir TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS permintaan_tamu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    email TEXT,
    asal TEXT,
    tujuan TEXT,
    alasan TEXT,
    status TEXT NOT NULL DEFAULT 'menunggu',
    akses_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS keluarga (     -- cerita / mukadimah sebuah keluarga besar
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    judul TEXT NOT NULL,
    tagline TEXT,
    asal TEXT,
    leluhur_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    mukadimah TEXT,
    kutipan TEXT,
    kutipan_oleh TEXT,
    tonggak TEXT,                  -- satu peristiwa per baris: "1925 — ..."
    sampul TEXT,
    created_by INTEGER,
    updated_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS usulan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    data TEXT NOT NULL,            -- isian form (JSON)
    ringkasan TEXT NOT NULL,       -- daftar perubahan (JSON)
    status TEXT NOT NULL DEFAULT 'menunggu',   -- menunggu | diterima | ditolak
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    decided_by INTEGER,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS riwayat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    user_id INTEGER,
    waktu TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ringkasan TEXT NOT NULL,       -- daftar perubahan (JSON)
    catatan TEXT
);

CREATE TABLE IF NOT EXISTS pernikahan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    orang1_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,   -- id terkecil
    orang2_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'menikah',      -- menikah | cerai | wafat
    tanggal_nikah TEXT,
    tanggal_selesai TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (orang1_id, orang2_id)
);
CREATE INDEX IF NOT EXISTS ix_persons_ibu ON persons(ibu_id);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    nama TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | active | rejected | disabled
    is_admin INTEGER NOT NULL DEFAULT 0,
    person_id INTEGER UNIQUE REFERENCES persons(id) ON DELETE SET NULL,
    catatan_daftar TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    approved_at TEXT,
    last_login TEXT
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(persons)")}
        if "tanggal_lahir" not in cols:  # migrasi database lama
            conn.execute("ALTER TABLE persons ADD COLUMN tanggal_lahir TEXT")
        if "urutan_lahir" not in cols:
            conn.execute("ALTER TABLE persons ADD COLUMN urutan_lahir INTEGER")
        for tabel in ("akses_tamu", "permintaan_tamu"):
            tcols = {r[1] for r in conn.execute(f"PRAGMA table_info({tabel})")}
            if "link_asal" not in tcols:   # link ke silsilah asal tamu (tautan balik)
                conn.execute(f"ALTER TABLE {tabel} ADD COLUMN link_asal TEXT")
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "wajib_ganti" not in ucols:   # akun dibuat admin: wajib ganti kata sandi sementara
            conn.execute("ALTER TABLE users ADD COLUMN wajib_ganti INTEGER NOT NULL DEFAULT 0")
        if "peran" not in ucols:   # anggota | head | super
            conn.execute("ALTER TABLE users ADD COLUMN peran TEXT NOT NULL DEFAULT 'anggota'")
        if "email" not in cols:   # email di biodata (dipakai juga untuk memberi akses akun)
            conn.execute("ALTER TABLE persons ADD COLUMN email TEXT")
        for kol, tipe in (("tanggal_wafat", "TEXT"), ("tahun_wafat", "INTEGER"), ("makam", "TEXT")):
            if kol not in cols:   # data kenangan untuk yang telah berpulang
                conn.execute(f"ALTER TABLE persons ADD COLUMN {kol} {tipe}")
        if "kelola_tier" not in cols:   # hak kelola: tingkat kedekatan pengubah terdekat & siapa orangnya
            conn.execute("ALTER TABLE persons ADD COLUMN kelola_tier INTEGER")
            conn.execute("ALTER TABLE persons ADD COLUMN kelola_user INTEGER")
        # migrasi: kolom pasangan_id lama & pasangan dari anak bersama -> tabel riwayat pernikahan
        pairs = conn.execute(
            "SELECT id, pasangan_id FROM persons WHERE pasangan_id IS NOT NULL "
            "UNION SELECT ayah_id, ibu_id FROM persons WHERE ayah_id IS NOT NULL AND ibu_id IS NOT NULL").fetchall()
        for a, b in pairs:
            if a != b:
                conn.execute("INSERT OR IGNORE INTO pernikahan (orang1_id, orang2_id) "
                             "SELECT ?, ? WHERE EXISTS (SELECT 1 FROM persons WHERE id=?) "
                             "AND EXISTS (SELECT 1 FROM persons WHERE id=?)", (min(a, b), max(a, b), a, b))
        conn.execute("UPDATE persons SET pasangan_id=NULL WHERE pasangan_id IS NOT NULL")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def family():
    if "family" not in g:
        db = get_db()
        g.family = Family(db.execute("SELECT * FROM persons").fetchall(),
                          db.execute("SELECT * FROM pernikahan").fetchall())
    return g.family


def claimed_ids():
    if "claimed" not in g:
        g.claimed = {r[0] for r in get_db().execute(
            "SELECT person_id FROM users WHERE person_id IS NOT NULL")}
    return g.claimed


# === EMAIL ===
def _plain(html):
    text = re.sub(r'<a href="([^"]+)"[^>]*>([^<]*)</a>', r"\2: \1", html)
    text = re.sub(r"<br\s*/?>|</(p|h\d|div)>", "\n", text)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"<[^>]+>", "", text)).strip()


def send_mail(to, subject, html):
    """Kirim email via SMTP (env SMTP_*). Tanpa konfigurasi, isi dicetak ke konsol."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"\n[EMAIL — SMTP belum diatur]\nKepada : {to}\nPerihal: {subject}\n{_plain(html)}\n")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM", os.environ.get("SMTP_USER", f"no-reply@{host}"))
    msg["To"] = to
    msg.set_content(_plain(html))
    msg.add_alternative(html, subtype="html")
    try:
        port = int(os.environ.get("SMTP_PORT", 587))
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
            smtp.starttls()
        with smtp:
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            smtp.send_message(msg)
        return True
    except Exception as e:  # jangan sampai gagal kirim email membuat aplikasi error
        app.logger.error("Gagal kirim email ke %s: %s", to, e)
        return False


def mail_body(judul, isi, tombol=None, link=None):
    btn = (f'<p style="margin:28px 0"><a href="{link}" style="background:#0f766e;color:#fff;'
           f'padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:600">{tombol}</a></p>'
           if link else "")
    return (f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:520px;margin:auto;color:#1f2937">'
            f'<h2 style="color:#0f766e">🌳 {APP_NAME}</h2><h3>{judul}</h3><p style="line-height:1.6">{isi}</p>'
            f'{btn}<p style="color:#6b7280;font-size:13px">Salam hangat,<br>Pengelola {APP_NAME}</p></div>')


def activation_link(user_id):
    token = serializer.dumps({"uid": user_id}, salt="aktivasi")
    return url_for("aktivasi", token=token, _external=True)


# === AUTH & KEAMANAN ===
def current_user():
    uid = session.get("uid")
    if uid is None:
        return None
    if "user" not in g:
        g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["status"] != "active":
            session.pop("uid", None)
            flash("Silakan masuk terlebih dahulu.", "info")
            return redirect(url_for("masuk", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user()["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("_csrf")
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not token or not sent or not secrets.compare_digest(token, sent):
            abort(400, "Sesi formulir kedaluwarsa, silakan muat ulang halaman.")


@app.context_processor
def inject_globals():
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]
    user = current_user()
    pending = 0
    if user and user["is_admin"]:
        pending = get_db().execute("SELECT COUNT(*) FROM users WHERE status='pending'").fetchone()[0]
    me_jk = None
    if user and user["person_id"]:
        row = get_db().execute("SELECT jenis_kelamin FROM persons WHERE id=?", (user["person_id"],)).fetchone()
        me_jk = row["jenis_kelamin"] if row else None
    return {"csrf_token": csrf_token, "me": user, "APP_NAME": APP_NAME, "APP_SHORT": APP_SHORT, "pending_count": pending, "me_person_jk": me_jk,
            "PERAN": PERAN, "PERAN_DESKRIPSI": PERAN_DESKRIPSI, "me_peran": user_peran(user) if user else None,
            "tahun_ini": datetime.now().year, "hari_ini": datetime.now().date().isoformat()}


# === HELPER TEMPLATE ===
@app.template_global()
def ringkas(pid):
    return matching.describe(family(), pid)


NAMA_DEPAN_UMUM = {"muhammad", "muhamad", "mohammad", "mohamad", "moh", "muh", "m", "siti", "nur", "raden", "rr",
                   "andi", "teuku", "cut", "gusti", "i", "ni", "made", "putu", "ketut", "kadek", "komang", "abdul"}


@app.template_global()
def panggilan(nama):
    """Nama panggilan singkat: 'Muhammad Iqbal' -> 'Iqbal', 'Zadarnusa Atmawan, S.Kom' -> 'Zadarnusa'."""
    words = [w for w in (nama or "").split(",")[0].replace(".", " ").split() if w]
    while len(words) > 1 and words[0].lower() in NAMA_DEPAN_UMUM:
        words.pop(0)
    return words[0] if words else (nama or "")


@app.template_global()
def nama_pendek(nama, kata=2):
    """'Abdul Djabar' tetap utuh, 'Dwi Purnomo, MT' -> 'Dwi Purnomo', 'Raden Mas Ahmad Fauzi' -> 'Raden Mas'."""
    words = (nama or "").split(",")[0].split()
    return " ".join(words[:kata]) if words else (nama or "")


@app.template_global()
def initials(nama):
    words = [w for w in (nama or "?").split() if w[:1].isalnum()]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


@app.template_global()
def hue(pid):
    return ((pid or 0) * 47 + 160) % 360


@app.template_global()
def my_tier(p):
    """Kedekatan pengguna yang sedang login terhadap orang p (0 pemilik … 3 kerabat jauh)."""
    user = current_user()
    if not user or not p:
        return 3
    return family().kin_tier(user["person_id"], p["id"])


PERAN = {"anggota": "Anggota", "head": "Head Family", "super": "Super Family"}
PERAN_DESKRIPSI = {
    "anggota": "Diri sendiri, pasangan, anak & keturunan.",
    "head": "Anggota + ayah-ibu, kakek-nenek kandung, saudara kandung & pasangannya, keponakan kandung, "
            "paman-tante kandung, sepupu kandung.",
    "super": "Head Family + pasangan keponakan, anak keponakan, anak sepupu, pasangan sepupu, "
             "saudara kandung kakek & nenek.",
}


def user_peran(user=None):
    user = user or current_user()
    try:
        return user["peran"] if user["peran"] in PERAN or user["peran"] == "tamu" else "anggota"
    except (KeyError, IndexError, TypeError):
        return "anggota"


@app.template_global()
def pendiri_pid():
    """Data diri pendiri silsilah (akun pertama yang mendaftar) — mendapat lencana bintang emas."""
    if "pendiri_pid" not in g:
        r = get_db().execute("SELECT person_id FROM users WHERE is_admin=1 AND peran<>'tamu' ORDER BY id LIMIT 1").fetchone()
        g.pendiri_pid = r["person_id"] if r else None
    return g.pendiri_pid


@app.template_global()
def tautan_map():
    """{person_id: [{label, url}, ...]} untuk ikon 🔗 yang bisa diklik."""
    if "tautan_map" not in g:
        m = {}
        for r in get_db().execute("SELECT person_id, label, url FROM tautan_luar ORDER BY id"):
            m.setdefault(r["person_id"], []).append({"label": r["label"], "url": r["url"]})
        g.tautan_map = m
    return g.tautan_map


@app.template_global()
def tautan_ids():
    return set(tautan_map())


@app.template_global()
def is_tamu():
    user = current_user()
    return bool(user) and user_peran(user) == "tamu"


def akses_tamu_saya():
    if "akses_tamu" not in g:
        user = current_user()
        g.akses_tamu = get_db().execute("SELECT * FROM akses_tamu WHERE user_id=?", (user["id"],)).fetchone() if user else None
    return g.akses_tamu


app.add_template_global(akses_tamu_saya)


def tamu_scope():
    """Yang boleh dilihat tamu: keluarga sedarah titik masuk beserta pasangan mereka."""
    if "tamu_scope" not in g:
        a, fam = akses_tamu_saya(), family()
        sc = set()
        if a and a["titik_id"] in fam.people:
            t = a["titik_id"]
            sc = {pid for pid in fam.people if pid == t or fam.blood(t, pid)}
            sc |= {s for pid in list(sc) for s in fam.spouses(pid)}
        g.tamu_scope = sc
    return g.tamu_scope


@app.template_global()
def tamu_boleh(pid):
    return not is_tamu() or pid in tamu_scope()


def akses_tamu_valid(a):
    if not a or not a["aktif"]:
        return False
    return not a["berlaku_sampai"] or a["berlaku_sampai"] >= datetime.now().date().isoformat()


TAMU_BOLEH = {"static", "foto", "anggota_detail", "anggota", "pohon", "bandingkan", "keluarga_daftar", "keluarga_lihat",
              "api_cari", "keluar", "tamu_masuk", "tamu_beranda", "tamu_minta", "index"}


@app.before_request
def batasi_tamu():
    """Tamu hanya menjelajah (baca-saja) dalam lingkup titik masuknya; akses dicabut/kedaluwarsa = keluar."""
    user = current_user()
    if not user or user_peran(user) != "tamu":
        return None
    if not akses_tamu_valid(akses_tamu_saya()):
        session.clear()
        flash("Akses tamu Anda sudah berakhir atau dicabut. Hubungi admin silsilah ini bila perlu.", "info")
        return redirect(url_for("masuk"))
    if request.endpoint not in TAMU_BOLEH:
        flash("Sebagai tamu, Anda hanya bisa menjelajah (melihat) silsilah.", "info")
        return redirect(url_for("tamu_beranda"))
    if request.endpoint == "index":
        return redirect(url_for("tamu_beranda"))
    return None


def my_scope():
    """Orang-orang yang boleh dikelola pengguna sesuai perannya; None = tanpa batas (admin)."""
    user = current_user()
    if not user:
        return set()
    if user["is_admin"]:
        return None
    if "scope" not in g:
        g.scope = family().scope(user["person_id"], user_peran(user))
    return g.scope


@app.template_global()
def in_scope(p):
    sc = my_scope()
    return sc is None or (p is not None and p["id"] in sc)


def scope_after(rows_patch=None, new_rows=(), new_marriages=(), check=()):
    """Cek jangkauan peran pada silsilah 'seandainya' perubahan disimpan.
    rows_patch: {id: {kolom: nilai}}, new_rows: data orang baru (id negatif sementara)."""
    user = current_user()
    if not user or user["is_admin"]:
        return True, []
    fam, db = family(), get_db()
    rows = []
    for pid, p in fam.people.items():
        r = dict(p)
        r.update((rows_patch or {}).get(pid, {}))
        rows.append(r)
    base = {k: None for k in ("tahun_lahir", "tanggal_lahir", "urutan_lahir", "pasangan_id", "jenis_kelamin")}
    rows += [{**base, "wafat": 0, **r} for r in new_rows]
    mars = [dict(m) for m in db.execute("SELECT * FROM pernikahan").fetchall()]
    for a, b in new_marriages:
        if a is not None and b is not None and a != b:
            mars.append({"id": None, "orang1_id": min(a, b), "orang2_id": max(a, b), "status": "menikah",
                         "tanggal_nikah": None, "tanggal_selesai": None})
    H = Family(rows, mars)
    me = user["person_id"]
    sc = H.scope(me, user_peran(user))
    names = {r["id"]: r["nama"] for r in rows}
    outside = [names.get(i, "?") for i in check if i is not None and i not in sc]
    return not outside, outside


def _lock(p):
    try:
        t = p["kelola_tier"]
    except (KeyError, IndexError):
        t = None
    return 3 if t is None else t


@app.template_global()
def can_edit(p):
    """Boleh langsung mengubah bila sama dekat atau lebih dekat daripada pengelola data saat ini.
    Data milik akun hanya diubah pemiliknya; admin selalu boleh."""
    user = current_user()
    if not user or not p or user_peran(user) == "tamu":
        return False
    if user["is_admin"] or user["person_id"] == p["id"]:
        return True
    if p["id"] in claimed_ids():
        return False
    # harus dalam jangkauan perannya, dan tidak kalah dekat dari pengelola data saat ini
    return in_scope(p) and my_tier(p) <= _lock(p)


@app.template_global()
def can_delete(p):
    user = current_user()
    if not user or not p or p["id"] in claimed_ids():
        return bool(user and user["is_admin"])
    return bool(user["is_admin"]) or can_edit(p)


@app.template_global()
def hak_info(p):
    """Keterangan hak kelola untuk ditampilkan di halaman orang."""
    fam, db, user = family(), get_db(), current_user()
    info = {"tier": my_tier(p), "label": TINGKAT[my_tier(p)], "edit": can_edit(p), "owner": p["id"] in claimed_ids(),
            "peran": PERAN[user_peran()] if user else "", "admin": bool(user and user["is_admin"]),
            "in_scope": in_scope(p),
            "manager": None, "manager_rel": None, "lock_label": TINGKAT[_lock(p)]}
    try:
        mid = p["kelola_user"]
    except (KeyError, IndexError):
        mid = None
    if info["owner"]:
        mid = db.execute("SELECT id FROM users WHERE person_id=?", (p["id"],)).fetchone()[0]
    if mid:
        m = db.execute("SELECT id, nama, person_id FROM users WHERE id=?", (mid,)).fetchone()
        if m:
            info["manager"] = m
            if m["person_id"] == p["id"]:
                info["manager_rel"] = "pemilik data"
            elif m["person_id"] in fam.people:
                r = fam.relation(m["person_id"], p["id"])
                info["manager_rel"] = r["ab"]["label"].lower() if r.get("ab") else "kerabat"
    info["is_manager"] = bool(user and info["manager"] and info["manager"]["id"] == user["id"])
    return info


@app.template_global()
def can_see_contact(p):
    user = current_user()
    if not user or not p or user_peran(user) == "tamu":
        return False
    return bool(p["tampil_kontak"]) or bool(user["is_admin"]) or user["person_id"] == p["id"]


@app.template_global()
def wa_link(wa):
    digits = re.sub(r"\D", "", wa or "")
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    elif digits.startswith("8"):
        digits = "62" + digits
    return f"https://wa.me/{digits}" if digits else None


@app.template_global()
def completeness(p):
    if not p:
        return 0, []
    checks = [("nama", "Nama"), ("jenis_kelamin", "Jenis kelamin"), ("tanggal_lahir", "Tanggal lahir"),
              ("alamat", "Alamat"), ("wa", "Nomor WA"), ("pekerjaan", "Pekerjaan"),
              ("ayah_id", "Ayah"), ("ibu_id", "Ibu"), ("foto", "Foto")]
    if p["wafat"]:
        checks = [c for c in checks if c[0] != "wa"]
    missing = [label for key, label in checks if not p[key]]
    return round(100 * (len(checks) - len(missing)) / len(checks)), missing


@app.template_global()
def sapaan():
    h = datetime.now().hour
    return "Selamat pagi" if h < 11 else "Selamat siang" if h < 15 else "Selamat sore" if h < 18 else "Selamat malam"


BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
         "Oktober", "November", "Desember"]


def _date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@app.template_global()
def lahir(p):
    """'12 Mei 1983', atau 'sekitar 1983' bila hanya tahun yang diketahui."""
    d = _date(p["tanggal_lahir"]) if p else None
    if d:
        return f"{d.day} {BULAN[d.month - 1]} {d.year}"
    return f"sekitar {p['tahun_lahir']}" if p and p["tahun_lahir"] else ""


@app.template_global()
def umur(p):
    if not p or p["wafat"]:
        return None
    d, today = _date(p["tanggal_lahir"]), datetime.now().date()
    if d:
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    return today.year - p["tahun_lahir"] if p["tahun_lahir"] else None


@app.template_global()
def wafat_info(p):
    """Keterangan untuk profil yang telah berpulang: rentang tahun, usia, tanggal & lama berpulang."""
    if not p or not p["wafat"]:
        return None
    try:
        tw, dw = p["tahun_wafat"], _date(p["tanggal_wafat"])
    except (KeyError, IndexError):
        tw, dw = None, None
    tl, dl = p["tahun_lahir"], _date(p["tanggal_lahir"])
    info = {"rentang": f"{tl or '…'} — {tw or '…'}" if (tl or tw) else None, "usia": None,
            "tanggal": f"{dw.day} {BULAN[dw.month - 1]} {dw.year}" if dw else (f"tahun {tw}" if tw else None), "lalu": None}
    if dl and dw:
        info["usia"] = dw.year - dl.year - ((dw.month, dw.day) < (dl.month, dl.day))
    elif tl and tw:
        info["usia"] = tw - tl
    if info["usia"] is not None and info["usia"] < 0:   # data lama yang keliru: jangan tampilkan usia negatif
        info["usia"] = None
    if tw:
        n = datetime.now().year - tw
        info["lalu"] = "tahun ini" if n == 0 else f"{n} tahun yang lalu"
    return info


def upcoming_haul(people, days=30):
    """Peringatan berpulang (haul) dalam beberapa hari ke depan."""
    today = datetime.now().date()
    out = []
    for p in people:
        d = _date(p.get("tanggal_wafat")) if p["wafat"] else None
        if not d:
            continue
        try:
            nxt = d.replace(year=today.year)
        except ValueError:
            nxt = d.replace(year=today.year, day=28)
        if nxt < today:
            try:
                nxt = d.replace(year=today.year + 1)
            except ValueError:
                nxt = d.replace(year=today.year + 1, day=28)
        hari, ke = (nxt - today).days, nxt.year - d.year
        if hari <= days and ke >= 1:
            out.append({"p": p, "hari": hari, "ke": ke, "tanggal": f"{nxt.day} {BULAN[nxt.month - 1]}"})
    return sorted(out, key=lambda x: x["hari"])


STIKER = ["🎂", "🎈", "🎁", "💐", "🥳", "✨", "🌷", "🤍"]


@app.template_global()
def ultah_info(p, sebelum=30, sesudah=7):
    """Ulang tahun terdekat (bisa yang baru lewat ≤7 hari): tanggal, ke-berapa, selisih hari, & boleh diberi ucapan."""
    if not p or p["wafat"]:
        return None
    d = _date(p["tanggal_lahir"])
    if not d:
        return None
    today = datetime.now().date()
    def pada(y):
        try:
            return d.replace(year=y)
        except ValueError:   # 29 Februari
            return d.replace(year=y, day=28)
    b = pada(today.year)
    if (b - today).days < -sesudah:
        b = pada(today.year + 1)
    elif (b - today).days > 365 - sesudah:
        b = pada(today.year - 1)
    hari = (b - today).days
    return {"tahun": b.year, "ke": b.year - d.year, "hari": hari, "tanggal": f"{b.day} {BULAN[b.month - 1]}",
            "boleh": -sesudah <= hari <= sebelum, "sudah_tiba": hari <= 0}


@app.template_global()
def pekan_ultah(p):
    """Pekan ulang tahun: dari hari-H sampai 6 hari sesudahnya — profil tampil bersuasana pesta."""
    ui = ultah_info(p)
    return ui if ui and -6 <= ui["hari"] <= 0 else None


def upcoming_birthdays(people, days=30):
    today = datetime.now().date()
    out = []
    for p in people:
        d = _date(p["tanggal_lahir"])
        if not d or p["wafat"]:
            continue
        try:
            nxt = d.replace(year=today.year)
        except ValueError:  # 29 Februari
            nxt = d.replace(year=today.year, day=28)
        if nxt < today:
            try:
                nxt = d.replace(year=today.year + 1)
            except ValueError:
                nxt = d.replace(year=today.year + 1, day=28)
        delta = (nxt - today).days
        if delta <= days:
            out.append({"p": p, "hari": delta, "ke": nxt.year - d.year,
                        "tanggal": f"{nxt.day} {BULAN[nxt.month - 1]}"})
    return sorted(out, key=lambda x: x["hari"])


@app.template_filter("tgl")
def tgl(value):
    if not value:
        return "-"
    bulan = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    d = datetime.strptime(value[:10], "%Y-%m-%d")
    return f"{d.day} {bulan[d.month - 1]} {d.year}"


# === CERITA KELUARGA ===
from markupsafe import Markup, escape


@app.template_filter("cerita")
def format_cerita(text):
    """Teks bebas -> HTML aman: paragraf (baris kosong), **tebal**, *miring*, baris baru."""
    if not text:
        return Markup("")
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        h = str(escape(para.strip()))
        h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
        h = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", h)
        out.append("<p>" + h.replace("\n", "<br>") + "</p>")
    return Markup("\n".join(out))


def parse_tonggak(text):
    """'1925 — Abdul Djabar lahir' -> [(1925, 'Abdul Djabar lahir'), ...]"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip(" -•\t")
        if not line:
            continue
        m = re.match(r"^(\d{3,4}(?:\s*[-–]\s*\d{2,4})?|±?\s*\d{3,4}an?)\s*[—:–\-]\s*(.+)$", line)
        out.append((m.group(1).strip(), m.group(2).strip()) if m else ("", line))
    return out


def can_tulis_cerita(k=None, leluhur_id=None):
    """Admin: semua. Penjaga (penulis) ceritanya. Super Family: hanya cerita garis keturunannya sendiri,
    yaitu bila ia adalah leluhur itu, pasangannya, atau keturunannya."""
    user = current_user()
    if not user:
        return False
    if user["is_admin"]:
        return True
    if user_peran(user) != "super":
        return False
    if k is not None and k["created_by"] == user["id"]:
        return True
    lid = leluhur_id if leluhur_id is not None else (k["leluhur_id"] if k is not None else None)
    if lid is None:
        return k is None
    fam, me = family(), user["person_id"]
    if me not in fam.people or lid not in fam.people:
        return False
    return me == lid or me in fam.spouses(lid) or me in fam.descendants(lid)


app.add_template_global(can_tulis_cerita)


GARIS_ATAS = {"sendiri", "ortu", "ayah", "ibu", "ayah-ibu", "pasangan"}
GARIS_URUT = {"sendiri": 0, "ortu": 1, "ayah": 2, "ayah-ibu": 2, "ibu": 3, "pasangan": 4, "keturunan": 5, "kerabat": 6}


def lineage_info(fam, me, lid):
    """Posisi pembaca terhadap leluhur sebuah cerita: garis ayah/ibu/pasangan/…, beserta sebutannya."""
    if me not in fam.people or lid not in fam.people:
        return None
    L = fam.people[lid]
    nm = L["nama"].split(",")[0]
    rel = fam.relation(lid, me)
    sebutan = rel["ab"]["label"] if rel.get("ab") else None
    anc = lambda x: set(fam._up(x)[0]) if x in fam.people else set()   # x beserta leluhurnya
    if me == lid or me in fam.spouses(lid):
        return {"kind": "sendiri", "label": "Keluarga Anda sendiri", "kalimat": "Ini cerita keluarga Anda sendiri."}
    p = fam.people[me]
    ortu = set(fam.parents(me))
    if lid in ortu or ortu & set(fam.spouses(lid)):
        return {"kind": "ortu", "label": "Keluarga orang tua",
                "kalimat": "Cerita keluarga ayah & ibu Anda." if len(ortu) > 1 else f"Cerita keluarga {sebutan.lower() if sebutan else 'orang tua'} Anda."}
    via_ayah = p["ayah_id"] in fam.people and lid in anc(p["ayah_id"])
    via_ibu = p["ibu_id"] in fam.people and lid in anc(p["ibu_id"])
    if via_ayah or via_ibu:
        kind = "ayah-ibu" if via_ayah and via_ibu else ("ayah" if via_ayah else "ibu")
        label = {"ayah-ibu": "Garis ayah & ibu", "ayah": "Garis ayah", "ibu": "Garis ibu"}[kind]
        return {"kind": kind, "label": label, "kalimat": f"{nm} adalah {sebutan.lower() if sebutan else 'leluhur'} Anda dari {label.lower()}."}
    if lid in fam.descendants(me):
        return {"kind": "keturunan", "label": "Keturunan Anda", "kalimat": f"{nm} adalah {sebutan.lower() if sebutan else 'keturunan'} Anda."}
    for s in fam.spouses(me):
        if s == lid or lid in anc(s):
            peran = {"L": "suami", "P": "istri"}.get(fam.people[s]["jenis_kelamin"], "pasangan")
            sn = fam.people[s]["nama"].split(",")[0]
            return {"kind": "pasangan", "label": f"Keluarga {peran}", "kalimat": f"Keluarga besar {peran} Anda, {sn}."}
    if sebutan:
        return {"kind": "kerabat", "label": "Kerabat", "kalimat": f"{nm} adalah {sebutan.lower()} Anda."}
    return None


def story_parents(fam, rows, k):
    """Cerita induk: untuk garis leluhur cerita ini dan garis setiap pasangannya, ambil cerita
    yang leluhurnya paling dekat di atasnya. Cerita sebuah pasangan bisa punya dua induk."""
    lid = k["leluhur_id"]
    if lid not in fam.people:
        return []
    inti = {lid} | set(fam.spouses(lid))
    out = []
    for asal in [lid] + fam.spouses(lid):
        dist = fam._up(asal)[0]
        best = None
        for r in rows:
            rl = r["leluhur_id"]
            if r["id"] == k["id"] or rl not in fam.people or rl in inti:
                continue
            d = dist.get(rl)
            for s in fam.spouses(rl):          # leluhur induk bisa tercatat lewat pasangannya
                ds = dist.get(s)
                if ds is not None and (d is None or ds < d):
                    d = ds
            if d and (best is None or d < best[0]):
                best = (d, r)
        if best and best[1]["id"] not in {o["id"] for o in out}:
            out.append(best[1])
    return out


def story_parent(fam, rows, k):
    ps = story_parents(fam, rows, k)
    return ps[0] if ps else None


def story_children(fam, rows, k):
    return [r for r in rows if r["id"] != k["id"] and k["id"] in {p["id"] for p in story_parents(fam, rows, r)}]


def kandidat_leluhur(fam, me, rows):
    """Pilihan leluhur untuk cerita: diri sendiri & semua leluhur pengguna, beserta sebutan dan garisnya."""
    if me not in fam.people:
        return []
    ada = {}
    for r in rows:
        for x in [r["leluhur_id"]] + (fam.spouses(r["leluhur_id"]) if r["leluhur_id"] in fam.people else []):
            ada[x] = r
    p = fam.people[me]
    anc_ayah = set(fam._up(p["ayah_id"])[0]) if p["ayah_id"] in fam.people else set()
    anc_ibu = set(fam._up(p["ibu_id"])[0]) if p["ibu_id"] in fam.people else set()
    dist = fam._up(me)[0]
    out = [{"id": me, "sebutan": "Keluarga saya", "garis": "Keluarga sendiri", "gen": 0, "cerita": ada.get(me)}]
    sudah = {me} | set(fam.spouses(me))
    # satu kartu per pasangan: laki-laki didahulukan, sebutan digabung ("Kakek & Nenek")
    urut = sorted(dist.items(), key=lambda t: (t[1], t[0] not in anc_ayah, fam.people[t[0]]["jenis_kelamin"] != "L"))
    for x, d in urut:
        if x in sudah:
            continue
        pas = [s for s in fam.spouses(x) if s in dist]
        sudah |= {x} | set(pas)
        rel = fam.relation(x, me)
        seb = rel["ab"]["label"] if rel.get("ab") else "Leluhur"
        for s in pas[:1]:
            r2 = fam.relation(s, me)
            if r2.get("ab") and r2["ab"]["label"] != seb:
                seb = f"{seb} & {r2['ab']['label']}"
        garis = ("Garis ayah & ibu" if x in anc_ayah and x in anc_ibu else
                 "Garis ayah" if x in anc_ayah else "Garis ibu" if x in anc_ibu else "")
        cer = ada.get(x) or next((ada[s] for s in pas if s in ada), None)
        out.append({"id": x, "sebutan": seb, "garis": "" if seb.startswith("Ayah & Ibu") else garis, "gen": d, "cerita": cer})
    return out


def garis_kosong(fam, me, rows):
    """Garis ayah/ibu yang belum punya cerita: tawarkan untuk ditulis kepada yang berhak."""
    out = []
    if me not in fam.people:
        return out
    tercakup = {lineage_info(fam, me, r["leluhur_id"])["kind"] for r in rows
                if lineage_info(fam, me, r["leluhur_id"])}
    for key, label in (("ayah_id", "ayah"), ("ibu_id", "ibu")):
        start = fam.people[me][key]
        if start not in fam.people or label in tercakup or "ayah-ibu" in tercakup:
            continue
        top, seen = start, {start}
        while True:   # telusuri ke atas lewat ayah, lalu ibu bila ayah tidak tercatat
            nxt = fam.people[top]["ayah_id"] if fam.people[top]["ayah_id"] in fam.people else fam.people[top]["ibu_id"]
            if nxt not in fam.people or nxt in seen:
                break
            top = nxt
            seen.add(top)
        if can_tulis_cerita(leluhur_id=top):
            out.append({"garis": label, "leluhur": top})
    return out


def keluarga_stats(fam, leluhur):
    """Angka-angka sebuah keluarga besar, dihitung dari silsilah."""
    if leluhur not in fam.people:
        return None
    desc = fam.descendants(leluhur)
    depth = {leluhur: 0}
    order = [leluhur]
    for x in order:
        for c in fam.children(x):
            if c not in depth:
                depth[c] = depth[x] + 1
                order.append(c)
    hidup = [d for d in desc if not fam.people[d]["wafat"]]
    cabang = []
    for c in fam.children(leluhur):
        n = len(fam.descendants(c))
        cabang.append({"id": c, "keturunan": n, "spouses": [m for m in fam.marriages(c)]})
    return {"generasi": (max(depth.values()) + 1) if depth else 1, "keturunan": len(desc),
            "anak": len(fam.children(leluhur)), "cucu": sum(1 for d in desc if depth.get(d) == 2),
            "hidup": len(hidup), "berpulang": len(desc) - len(hidup), "cabang": cabang,
            "pasangan": fam.marriages(leluhur)}


def families_of(fam, pid, rows):
    """Cerita keluarga yang memuat orang ini (leluhur, pasangannya, keturunan, atau pasangan keturunan)."""
    out = []
    for k in rows:
        lid = k["leluhur_id"]
        if lid not in fam.people:
            continue
        inti = {lid} | set(fam.spouses(lid)) | fam.descendants(lid)
        if pid in inti or any(s in inti for s in fam.spouses(pid)):
            out.append(k)
    return out


def process_cover(file):
    try:
        img = Image.open(file.stream)
        img.load()
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise ValueError("File sampul tidak dapat dibaca. Gunakan JPG atau PNG.")
    img = ImageOps.fit(img, (1600, 680), Image.LANCZOS, centering=(0.5, 0.42))
    name = "sampul-" + uuid.uuid4().hex + ".jpg"
    img.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=82, optimize=True, progressive=True)
    return name


# === FOTO ===
def process_photo(file):
    try:
        img = Image.open(file.stream)
        img.load()
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise ValueError("File foto tidak dapat dibaca. Gunakan format JPG atau PNG, ya.")
    img = ImageOps.fit(img, (FOTO_SIZE, FOTO_SIZE), Image.LANCZOS, centering=(0.5, 0.4))
    name = uuid.uuid4().hex + ".jpg"
    img.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=FOTO_QUALITY, optimize=True, progressive=True)
    return name


def remove_photo(name):
    if name:
        path = os.path.join(UPLOAD_DIR, os.path.basename(name))
        if os.path.exists(path):
            os.remove(path)


@app.route("/foto/<path:name>")
@login_required
def foto(name):
    resp = send_from_directory(UPLOAD_DIR, os.path.basename(name))
    resp.cache_control.max_age = 86400
    resp.cache_control.private = True
    return resp


# === FORM ANGGOTA ===
def _clean(value, limit=200):
    return re.sub(r"\s+", " ", (value or "")).strip()[:limit] or None


def _person(pid):
    return get_db().execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()


def _int(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def form_ctx(f, person_id=None):
    """Konteks isian form untuk prediksi: nama, jk, tahun, serta ayah/ibu/pasangan (id atau nama)."""
    fam = family()

    def ref(key):
        rid = _int(f.get(f"{key}_id"))
        nama = fam.people[rid]["nama"] if rid in fam.people else _clean(f.get(f"{key}_nama"), 120)
        return {"id": rid if rid in fam.people else None, "nama": nama}
    tanggal = _date(f.get("tanggal_lahir") or f.get("tanggal"))
    tahun = tanggal.year if tanggal else _int(f.get("tahun_lahir") or f.get("tahun"))
    return {"id": person_id, "nama": _clean(f.get("nama"), 120),
            "jk": f.get("jenis_kelamin") or f.get("jk_diri"), "tahun": tahun,
            "tanggal": tanggal.isoformat() if tanggal else None,
            "ayah": ref("ayah"), "ibu": ref("ibu"), "pasangan": ref("pasangan")}


def ensure_marriage(db, a, b, status="menikah", tanggal_nikah=None, tanggal_selesai=None):
    if a and b and a != b:
        db.execute("INSERT OR IGNORE INTO pernikahan (orang1_id, orang2_id, status, tanggal_nikah, tanggal_selesai, created_by) "
                   "VALUES (?,?,?,?,?,?)", (min(a, b), max(a, b), status, tanggal_nikah, tanggal_selesai,
                                            current_user()["id"] if current_user() else None))


LABEL_KOLOM = [("nama", "Nama"), ("jenis_kelamin", "Jenis kelamin"), ("tanggal_lahir", "Tanggal lahir"),
               ("tahun_lahir", "Tahun lahir"), ("urutan_lahir", "Anak ke-"), ("pekerjaan", "Pekerjaan"),
               ("wa", "WA"), ("email", "Email"), ("alamat", "Alamat"), ("bio", "Cerita"), ("wafat", "Status berpulang"),
               ("ayah_id", "Ayah"), ("ibu_id", "Ibu"), ("tampil_kontak", "Tampilkan kontak"),
               ("tanggal_wafat", "Tanggal berpulang"), ("tahun_wafat", "Tahun berpulang"), ("makam", "Tempat peristirahatan")]


def diff_person(old, new, nama_baru=None):
    """Daftar perubahan yang mudah dibaca, mis. 'Pekerjaan: Guru → Dosen'."""
    fam, nama_baru = family(), nama_baru or {}
    def tampil(key, v):
        if v in (None, ""):
            return nama_baru.get(key, "—")
        if key in ("ayah_id", "ibu_id"):
            return fam.people[v]["nama"] if v in fam.people else nama_baru.get(key, f"#{v}")
        if key == "jenis_kelamin":
            return {"L": "Laki-laki", "P": "Perempuan"}.get(v, v)
        if key in ("wafat", "tampil_kontak"):
            return "ya" if v else "tidak"
        return str(v)
    out = []
    for key, label in LABEL_KOLOM:
        if key not in new:
            continue
        a, b = (old[key] if old else None), new[key]
        if (key == "tahun_lahir" and new.get("tanggal_lahir")) or (key == "tahun_wafat" and new.get("tanggal_wafat")):
            continue
        if (a or None) == (b or None) and not (key in nama_baru and not b):
            continue
        if key in ("bio", "alamat") and a and b:
            out.append(f"{label} diperbarui")
        else:
            out.append(f"{label}: {tampil(key, a)} → {tampil(key, b)}")
    return out


def log_riwayat(db, pid, perubahan, catatan=None):
    if perubahan:
        db.execute("INSERT INTO riwayat (person_id, user_id, ringkasan, catatan) VALUES (?,?,?,?)",
                   (pid, current_user()["id"] if current_user() else None, json.dumps(perubahan), catatan))


def update_kelola(db, pid):
    """Hak kelola berpindah ke pengubah yang lebih dekat (atau sama dekat)."""
    g.pop("family", None)
    user, fam = current_user(), family()
    t = fam.kin_tier(user["person_id"], pid)
    row = db.execute("SELECT kelola_tier FROM persons WHERE id=?", (pid,)).fetchone()
    if row["kelola_tier"] is None or t <= row["kelola_tier"]:
        db.execute("UPDATE persons SET kelola_tier=?, kelola_user=? WHERE id=?", (t, user["id"], pid))


BUKTI_KUAT = ("— cocok", "Sudah tercatat", "Punya anak", "Pasangannya tercatat", "sama:")


def save_person(person=None, keep_existing=False, self_exclude=None, form=None, dry=False, auto_new=False,
                catatan=None, own=False, link_child=None):
    """Simpan data dari form (default request.form).

    Mengembalikan (id, daftar_error, saran). `saran` berisi kandidat orang yang mirip
    untuk nama-nama baru; bila ada, data belum disimpan dan pengguna diminta memilih.
    dry=True: tidak menyimpan apa pun, mengembalikan (daftar_perubahan, error, saran) — untuk usulan.
    auto_new=True: nama baru yang tidak pasti langsung dibuat (dipakai saat menyetujui usulan).
    """
    f = form if form is not None else request.form
    db, user, fam = get_db(), current_user(), family()
    errors = []
    nama = _clean(f.get("nama"), 120)
    if not nama:
        errors.append("Nama wajib diisi.")
    jk = f.get("jenis_kelamin") if f.get("jenis_kelamin") in ("L", "P") else None
    tahun, tanggal = None, None
    if (f.get("tanggal_lahir") or "").strip():
        d = _date(f["tanggal_lahir"].strip())
        if not d or d.year < 1000 or d > datetime.now().date():
            errors.append("Tanggal lahir sepertinya belum tepat.")
        else:
            tanggal, tahun = d.isoformat(), d.year
    elif (f.get("tahun_lahir") or "").strip():
        try:
            tahun = int(f["tahun_lahir"])
            if not 1000 <= tahun <= datetime.now().year:
                raise ValueError
        except ValueError:
            errors.append("Tahun lahir sepertinya belum tepat.")
    urutan = _int(f.get("urutan_lahir"))
    if urutan is not None and not 1 <= urutan <= 30:
        errors.append("Urutan anak ke- sepertinya belum tepat.")
        urutan = None
    tgl_wafat, thn_wafat, makam = None, None, None
    if f.get("wafat"):
        if (f.get("tanggal_wafat") or "").strip():
            dw = _date(f["tanggal_wafat"].strip())
            if not dw or dw > datetime.now().date() or (tanggal and dw.isoformat() < tanggal) or (tahun and dw.year < tahun):
                errors.append("Tanggal berpulang sepertinya belum tepat.")
            else:
                tgl_wafat, thn_wafat = dw.isoformat(), dw.year
        elif (f.get("tahun_wafat") or "").strip():
            thn_wafat = _int(f.get("tahun_wafat"))
            if not thn_wafat or not 1000 <= thn_wafat <= datetime.now().year or (tahun and thn_wafat < tahun):
                errors.append("Tahun berpulang sepertinya belum tepat.")
                thn_wafat = None
        makam = _clean(f.get("makam"), 200)
    email_p = (f.get("email") or "").strip().lower()[:160] or None
    if email_p and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_p):
        errors.append("Format email belum benar.")
    wa = _clean(f.get("wa"), 25)
    if wa and len(re.sub(r"\D", "", wa)) < 8:
        errors.append("Nomor WA terlalu pendek.")

    # relasi: pilih yang sudah ada (id) atau ketik nama baru
    relasi, baru = {}, {}
    descendants = fam.descendants(person["id"]) if person else set()
    for key, label, need_jk in (("ayah", "Ayah", "L"), ("ibu", "Ibu", "P"), ("pasangan", "Pasangan", None)):
        pilih = (f.get(f"pilih_{key}") or "").strip()
        rid = pilih if pilih.isdigit() else (f.get(f"{key}_id") or "").strip()
        rnama = _clean(f.get(f"{key}_nama"), 120)
        relasi[key] = None
        if rid.isdigit():
            target = _person(int(rid))
            if not target:
                errors.append(f"Data {label.lower()} tidak ditemukan.")
            elif person and (target["id"] == person["id"] or (key != "pasangan" and target["id"] in descendants)):
                errors.append(f"{target['nama']} tidak bisa menjadi {label.lower()} — "
                              "beliau tercatat sebagai keturunan dari orang ini.")
            elif key == "pasangan" and person and fam.close_kin(person["id"], target["id"]):
                errors.append(f"{target['nama']} tercatat sebagai kerabat dekat, tidak bisa menjadi pasangan.")
            elif key == "pasangan" and fam.gender(target["id"]) == "P" and \
                    [x for x in fam.active_spouses(target["id"]) if not person or x != person["id"]]:
                errors.append(f"{target['nama']} masih tercatat menikah dengan "
                              f"{fam.people[fam.active_spouses(target['id'])[0]]['nama']}.")
            elif need_jk and target["jenis_kelamin"] and target["jenis_kelamin"] != need_jk:
                errors.append(f"{target['nama']} tercatat dengan jenis kelamin yang berbeda untuk peran {label.lower()}.")
            else:
                relasi[key] = target["id"]
        elif rnama and not pilih.isdigit():
            baru[key] = (rnama, need_jk or ({"L": "P", "P": "L"}.get(jk)))
    if errors:
        return None, errors, {}

    # foto diproses lebih dulu agar tidak hilang bila pengguna perlu mengonfirmasi saran
    new_foto = None
    file = request.files.get("foto") if form is None and not dry else None
    if file and file.filename:
        try:
            new_foto = process_photo(file)
        except ValueError as e:
            return None, [str(e)], {}
        session["foto_tmp"] = new_foto
    elif f.get("foto_tmp") and f.get("foto_tmp") == session.get("foto_tmp"):
        new_foto = session["foto_tmp"]

    # prediksi: apakah nama yang diketik sebenarnya sudah ada di silsilah?
    saran = {}
    ctx = form_ctx(f, person["id"] if person else None)
    tersambung = []
    for key, (rnama, _) in list(baru.items()):
        if f.get(f"{key}_baru_ok") or f.get(f"pilih_{key}") == "baru":
            continue
        excl = {person["id"]} | (descendants if key != "pasangan" else set()) if person else set()
        cands = matching.rank(fam, key, rnama, ctx, exclude=excl, limit=4, min_score=0.78)
        if not cands:
            continue
        top = cands[0]
        bukti = [r for r in top["reasons"] if any(k in r for k in BUKTI_KUAT)]
        # sangat pasti & tidak ambigu: langsung disambungkan, tanpa membuat data kembar
        if top["score"] >= 0.95 and bukti and (len(cands) == 1 or cands[1]["score"] < 0.8):
            relasi[key] = top["id"]
            baru.pop(key)
            tersambung.append(f"“{rnama}” otomatis disambungkan ke data {fam.people[top['id']]['nama']} "
                              f"yang sudah ada ({bukti[0].lower()}).")
            continue
        if not auto_new:
            saran[key] = cands
    if self_exclude is not None and not person and f.get("pilih_diri") != "baru":
        cands = matching.rank(fam, "diri", nama, ctx, exclude=self_exclude, limit=4, min_score=0.8)
        if cands:
            saran["diri"] = cands
    if saran:
        return None, [], saran

    # jangkauan peran: orang ini & orang baru yang ikut dibuat harus termasuk keluarga yang boleh dikelola
    if not dry and not own:
        tid = person["id"] if person else -1
        patch, new_rows = {}, []
        ids = {}
        for n, (key, (rnama, rjk)) in enumerate(baru.items(), start=2):
            ids[key] = -n
            new_rows.append({"id": -n, "nama": rnama, "jenis_kelamin": rjk, "ayah_id": None, "ibu_id": None})
        ay = ids.get("ayah", relasi["ayah"]); ib = ids.get("ibu", relasi["ibu"]); ps = ids.get("pasangan", relasi["pasangan"])
        if person:
            patch[tid] = {"ayah_id": ay, "ibu_id": ib}
        else:
            new_rows.append({"id": -1, "nama": nama, "jenis_kelamin": jk, "ayah_id": ay, "ibu_id": ib})
        if link_child:
            cid, peran_ = link_child
            patch.setdefault(cid, {})[f"{peran_}_id"] = tid
        ok, luar = scope_after(patch, new_rows, [(tid, ps), (ay, ib)], [tid] + list(ids.values()))
        if not ok:
            return None, [f"Di luar jangkauan peran {PERAN[user_peran()]} Anda: {', '.join(luar)}. "
                          "Minta bantuan Super Family atau admin, atau kirim sebagai usulan."], {}

    if dry:   # pratinjau usulan: hitung perubahan tanpa menyimpan
        new = dict(nama=nama, jenis_kelamin=jk, tahun_lahir=tahun, tanggal_lahir=tanggal, urutan_lahir=urutan,
                   alamat=_clean(f.get("alamat"), 300), wa=wa, email=email_p, pekerjaan=_clean(f.get("pekerjaan"), 120),
                   bio=_clean(f.get("bio"), 1000), ayah_id=relasi["ayah"], ibu_id=relasi["ibu"],
                   wafat=1 if f.get("wafat") else 0, tampil_kontak=1 if f.get("tampil_kontak") else 0,
                   tanggal_wafat=tgl_wafat, tahun_wafat=thn_wafat, makam=makam)
        if person and not f.get("wafat") and "wafat" not in f and person["wafat"]:
            new["wafat"] = person["wafat"]
        perubahan = diff_person(person, new, {f"{k}_id": f"{n} (baru)" for k, (n, _) in baru.items()})
        if relasi["pasangan"] or "pasangan" in baru:
            perubahan.append("Tambah pasangan: " + (fam.people[relasi["pasangan"]]["nama"] if relasi["pasangan"]
                                                    else baru["pasangan"][0] + " (baru)"))
        return perubahan, [], {}
    session.pop("foto_tmp", None)
    for t in tersambung:
        flash(t, "info")

    for key, (rnama, rjk) in baru.items():
        cur = db.execute("INSERT INTO persons (nama, jenis_kelamin, created_by) VALUES (?,?,?)",
                         (rnama, rjk, user["id"]))
        relasi[key] = cur.lastrowid

    data = dict(nama=nama, jenis_kelamin=jk, tahun_lahir=tahun, tanggal_lahir=tanggal, urutan_lahir=urutan, alamat=_clean(f.get("alamat"), 300),
                wa=wa, email=email_p, pekerjaan=_clean(f.get("pekerjaan"), 120), bio=_clean(f.get("bio"), 1000),
                ayah_id=relasi["ayah"], ibu_id=relasi["ibu"],
                wafat=1 if f.get("wafat") else 0, tampil_kontak=1 if f.get("tampil_kontak") else 0,
                tanggal_wafat=tgl_wafat, tahun_wafat=thn_wafat, makam=makam)
    if person:
        if keep_existing:  # menautkan ke data yang dicatat keluarga: isian kosong tidak menghapus data lama
            data = {k: (person[k] if v in (None, "") else v) for k, v in data.items()}
            for k in ("wafat", "tanggal_wafat", "tahun_wafat", "makam"):
                data[k] = person[k]
        perubahan = diff_person(person, data)
        if new_foto or f.get("hapus_foto"):
            remove_photo(person["foto"])
            data["foto"] = new_foto
            perubahan.append("Foto diganti" if new_foto else "Foto dihapus")
        data.update(updated_by=user["id"], updated_at=now())
        cols = ", ".join(f"{k}=?" for k in data)
        db.execute(f"UPDATE persons SET {cols} WHERE id=?", (*data.values(), person["id"]))
        pid = person["id"]
    else:
        data.update(foto=new_foto, created_by=user["id"])
        cols = ", ".join(data)
        cur = db.execute(f"INSERT INTO persons ({cols}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values()))
        pid = cur.lastrowid
        perubahan = ["Data dicatat"]
    # ikatan otomatis: pasangan dicatat di riwayat pernikahan, ayah-ibu juga dicatat sebagai suami-istri
    if relasi["pasangan"]:
        sp = relasi["pasangan"]
        ensure_marriage(db, pid, sp)
        if data["jenis_kelamin"]:
            db.execute("UPDATE persons SET jenis_kelamin=? WHERE id=? AND jenis_kelamin IS NULL",
                       ({"L": "P", "P": "L"}[data["jenis_kelamin"]], sp))
            # anak yang dicentang: pasangan ini juga menjadi orang tuanya
            slot = "ibu_id" if data["jenis_kelamin"] == "L" else "ayah_id"
            own = "ayah_id" if slot == "ibu_id" else "ibu_id"
            for cid in {_int(x) for x in f.getlist("anak_bersama")} - {None}:
                db.execute(f"UPDATE persons SET {slot}=? WHERE id=? AND {own}=? AND {slot} IS NULL", (sp, cid, pid))
    if data["ayah_id"] and data["ibu_id"]:
        ensure_marriage(db, data["ayah_id"], data["ibu_id"])
    if relasi["pasangan"]:
        perubahan.append("Pasangan: " + (family().people.get(relasi["pasangan"], {}).get("nama") or "baru"))
    log_riwayat(db, pid, perubahan, catatan)
    for key in baru:   # orang tua/pasangan baru ikut dikelola oleh pencatatnya
        if relasi.get(key):
            log_riwayat(db, relasi[key], ["Data dicatat"])
    db.commit()
    update_kelola(db, pid)
    for key in baru:
        if relasi.get(key):
            update_kelola(db, relasi[key])
    db.commit()
    return pid, [], {}


def form_values(person=None):
    """Nilai form: dari POST yang gagal, dari data tersimpan, atau prefill query string."""
    fam = family()
    if request.method == "POST":
        v = {k: request.form.get(k) for k in request.form}
        v["wafat"] = bool(request.form.get("wafat"))
        v["tampil_kontak"] = bool(request.form.get("tampil_kontak"))
        v["foto"] = person["foto"] if person else None
        if v.get("foto_tmp") and v["foto_tmp"] == session.get("foto_tmp"):
            v["foto"] = v["foto_tmp"]
        else:
            v.pop("foto_tmp", None)
        return v
    v = dict(person) if person else {"tampil_kontak": True, "wafat": False}
    if not person:
        for key in ("ayah", "ibu", "pasangan"):
            arg = request.args.get(key, type=int)
            if arg in fam.people:
                v[f"{key}_id"] = arg
        if request.args.get("jk") in ("L", "P"):
            v["jenis_kelamin"] = request.args["jk"]
    for key in ("ayah", "ibu", "pasangan"):
        rid = v.get(f"{key}_id")
        v[f"{key}_nama"] = fam.people[rid]["nama"] if rid in fam.people else ""
    return v


# === HALAMAN PUBLIK ===
@app.route("/")
def index():
    user = current_user()
    if user and user["status"] == "active":
        return redirect(url_for("beranda"))
    db = get_db()
    stats = {"anggota": db.execute("SELECT COUNT(*) FROM persons").fetchone()[0],
             "akun": db.execute("SELECT COUNT(*) FROM users WHERE status='active' AND peran<>'tamu'").fetchone()[0]}
    return render_template("landing.html", stats=stats)


@app.route("/daftar", methods=["GET", "POST"])
def daftar():
    if request.method == "POST":
        db = get_db()
        nama = _clean(request.form.get("nama"), 120)
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        catatan = _clean(request.form.get("catatan"), 500)
        errors = []
        if not nama:
            errors.append("Nama lengkap wajib diisi.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors.append("Format email belum benar.")
        if len(pw) < 8:
            errors.append("Kata sandi minimal 8 karakter.")
        if pw != request.form.get("password2"):
            errors.append("Konfirmasi kata sandi belum sama.")
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            errors.append("Email ini sudah terdaftar. Coba masuk, atau hubungi admin keluarga.")
        if errors:
            for e in errors:
                flash(e, "warning")
            return render_template("auth/daftar.html", v=request.form)

        first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        cur = db.execute(
            "INSERT INTO users (email, password_hash, nama, status, is_admin, catatan_daftar, approved_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (email, generate_password_hash(pw), nama, "active" if first else "pending",
             1 if first else 0, catatan, now() if first else None))
        db.commit()
        if first:
            session.clear()
            session["uid"] = cur.lastrowid
            flash("Selamat datang! Anda adalah pengguna pertama sehingga otomatis menjadi admin keluarga.", "success")
            return redirect(url_for("profil"))

        send_mail(email, "Pendaftaran Anda sudah kami terima",
                  mail_body(f"Halo {nama},",
                            "Terima kasih sudah bergabung merajut silsilah keluarga. Admin keluarga akan "
                            "meninjau pendaftaran Anda. Begitu disetujui, kami kirimkan tautan aktivasi ke email ini."))
        for adm in db.execute("SELECT email FROM users WHERE is_admin=1 AND status='active'"):
            send_mail(adm["email"], f"Pendaftar baru: {nama}",
                      mail_body("Ada anggota keluarga yang ingin bergabung",
                                f"<b>{nama}</b> ({email})<br><i>{catatan or 'Tanpa catatan'}</i>",
                                "Tinjau pendaftaran", url_for("admin", _external=True)))
        return render_template("auth/status.html", status="pending", nama=nama)
    return render_template("auth/daftar.html", v={})


@app.route("/masuk", methods=["GET", "POST"])
def masuk():
    if request.method == "POST":
        db = get_db()
        email = (request.form.get("email") or "").strip().lower()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], request.form.get("password") or ""):
            flash("Email atau kata sandi belum cocok. Coba lagi pelan-pelan, ya.", "warning")
            return render_template("auth/masuk.html", email=email)
        if user["status"] != "active":
            return render_template("auth/status.html", status=user["status"], nama=user["nama"])
        session.clear()
        session["uid"] = user["id"]
        session.permanent = bool(request.form.get("ingat"))
        db.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
        db.commit()
        if user["wajib_ganti"]:
            flash(f"Selamat datang, {user['nama'].split()[0]}! Silakan buat kata sandi pribadi Anda dulu 🔐", "info")
            return redirect(url_for("akun_sandi"))
        nxt = request.args.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("beranda"))
    return render_template("auth/masuk.html", email="")


@app.route("/keluar")
def keluar():
    session.clear()
    flash("Sampai jumpa lagi! 👋", "info")
    return redirect(url_for("index"))


@app.route("/aktivasi/<token>")
def aktivasi(token):
    try:
        data = serializer.loads(token, salt="aktivasi", max_age=14 * 86400)
    except SignatureExpired:
        return render_template("auth/status.html", status="expired")
    except BadSignature:
        abort(404)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (data["uid"],)).fetchone()
    if not user:
        abort(404)
    if user["status"] == "approved":
        db.execute("UPDATE users SET status='active' WHERE id=?", (user["id"],))
        db.commit()
    elif user["status"] != "active":
        return render_template("auth/status.html", status=user["status"], nama=user["nama"])
    session.clear()
    session["uid"] = user["id"]
    flash(f"Akun Anda aktif. Selamat bergabung, {user['nama'].split()[0]}! 🌱", "success")
    return redirect(url_for("profil"))


# === HALAMAN ANGGOTA ===
@app.route("/beranda")
@login_required
def beranda():
    if is_tamu():
        return redirect(url_for("tamu_beranda"))
    db, fam, user = get_db(), family(), current_user()
    my = fam.get(user["person_id"]) if user["person_id"] else None
    terbaru = db.execute("SELECT * FROM persons ORDER BY id DESC LIMIT 8").fetchall()
    stats = {"anggota": len(fam.people), "generasi": fam.generation_count(),
             "akun": db.execute("SELECT COUNT(*) FROM users WHERE status='active' AND peran<>'tamu'").fetchone()[0],
             "foto": sum(1 for p in fam.people.values() if p["foto"])}
    suggestions = []
    if my:
        for key, role, jk in (("ayah_id", "ayah", "L"), ("ibu_id", "ibu", "P")):
            if not my[key]:
                suggestions.append({"icon": "person-plus", "text": f"Tambahkan data {role} Anda",
                                    "url": url_for("profil")})
            else:
                par = fam.get(my[key])
                for k2, r2 in (("ayah_id", "ayah"), ("ibu_id", "ibu")):
                    if not par[k2]:
                        suggestions.append({"icon": "diagram-3", "text": f"Kenali {r2} dari {par['nama']}",
                                            "url": url_for("anggota_ubah", pid=par["id"])})
    ultah = upcoming_birthdays(fam.people.values())[:6]
    ucapan_saya = {r["person_id"]: True for r in db.execute(
        "SELECT person_id FROM ucapan WHERE user_id=? AND tahun>=?", (user["id"], datetime.now().year - 1))}
    kejutan = None
    if my:
        ui = ultah_info(my)
        if ui and ui["sudah_tiba"] and ui["hari"] >= -7:
            rows = db.execute("SELECT c.*, u.nama AS oleh, u.person_id AS oleh_pid FROM ucapan c LEFT JOIN users u "
                              "ON u.id=c.user_id WHERE c.person_id=? AND c.tahun=? ORDER BY c.id", (my["id"], ui["tahun"])).fetchall()
            belum = sum(1 for r in rows if not r["dibaca"])
            kunci = f"kejutan-{ui['tahun']}-{datetime.now().date().isoformat()}"
            buka = (ui["hari"] == 0 or belum) and not session.get(kunci)
            session[kunci] = True
            kejutan = {"info": ui, "ucapan": rows, "belum": belum, "buka": buka}
    haul = upcoming_haul(fam.people.values())[:6]
    semua_cerita = db.execute("SELECT * FROM keluarga ORDER BY updated_at DESC, id DESC").fetchall()
    cerita = []
    for k in semua_cerita:
        pos = lineage_info(fam, my["id"], k["leluhur_id"]) if my else None
        # hanya garis ke atas: keluarga sendiri, orang tua, garis ayah/ibu, atau garis pasangan
        if pos and pos["kind"] in GARIS_ATAS:
            cerita.append({"k": k, "stats": keluarga_stats(fam, k["leluhur_id"]), "posisi": pos})
    # garis yang sama: tampilkan yang leluhurnya paling tinggi (cerita induk), cabangnya ada di halaman cerita
    cerita.sort(key=lambda c: (GARIS_URUT.get(c["posisi"]["kind"], 9),
                               -len(fam.descendants(c["k"]["leluhur_id"])) if c["k"]["leluhur_id"] in fam.people else 0))
    terpilih, dipakai = [], set()
    for c in cerita:
        if c["posisi"]["kind"] not in dipakai:
            terpilih.append(c)
            dipakai.add(c["posisi"]["kind"])
    # akun tanpa data diri (mis. admin pengelola) melihat cerita keluarga besar secara umum
    cerita = terpilih if my else [{"k": k, "stats": keluarga_stats(fam, k["leluhur_id"]),
                                   "posisi": {"kind": "umum", "label": "Cerita keluarga besar", "kalimat": ""}}
                                  for k in list(semua_cerita)[:1]]
    garis_belum = garis_kosong(fam, my["id"], semua_cerita) if my else []
    menunggu = [r for r in db.execute(
        "SELECT s.*, u.nama AS oleh, p.nama AS untuk FROM usulan s JOIN users u ON u.id=s.user_id "
        "JOIN persons p ON p.id=s.person_id WHERE s.status='menunggu' AND s.user_id<>? ORDER BY s.id DESC",
        (user["id"],)).fetchall() if can_edit(fam.get(r["person_id"]))][:6]
    return render_template("beranda.html", my=my, terbaru=terbaru, stats=stats,
                           suggestions=suggestions[:3], fam=fam, ultah=ultah, menunggu=menunggu, loads=json.loads,
                           jangkauan=None if my_scope() is None else len(my_scope()), cerita=cerita,
                           garis_belum=garis_belum, haul=haul, ucapan_saya=ucapan_saya, kejutan=kejutan,
                           STIKER=STIKER)


def suggest_self(nama, ctx=None):
    """Data yang dicatat keluarga dan kemungkinan adalah orang ini (belum punya akun)."""
    if not nama:
        return []
    return matching.rank(family(), "diri", nama, ctx or {}, exclude=claimed_ids(), limit=6, min_score=0.7)


@app.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    user, db = current_user(), get_db()
    person = _person(user["person_id"]) if user["person_id"] else None
    saran = {}
    if request.method == "POST":
        klaim = _int(request.form.get("klaim_id")) or _int(request.form.get("pilih_diri"))
        if not person and klaim and klaim not in claimed_ids() and _person(klaim):
            # "Ya, ini saya": tautkan ke data yang sudah dicatat keluarga, lalu lengkapi dengan isian form
            person = _person(klaim)
            db.execute("UPDATE users SET person_id=? WHERE id=?", (klaim, user["id"]))
            db.commit()
            g.pop("claimed", None)
            pid, errors, saran = save_person(person, keep_existing=True, own=True)
        else:
            pid, errors, saran = save_person(person, self_exclude=None if person else claimed_ids(), own=True)
        for e in errors:
            flash(e, "warning")
        if pid:
            if not user["person_id"] and not klaim:
                db.execute("UPDATE users SET person_id=? WHERE id=?", (pid, user["id"]))
                db.commit()
            flash("Profil tersimpan. Terima kasih sudah ikut merawat silsilah keluarga 💚", "success")
            return redirect(url_for("anggota_detail", pid=pid))
    calon = []
    if not person and request.method == "GET":
        calon = suggest_self(user["nama"])
    v = form_values(person)
    if not person and request.method == "GET":
        v.setdefault("nama", user["nama"])
    if request.method == "GET" and not v.get("email"):
        v["email"] = user["email"]
    return render_template("person_form.html", v=v, person=person, is_profile=True, calon=calon,
                           saran=saran, fam=family())


@app.route("/profil/klaim/<int:pid>", methods=["POST"])
@login_required
def profil_klaim(pid):
    user, db = current_user(), get_db()
    if user["person_id"] or pid in claimed_ids() or not _person(pid):
        flash("Data tersebut tidak bisa ditautkan.", "warning")
        return redirect(url_for("profil"))
    db.execute("UPDATE users SET person_id=? WHERE id=?", (pid, user["id"]))
    db.commit()
    flash("Berhasil! Data ini sekarang tertaut dengan akun Anda. Silakan lengkapi bila perlu.", "success")
    return redirect(url_for("profil"))


@app.route("/anggota")
@login_required
def anggota():
    q = (request.args.get("q") or "").strip()
    filt = request.args.get("f", "")
    sql, params = "SELECT * FROM persons WHERE 1=1", []
    if q:
        sql += " AND (nama LIKE ? OR pekerjaan LIKE ? OR alamat LIKE ?)"
        params += [f"%{q}%"] * 3
    if filt == "foto":
        sql += " AND foto IS NOT NULL"
    elif filt == "wafat":
        sql += " AND wafat=1"
    elif filt == "akun":
        sql += " AND id IN (SELECT person_id FROM users WHERE person_id IS NOT NULL)"
    urut = "tertua" if request.args.get("urut") == "tertua" else "nama"
    sql += " ORDER BY nama COLLATE NOCASE LIMIT 300"
    rows = get_db().execute(sql, params).fetchall()
    if is_tamu():
        rows = [r for r in rows if r["id"] in tamu_scope()]
    fam = family()
    if urut == "tertua":
        rows = sorted(rows, key=lambda r: fam.age_key(r["id"]))
    return render_template("anggota.html", rows=rows, q=q, filt=filt, urut=urut, fam=fam, claimed=claimed_ids())


@app.route("/anggota/tambah", methods=["GET", "POST"])
@login_required
def anggota_tambah():
    anak_id = request.args.get("anak", type=int)
    peran = request.args.get("peran")
    anak = _person(anak_id) if anak_id else None
    saran = {}
    if request.method == "POST":
        pakai = _int(request.form.get("pakai_id")) or _int(request.form.get("pilih_diri"))
        if pakai and _person(pakai):
            # orang yang dimaksud ternyata sudah ada: cukup tautkan, tidak membuat data ganda
            if anak and peran in ("ayah", "ibu") and can_edit(anak) and not anak[f"{peran}_id"]:
                target = _person(pakai)
                if pakai == anak["id"] or pakai in family().descendants(anak["id"]):
                    flash("Data tersebut tidak bisa dijadikan orang tua (silsilah melingkar).", "warning")
                else:
                    get_db().execute(f"UPDATE persons SET {peran}_id=? WHERE id=?", (pakai, anak["id"]))
                    get_db().commit()
                    flash(f"{target['nama']} kini tercatat sebagai {peran} dari {anak['nama']} 🌿", "success")
                    return redirect(url_for("anggota_detail", pid=anak["id"]))
            else:
                return redirect(url_for("anggota_detail", pid=pakai))
        pid, errors, saran = save_person(self_exclude=set(), link_child=(anak["id"], peran) if anak and peran in ("ayah", "ibu") else None)
        for e in errors:
            flash(e, "warning")
        if pid:
            if anak and peran in ("ayah", "ibu") and can_edit(anak) and not anak[f"{peran}_id"]:
                get_db().execute(f"UPDATE persons SET {peran}_id=? WHERE id=?", (pid, anak["id"]))
                get_db().commit()
            flash("Anggota keluarga berhasil ditambahkan 🌿", "success")
            return redirect(url_for("anggota_detail", pid=anak["id"] if anak else pid))
    v = form_values()
    if anak and peran in ("ayah", "ibu") and request.method == "GET":
        v["jenis_kelamin"] = "L" if peran == "ayah" else "P"
        other = anak["ibu_id" if peran == "ayah" else "ayah_id"]
        if other in family().people:  # ayah baru otomatis berpasangan dengan ibu yang sudah tercatat
            v["pasangan_id"], v["pasangan_nama"] = other, family().people[other]["nama"]
    return render_template("person_form.html", v=v, person=None, anak=anak, peran=peran, saran=saran, fam=family())


@app.route("/anggota/<int:pid>")
@login_required
def anggota_detail(pid):
    fam = family()
    p = fam.get(pid)
    if not p or not tamu_boleh(pid):
        abort(404)
    db = get_db()
    akun = db.execute("SELECT nama, created_at FROM users WHERE person_id=?", (pid,)).fetchone()
    editor = None
    if p["updated_by"] or p["created_by"]:
        editor = db.execute("SELECT nama FROM users WHERE id=?",
                            (p["updated_by"] or p["created_by"],)).fetchone()
    rel = None
    me = current_user()
    if me["person_id"] and me["person_id"] != pid:
        rel = fam.relation(pid, me["person_id"])
    riwayat = db.execute(
        "SELECT r.*, u.nama AS oleh, u.person_id AS oleh_pid FROM riwayat r LEFT JOIN users u ON u.id=r.user_id "
        "WHERE r.person_id=? ORDER BY r.id DESC LIMIT 8", (pid,)).fetchall()
    usulan = db.execute(
        "SELECT s.*, u.nama AS oleh, u.person_id AS oleh_pid FROM usulan s JOIN users u ON u.id=s.user_id "
        "WHERE s.person_id=? AND s.status='menunggu' ORDER BY s.id", (pid,)).fetchall()
    kembar = [d for d in matching.find_duplicates(fam, claimed=claimed_ids()) if pid in (d["a"], d["b"])]
    kembar = [d for d in kembar if can_merge(d["a"], d["b"])]
    akun_orang = db.execute("SELECT id, email, peran, is_admin, wajib_ganti, last_login FROM users WHERE person_id=?", (pid,)).fetchone()
    akses_baru = session.pop("akses_baru", None) if current_user()["is_admin"] else None
    cerita = families_of(fam, pid, db.execute("SELECT * FROM keluarga").fetchall())
    tautan = db.execute("SELECT * FROM tautan_luar WHERE person_id=? ORDER BY id", (pid,)).fetchall()
    ucapan, ui = [], ultah_info(p)
    if ui and ui["boleh"]:
        ucapan = db.execute("SELECT c.*, u.nama AS oleh, u.person_id AS oleh_pid FROM ucapan c LEFT JOIN users u "
                            "ON u.id=c.user_id WHERE c.person_id=? AND c.tahun=? ORDER BY c.id", (pid, ui["tahun"])).fetchall()
    kenangan, tabur = [], {"jumlah": 0, "saya": False}
    rayak = {"jumlah": 0, "saya": False}
    pu = pekan_ultah(p)
    if pu:
        rayak["jumlah"] = db.execute("SELECT COUNT(*) FROM rayakan WHERE person_id=? AND tahun=?", (pid, pu["tahun"])).fetchone()[0]
        rayak["saya"] = bool(db.execute("SELECT 1 FROM rayakan WHERE person_id=? AND user_id=? AND tanggal=?",
                                        (pid, current_user()["id"], datetime.now().date().isoformat())).fetchone())
    if p["wafat"]:
        kenangan = db.execute("SELECT k.*, u.nama AS oleh, u.person_id AS oleh_pid FROM kenangan k "
                              "LEFT JOIN users u ON u.id=k.user_id WHERE k.person_id=? ORDER BY k.id DESC", (pid,)).fetchall()
        tabur["jumlah"] = db.execute("SELECT COUNT(*) FROM tabur_bunga WHERE person_id=?", (pid,)).fetchone()[0]
        tabur["saya"] = bool(db.execute("SELECT 1 FROM tabur_bunga WHERE person_id=? AND user_id=? AND tanggal=?",
                                        (pid, current_user()["id"], datetime.now().date().isoformat())).fetchone())
    sudah = {t["url"] for t in tautan}
    pintu = [a for a in db.execute("SELECT a.*, u.nama FROM akses_tamu a JOIN users u ON u.id=a.user_id "
                                   "WHERE a.titik_id=? AND a.aktif=1", (pid,)).fetchall()
             if akses_tamu_valid(a) and (not a["link_asal"] or a["link_asal"] not in sudah)]
    return render_template("person.html", p=p, fam=fam, akun=akun, editor=editor, rel=rel, cerita=cerita, tautan=tautan,
                           akun_orang=akun_orang, akses_baru=akses_baru,
                           pintu=pintu, kenangan=kenangan, tabur=tabur, ucapan=ucapan, ui=ui, STIKER=STIKER, rayak=rayak,
                           tebakan=parent_guesses(fam, pid), riwayat=riwayat, usulan=usulan, kembar=kembar,
                           loads=json.loads)


@app.route("/anggota/<int:pid>/ubah", methods=["GET", "POST"])
@login_required
def anggota_ubah(pid):
    person = _person(pid)
    if not person:
        abort(404)
    editable, saran = can_edit(person), {}
    if request.method == "POST":
        if editable:
            saved, errors, saran = save_person(person)
            for e in errors:
                flash(e, "warning")
            if saved:
                flash("Perubahan tersimpan ✨", "success")
                return redirect(url_for("anggota_detail", pid=pid))
        else:
            # kerabat yang lebih jauh: perubahan dikirim sebagai usulan ke pengelola
            perubahan, errors, saran = save_person(person, dry=True)
            for e in errors:
                flash(e, "warning")
            if perubahan is not None and not errors and not saran:
                if not perubahan:
                    flash("Tidak ada perubahan yang diusulkan.", "info")
                else:
                    data = {k: v for k, v in request.form.items(multi=False) if k not in ("_csrf", "foto_tmp")}
                    data["anak_bersama"] = request.form.getlist("anak_bersama")
                    db = get_db()
                    db.execute("INSERT INTO usulan (person_id, user_id, data, ringkasan) VALUES (?,?,?,?)",
                               (pid, current_user()["id"], json.dumps(data), json.dumps(perubahan)))
                    db.commit()
                    info = hak_info(person)
                    ke = info["manager"]["nama"] if info["manager"] else "admin keluarga"
                    flash(f"Usulan terkirim ke {ke} untuk ditinjau. Terima kasih sudah ikut merawat data 🙏", "success")
                    return redirect(url_for("anggota_detail", pid=pid))
    return render_template("person_form.html", v=form_values(person), person=person, saran=saran, fam=family(),
                           usul=not editable)


@app.route("/usulan/<int:uid>/<aksi>", methods=["POST"])
@login_required
def usulan_aksi(uid, aksi):
    db = get_db()
    u = db.execute("SELECT * FROM usulan WHERE id=? AND status='menunggu'", (uid,)).fetchone()
    if not u:
        abort(404)
    person = _person(u["person_id"])
    me = current_user()
    if aksi == "batal" and u["user_id"] == me["id"]:
        db.execute("UPDATE usulan SET status='dibatalkan', decided_by=?, decided_at=? WHERE id=?", (me["id"], now(), uid))
        db.commit()
        flash("Usulan dibatalkan.", "info")
        return redirect(url_for("anggota_detail", pid=person["id"]))
    if not can_edit(person):
        abort(403)
    pengusul = db.execute("SELECT nama FROM users WHERE id=?", (u["user_id"],)).fetchone()
    if aksi == "setujui":
        from werkzeug.datastructures import MultiDict
        data = json.loads(u["data"])
        kids = data.pop("anak_bersama", [])
        form = MultiDict(list(data.items()) + [("anak_bersama", k) for k in kids])
        saved, errors, _ = save_person(person, form=form, auto_new=True,
                                       catatan=f"Usulan dari {pengusul['nama'] if pengusul else 'anggota'}")
        if errors:
            for e in errors:
                flash(e, "warning")
            return redirect(url_for("anggota_detail", pid=person["id"]))
        db.execute("UPDATE usulan SET status='diterima', decided_by=?, decided_at=? WHERE id=?", (me["id"], now(), uid))
        flash("Usulan disetujui dan diterapkan ✨", "success")
    elif aksi == "tolak":
        db.execute("UPDATE usulan SET status='ditolak', decided_by=?, decided_at=? WHERE id=?", (me["id"], now(), uid))
        flash("Usulan ditolak.", "info")
    else:
        abort(404)
    db.commit()
    return redirect(url_for("anggota_detail", pid=person["id"]))


def parent_guesses(fam, pid):
    """Tebakan orang tua yang belum tercatat, dari pasangan orang tua yang sudah ada."""
    p, out = fam.people[pid], {}
    for missing, known in (("ibu", "ayah_id"), ("ayah", "ibu_id")):
        if not p[f"{missing}_id"] and p[known] in fam.people:
            need = "P" if missing == "ibu" else "L"
            lahir_anak = p["tanggal_lahir"] or (f"{p['tahun_lahir']}-07-01" if p["tahun_lahir"] else None)

            def fit(m):
                if not lahir_anak or not m["tanggal_nikah"]:
                    return 1
                if m["tanggal_nikah"] <= lahir_anak and (not m["tanggal_selesai"] or lahir_anak <= m["tanggal_selesai"]):
                    return 0
                return 2
            cands = [m for m in fam.marriages(p[known])
                     if fam.people[m["spouse"]]["jenis_kelamin"] in (need, None) and m["spouse"] != pid
                     and m["spouse"] not in fam.descendants(pid)]
            cands.sort(key=lambda m: (fit(m), m["status"] == "cerai", m["ke"]))
            out[missing] = [m["spouse"] for m in cands]
            out[missing + "_info"] = {m["spouse"]: {"m": m, "cocok": fit(m) == 0} for m in cands}
    return {k: v for k, v in out.items() if v}


def _marriage_form():
    f = request.form
    status = f.get("status") if f.get("status") in ("menikah", "cerai", "wafat") else "menikah"
    tn, ts = _date(f.get("tanggal_nikah")), _date(f.get("tanggal_selesai"))
    if tn and ts and ts < tn:
        ts = None
    return status, (tn.isoformat() if tn else None), (ts.isoformat() if ts and status != "menikah" else None)


@app.route("/anggota/<int:pid>/nikah", methods=["POST"])
@login_required
def nikah_tambah(pid):
    person, db, fam = _person(pid), get_db(), family()
    if not person or not can_edit(person):
        abort(403)
    sp = _int(request.form.get("pasangan_id"))
    nama = _clean(request.form.get("pasangan_nama"), 120)
    if sp not in fam.people:
        if not nama:
            flash("Pilih atau ketik nama pasangan terlebih dahulu.", "warning")
            return redirect(url_for("anggota_detail", pid=pid))
        jk = {"L": "P", "P": "L"}.get(person["jenis_kelamin"])
        ok, _ = scope_after(new_rows=[{"id": -9, "nama": nama, "jenis_kelamin": jk, "ayah_id": None, "ibu_id": None}],
                            new_marriages=[(pid, -9)], check=[-9])
        if not ok:
            flash(f"Pasangan {nama} di luar jangkauan peran {PERAN[user_peran()]} Anda. "
                  "Minta bantuan Super Family atau admin.", "warning")
            return redirect(url_for("anggota_detail", pid=pid))
        sp = db.execute("INSERT INTO persons (nama, jenis_kelamin, created_by) VALUES (?,?,?)",
                        (nama, jk, current_user()["id"])).lastrowid
        g.pop("family", None)
        fam = family()
    if sp == pid or (sp in fam.people and fam.close_kin(pid, sp)):
        flash("Pasangan tersebut tidak valid — tercatat sebagai kerabat dekat.", "warning")
        return redirect(url_for("anggota_detail", pid=pid))
    if sp in fam.people and fam.gender(sp) == "P" and [x for x in fam.active_spouses(sp) if x != pid]:
        other = fam.people[fam.active_spouses(sp)[0]]["nama"]
        flash(f"{fam.people[sp]['nama']} masih tercatat menikah dengan {other}. Ubah dulu status pernikahan "
              "tersebut (bercerai/berpisah) bila memang sudah berakhir.", "warning")
        return redirect(url_for("anggota_detail", pid=pid))
    ok, luar = scope_after(new_marriages=[(pid, sp)], check=[sp])
    if not ok:
        flash(f"Pasangan {luar[0]} di luar jangkauan peran {PERAN[user_peran()]} Anda. "
              "Minta bantuan Super Family atau admin.", "warning")
        return redirect(url_for("anggota_detail", pid=pid))
    status, tn, ts = _marriage_form()
    existing = fam.marriage(pid, sp)
    if existing and existing.get("id"):
        db.execute("UPDATE pernikahan SET status=?, tanggal_nikah=?, tanggal_selesai=? WHERE id=?",
                   (status, tn, ts, existing["id"]))
    else:
        ensure_marriage(db, pid, sp, status, tn, ts)
    db.commit()
    g.pop("family", None)
    if request.form.get("lanjut") == "anak":
        # tombol "Tambahkan anak" ditekan sebelum pernikahan disimpan: simpan dulu, lalu isi data anak
        jk = person["jenis_kelamin"] or {"L": "P", "P": "L"}.get(family().gender(sp) if sp in family().people else None)
        flash("Pernikahan tersimpan 💍 Sekarang lengkapi data anaknya.", "success")
        if jk == "P":
            return redirect(url_for("anggota_tambah", ibu=pid, ayah=sp))
        return redirect(url_for("anggota_tambah", ayah=pid, ibu=sp))
    flash("Riwayat pernikahan tersimpan 💍", "success")
    return redirect(url_for("anggota_detail", pid=pid) + "#nikah")


@app.route("/nikah/<int:a>/<int:b>/ubah", methods=["POST"])
@login_required
def nikah_ubah(a, b):
    fam, db = family(), get_db()
    if not (fam.marriage(a, b) and (can_edit(fam.get(a)) or can_edit(fam.get(b)))):
        abort(403)
    status, tn, ts = _marriage_form()
    ensure_marriage(db, a, b)  # pasangan yang hanya dikenali dari anak bersama dibuatkan catatannya
    db.execute("UPDATE pernikahan SET status=?, tanggal_nikah=?, tanggal_selesai=? WHERE orang1_id=? AND orang2_id=?",
               (status, tn, ts, min(a, b), max(a, b)))
    db.commit()
    flash("Riwayat pernikahan diperbarui.", "success")
    back = _int(request.form.get("kembali")) or a
    return redirect(url_for("anggota_detail", pid=back) + "#nikah")


@app.route("/nikah/<int:a>/<int:b>/hapus", methods=["POST"])
@login_required
def nikah_hapus(a, b):
    fam, db = family(), get_db()
    if not (fam.marriage(a, b) and (can_edit(fam.get(a)) or can_edit(fam.get(b)))):
        abort(403)
    m = fam.marriages(a)
    if any(x["spouse"] == b and x["children"] for x in m):
        flash("Pernikahan ini punya anak tercatat, jadi tidak dihapus. Ubah statusnya bila sudah berakhir.", "warning")
    else:
        db.execute("DELETE FROM pernikahan WHERE orang1_id=? AND orang2_id=?", (min(a, b), max(a, b)))
        db.commit()
        flash("Catatan pernikahan dihapus.", "info")
    back = _int(request.form.get("kembali")) or a
    return redirect(url_for("anggota_detail", pid=back) + "#nikah")


@app.route("/anggota/<int:pid>/ortu", methods=["POST"])
@login_required
def anggota_set_ortu(pid):
    person, fam = _person(pid), family()
    peran, ortu = request.form.get("peran"), _int(request.form.get("ortu_id"))
    if not person or not can_edit(person) or peran not in ("ayah", "ibu"):
        abort(403)
    if ortu not in parent_guesses(fam, pid).get(peran, []):
        abort(400)
    get_db().execute(f"UPDATE persons SET {peran}_id=? WHERE id=? AND {peran}_id IS NULL", (ortu, pid))
    get_db().commit()
    flash(f"{fam.people[ortu]['nama']} kini tercatat sebagai {peran} dari {person['nama']} 💚", "success")
    return redirect(request.form.get("next") or url_for("anggota_detail", pid=pid))


@app.route("/anggota/<int:pid>/hapus", methods=["POST"])
@login_required
def anggota_hapus(pid):
    person = _person(pid)
    if not person or not can_delete(person):
        abort(403)
    db = get_db()
    db.execute("DELETE FROM persons WHERE id=?", (pid,))
    db.commit()
    remove_photo(person["foto"])
    flash(f"Data {person['nama']} telah dihapus.", "info")
    return redirect(url_for("anggota"))


# === AKUN: AKSES DARI ADMIN & KATA SANDI ===
KATA_SANDI_DASAR = ["Melati", "Kenanga", "Cempaka", "Anggrek", "Teratai", "Mawar", "Kemuning", "Seroja"]


def sandi_sementara():
    return secrets.choice(KATA_SANDI_DASAR) + str(secrets.randbelow(9000) + 1000)


def link_atur_sandi(user_id):
    return url_for("atur_sandi", token=serializer.dumps({"uid": user_id}, salt="atur-sandi"), _external=True)


def kirim_akses(user, sandi, link, judul):
    isi = (f"Akun Anda di <b>{APP_NAME}</b> sudah disiapkan oleh admin keluarga.<br><br>"
           f"Email: <b>{user['email']}</b><br>Kata sandi sementara: <b>{sandi}</b><br><br>"
           "Klik tombol di bawah untuk langsung membuat kata sandi pribadi (berlaku 7 hari), "
           "atau masuk dengan kata sandi sementara lalu menggantinya.")
    return send_mail(user["email"], judul, mail_body(f"Halo {user['nama'].split(',')[0]},", isi, "Buat kata sandi saya", link))


@app.before_request
def paksa_ganti_sandi():
    user = current_user()
    if user and user["status"] == "active" and user["wajib_ganti"] and request.endpoint not in (
            "akun_sandi", "keluar", "static", "foto", "atur_sandi"):
        return redirect(url_for("akun_sandi"))
    return None


@app.route("/anggota/<int:pid>/beri-akses", methods=["POST"])
@admin_required
def beri_akses(pid):
    p, db = _person(pid), get_db()
    if not p:
        abort(404)
    email = (p["email"] or "").strip().lower()
    peran = request.form.get("peran") if request.form.get("peran") in PERAN else "anggota"
    if not email:
        flash("Isi email di biodata orang ini terlebih dahulu.", "warning")
    elif p["wafat"]:
        flash("Anggota yang telah berpulang tidak dapat diberi akun.", "warning")
    elif db.execute("SELECT 1 FROM users WHERE person_id=?", (pid,)).fetchone():
        flash("Orang ini sudah memiliki akun.", "info")
    elif db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        flash(f"Email {email} sudah dipakai akun lain. Gunakan email berbeda di biodata.", "warning")
    else:
        sandi = sandi_sementara()
        uid = db.execute("INSERT INTO users (email, password_hash, nama, status, is_admin, peran, person_id, approved_at, "
                         "wajib_ganti) VALUES (?,?,?,?,?,?,?,?,1)",
                         (email, generate_password_hash(sandi), p["nama"], "active", 0, peran, pid, now())).lastrowid
        log_riwayat(db, pid, [f"Diberi akses akun ({PERAN[peran]})"])
        db.commit()
        g.pop("claimed", None)
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        link = link_atur_sandi(uid)
        terkirim = kirim_akses(user, sandi, link, f"Akun {APP_NAME} untuk Anda")
        session["akses_baru"] = {"nama": p["nama"], "email": email, "sandi": sandi, "link": link,
                                 "wa": wa_link(p["wa"]) if p["wa"] else None, "terkirim": terkirim}
        flash(f"Akun untuk {nama_pendek(p['nama'])} dibuat sebagai {PERAN[peran]} 🎉", "success")
    return redirect(url_for("anggota_detail", pid=pid) + "#akses")


@app.route("/admin/user/<int:uid>/reset-sandi", methods=["POST"])
@admin_required
def reset_sandi(uid):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=? AND peran<>'tamu'", (uid,)).fetchone()
    if not user or user["id"] == current_user()["id"]:
        abort(400)
    sandi = sandi_sementara()
    db.execute("UPDATE users SET password_hash=?, wajib_ganti=1 WHERE id=?", (generate_password_hash(sandi), uid))
    db.commit()
    link = link_atur_sandi(uid)
    terkirim = kirim_akses(user, sandi, link, f"Kata sandi {APP_NAME} Anda direset")
    wa = None
    if user["person_id"]:
        p = _person(user["person_id"])
        wa = wa_link(p["wa"]) if p and p["wa"] else None
    session["akses_baru"] = {"nama": user["nama"], "email": user["email"], "sandi": sandi, "link": link, "wa": wa,
                             "terkirim": terkirim, "reset": True}
    flash(f"Kata sandi {user['nama'].split(',')[0]} direset.", "success")
    return redirect(url_for("admin") + "#akses")


@app.route("/atur-sandi/<token>", methods=["GET", "POST"])
def atur_sandi(token):
    try:
        data = serializer.loads(token, salt="atur-sandi", max_age=7 * 86400)
    except SignatureExpired:
        return render_template("auth/status.html", status="expired")
    except BadSignature:
        abort(404)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=? AND status='active'", (data["uid"],)).fetchone()
    if not user:
        abort(404)
    if request.method == "POST":
        baru, ulang = request.form.get("baru") or "", request.form.get("ulang") or ""
        if len(baru) < 8:
            flash("Kata sandi minimal 8 karakter.", "warning")
        elif baru != ulang:
            flash("Konfirmasi kata sandi belum sama.", "warning")
        else:
            db.execute("UPDATE users SET password_hash=?, wajib_ganti=0, last_login=? WHERE id=?",
                       (generate_password_hash(baru), now(), user["id"]))
            db.commit()
            session.clear()
            session["uid"] = user["id"]
            flash("Kata sandi tersimpan. Selamat datang di silsilah keluarga 🌿", "success")
            return redirect(url_for("beranda"))
    return render_template("auth/sandi.html", user=user, lewat_link=True)


@app.route("/akun/sandi", methods=["GET", "POST"])
@login_required
def akun_sandi():
    if is_tamu():
        abort(403)
    user, db = current_user(), get_db()
    if request.method == "POST":
        lama, baru, ulang = (request.form.get(k) or "" for k in ("lama", "baru", "ulang"))
        if not check_password_hash(user["password_hash"], lama):
            flash("Kata sandi saat ini belum cocok.", "warning")
        elif len(baru) < 8:
            flash("Kata sandi baru minimal 8 karakter.", "warning")
        elif baru != ulang:
            flash("Konfirmasi kata sandi baru belum sama.", "warning")
        elif baru == lama:
            flash("Gunakan kata sandi yang berbeda dari sebelumnya.", "warning")
        else:
            db.execute("UPDATE users SET password_hash=?, wajib_ganti=0 WHERE id=?", (generate_password_hash(baru), user["id"]))
            db.commit()
            flash("Kata sandi berhasil diganti 🔐", "success")
            return redirect(url_for("beranda"))
    return render_template("auth/sandi.html", user=user, lewat_link=False)


# === UCAPAN ULANG TAHUN ===
@app.route("/anggota/<int:pid>/ucapan", methods=["POST"])
@login_required
def ucapan_kirim(pid):
    p, me, db = _person(pid), current_user(), get_db()
    ui = ultah_info(p)
    if not p or is_tamu() or me["person_id"] == pid or not ui or not ui["boleh"]:
        abort(403)
    pesan = (request.form.get("pesan") or "").strip()[:600]
    stiker = request.form.get("stiker") if request.form.get("stiker") in STIKER else "🎂"
    if len(pesan) < 2:
        flash("Tuliskan ucapan terlebih dahulu.", "warning")
    else:
        lama = db.execute("SELECT id FROM ucapan WHERE person_id=? AND user_id=? AND tahun=?", (pid, me["id"], ui["tahun"])).fetchone()
        if lama:   # satu ucapan per orang per tahun: yang baru menggantikan yang lama
            db.execute("UPDATE ucapan SET pesan=?, stiker=?, dibaca=0, created_at=? WHERE id=?", (pesan, stiker, now(), lama["id"]))
        else:
            db.execute("INSERT INTO ucapan (person_id, user_id, tahun, stiker, pesan) VALUES (?,?,?,?,?)",
                       (pid, me["id"], ui["tahun"], stiker, pesan))
        db.commit()
        nm = nama_pendek(p["nama"])
        flash(f"{stiker} Ucapan untuk {nm} tersimpan" + (" dan akan menjadi kejutan di hari ulang tahunnya." if ui["hari"] > 0 else "."), "success")
    return redirect(request.form.get("next") or url_for("anggota_detail", pid=pid))


@app.route("/anggota/<int:pid>/rayakan", methods=["POST"])
@login_required
def rayakan_ultah(pid):
    p = _person(pid)
    pu = pekan_ultah(p)
    if not p or not pu or is_tamu():
        abort(403)
    db = get_db()
    db.execute("INSERT OR IGNORE INTO rayakan (person_id, user_id, tahun, tanggal) VALUES (?,?,?,?)",
               (pid, current_user()["id"], pu["tahun"], datetime.now().date().isoformat()))
    db.commit()
    return jsonify({"jumlah": db.execute("SELECT COUNT(*) FROM rayakan WHERE person_id=? AND tahun=?",
                                         (pid, pu["tahun"])).fetchone()[0]})


@app.route("/ucapan/baca", methods=["POST"])
@login_required
def ucapan_baca():
    me = current_user()
    if me["person_id"]:
        db = get_db()
        db.execute("UPDATE ucapan SET dibaca=1 WHERE person_id=? AND tahun<=?", (me["person_id"], datetime.now().year))
        db.commit()
    return jsonify({"ok": True})


# === KENANGAN UNTUK YANG TELAH BERPULANG ===
@app.route("/anggota/<int:pid>/kenangan", methods=["POST"])
@login_required
def kenangan_tulis(pid):
    p = _person(pid)
    if not p or not p["wafat"] or is_tamu():
        abort(403)
    pesan = (request.form.get("pesan") or "").strip()[:800]
    if len(pesan) < 3:
        flash("Tuliskan kenangan atau doa terlebih dahulu.", "warning")
    else:
        db = get_db()
        db.execute("INSERT INTO kenangan (person_id, user_id, pesan) VALUES (?,?,?)", (pid, current_user()["id"], pesan))
        db.commit()
        flash("Terima kasih, kenangan Anda tersimpan 🤍", "success")
    return redirect(url_for("anggota_detail", pid=pid) + "#kenangan")


@app.route("/kenangan/<int:kid>/hapus", methods=["POST"])
@login_required
def kenangan_hapus(kid):
    db, me = get_db(), current_user()
    k = db.execute("SELECT * FROM kenangan WHERE id=?", (kid,)).fetchone()
    if not k or not (me["is_admin"] or k["user_id"] == me["id"]):
        abort(403)
    db.execute("DELETE FROM kenangan WHERE id=?", (kid,))
    db.commit()
    flash("Kenangan dihapus.", "info")
    return redirect(url_for("anggota_detail", pid=k["person_id"]) + "#kenangan")


@app.route("/anggota/<int:pid>/tabur", methods=["POST"])
@login_required
def tabur_bunga(pid):
    p = _person(pid)
    if not p or not p["wafat"] or is_tamu():
        abort(403)
    db = get_db()
    db.execute("INSERT OR IGNORE INTO tabur_bunga (person_id, user_id, tanggal) VALUES (?,?,?)",
               (pid, current_user()["id"], datetime.now().date().isoformat()))
    db.commit()
    return jsonify({"jumlah": db.execute("SELECT COUNT(*) FROM tabur_bunga WHERE person_id=?", (pid,)).fetchone()[0]})


# === TAUTAN KE SILSILAH LAIN & AKSES TAMU ===
def _url_aman(url):
    url = (url or "").strip()
    return url if re.match(r"^https?://[^\s<>\"']+$", url) else None


@app.route("/anggota/<int:pid>/tautan", methods=["POST"])
@admin_required
def tautan_tambah(pid):
    if not _person(pid):
        abort(404)
    label, url = _clean(request.form.get("label"), 120), _url_aman(request.form.get("url"))
    if not label or not url:
        flash("Isi nama silsilah dan alamat link yang diawali http:// atau https://", "warning")
    else:
        db = get_db()
        db.execute("INSERT INTO tautan_luar (person_id, label, url, catatan, created_by) VALUES (?,?,?,?,?)",
                   (pid, label, url, _clean(request.form.get("catatan"), 300), current_user()["id"]))
        log_riwayat(db, pid, [f"Ditautkan ke silsilah lain: {label}"])
        db.commit()
        flash("Tautan ke silsilah lain tersimpan 🔗", "success")
    return redirect(url_for("anggota_detail", pid=pid) + "#tautan")


@app.route("/tautan/<int:tid>/hapus", methods=["POST"])
@admin_required
def tautan_hapus(tid):
    db = get_db()
    t = db.execute("SELECT * FROM tautan_luar WHERE id=?", (tid,)).fetchone()
    if not t:
        abort(404)
    db.execute("DELETE FROM tautan_luar WHERE id=?", (tid,))
    db.commit()
    flash("Tautan dihapus.", "info")
    return redirect(url_for("anggota_detail", pid=t["person_id"]))


@app.template_global()
def tebak_link(asal):
    """'jajar.do' / 'Keluarga A — serverA.com' -> 'https://jajar.do' bila ada nama domain di dalamnya."""
    m = re.search(r"(https?://[^\s]+|[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s]*)?)", (asal or "").lower())
    if not m:
        return ""
    u = m.group(1)
    return u if u.startswith("http") else "https://" + u


def pasang_tautan_balik(db, titik_id, asal, url, sumber):
    """Profil titik masuk mendapat tautan ke silsilah asal tamu (bila belum ada link yang sama)."""
    url = _url_aman(url)
    if not url or titik_id is None:
        return False
    if db.execute("SELECT 1 FROM tautan_luar WHERE person_id=? AND url=?", (titik_id, url)).fetchone():
        return True
    label = asal or url.split("/")[2]
    db.execute("INSERT INTO tautan_luar (person_id, label, url, catatan, created_by) VALUES (?,?,?,?,?)",
               (titik_id, label, url, f"Tautan balik dari akses tamu {sumber}", current_user()["id"] if current_user() else None))
    log_riwayat(db, titik_id, [f"Ditautkan ke silsilah lain: {label}"])
    return True


def buat_akses_tamu(nama, asal, titik_id, hari, catatan=None, email=None, link_asal=None):
    """Membuat akun tamu baca-saja beserta link masuk rahasianya."""
    db = get_db()
    token = secrets.token_urlsafe(24)
    uid = db.execute("INSERT INTO users (email, password_hash, nama, status, is_admin, peran, catatan_daftar) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (f"tamu-{token[:10].lower()}@tamu.local", generate_password_hash(secrets.token_urlsafe(32)),
                      nama, "active", 0, "tamu", email)).lastrowid
    sampai = (datetime.now().date() + timedelta(days=hari)).isoformat() if hari else None
    aid = db.execute("INSERT INTO akses_tamu (user_id, titik_id, token, asal, catatan, berlaku_sampai, dibuat_oleh, link_asal) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, titik_id, token, asal, catatan, sampai, current_user()["id"],
                                                 _url_aman(link_asal))).lastrowid
    if link_asal:
        pasang_tautan_balik(db, titik_id, asal, link_asal, nama)
    db.commit()
    return aid, url_for("tamu_masuk", token=token, _external=True)


@app.route("/tamu/minta", methods=["GET", "POST"])
def tamu_minta():
    """Halaman publik: penjelajah dari silsilah lain meminta akses tamu."""
    if request.method == "POST":
        f = request.form
        nama = _clean(f.get("nama"), 120)
        if not nama:
            flash("Nama wajib diisi.", "warning")
        else:
            db = get_db()
            db.execute("INSERT INTO permintaan_tamu (nama, email, asal, tujuan, alasan, link_asal) VALUES (?,?,?,?,?,?)",
                       (nama, _clean(f.get("email"), 160), _clean(f.get("asal"), 160), _clean(f.get("tujuan"), 200),
                        _clean(f.get("alasan"), 600), _url_aman(f.get("link_asal"))))
            db.commit()
            for adm in db.execute("SELECT email FROM users WHERE is_admin=1 AND status='active'"):
                send_mail(adm["email"], f"Permintaan akses tamu: {nama}",
                          mail_body("Ada penjelajah dari silsilah lain", f"<b>{nama}</b> dari {f.get('asal') or '-'} "
                                    f"ingin menjelajah: {f.get('tujuan') or '-'}", "Tinjau", url_for("admin_tamu", _external=True)))
            return render_template("tamu_minta.html", terkirim=True)
    return render_template("tamu_minta.html", terkirim=False, tujuan=request.args.get("tujuan", ""))


@app.route("/tamu/<token>")
def tamu_masuk(token):
    db = get_db()
    a = db.execute("SELECT * FROM akses_tamu WHERE token=?", (token,)).fetchone()
    if not akses_tamu_valid(a):
        return render_template("auth/status.html", status="tamu_habis")
    session.clear()
    session["uid"] = a["user_id"]
    db.execute("UPDATE akses_tamu SET terakhir=? WHERE id=?", (now(), a["id"]))
    db.commit()
    ke = request.args.get("ke", type=int)
    g.pop("user", None)
    if ke:
        g.akses_tamu = a
        if ke in tamu_scope():
            return redirect(url_for("anggota_detail", pid=ke))
    return redirect(url_for("tamu_beranda"))


@app.route("/jelajah")
@login_required
def tamu_beranda():
    if not is_tamu():
        return redirect(url_for("beranda"))
    fam, a = family(), akses_tamu_saya()
    titik = fam.get(a["titik_id"]) if a else None
    rows = [r for r in get_db().execute("SELECT * FROM keluarga").fetchall() if r["leluhur_id"] in tamu_scope()]
    return render_template("tamu_beranda.html", fam=fam, a=a, titik=titik, jumlah=len(tamu_scope()),
                           cerita=[{"k": k, "stats": keluarga_stats(fam, k["leluhur_id"])} for k in rows])


@app.route("/admin/tamu")
@admin_required
def admin_tamu():
    db = get_db()
    akses = db.execute("SELECT a.*, u.nama, p.nama AS titik_nama FROM akses_tamu a JOIN users u ON u.id=a.user_id "
                       "LEFT JOIN persons p ON p.id=a.titik_id ORDER BY a.aktif DESC, a.id DESC").fetchall()
    minta = db.execute("SELECT * FROM permintaan_tamu WHERE status='menunggu' ORDER BY id").fetchall()
    return render_template("admin/tamu.html", akses=akses, minta=minta, fam=family(),
                           baru=session.pop("tamu_link_baru", None), hari_ini_iso=datetime.now().date().isoformat())


@app.route("/admin/tamu/buat", methods=["POST"])
@admin_required
def admin_tamu_buat():
    f = request.form
    titik = _int(f.get("titik_id"))
    nama = _clean(f.get("nama"), 120)
    if not nama or titik not in family().people:
        flash("Isi nama tamu dan pilih titik masuk (orang yang akan dijelajahi).", "warning")
        return redirect(url_for("admin_tamu"))
    hari = _int(f.get("hari"))
    mid = _int(f.get("minta_id"))
    aid, link = buat_akses_tamu(nama, _clean(f.get("asal"), 160), titik, hari, _clean(f.get("catatan"), 300),
                                _clean(f.get("email"), 160), f.get("link_asal"))
    db = get_db()
    if mid:
        m = db.execute("SELECT * FROM permintaan_tamu WHERE id=?", (mid,)).fetchone()
        db.execute("UPDATE permintaan_tamu SET status='disetujui', akses_id=? WHERE id=?", (aid, mid))
        db.commit()
        if m and m["email"]:
            send_mail(m["email"], f"Akses tamu ke {APP_NAME}",
                      mail_body(f"Halo {m['nama']},", "Permintaan Anda disetujui. Gunakan tautan berikut untuk menjelajah "
                                "silsilah sebagai tamu (baca-saja).", "Mulai menjelajah", link))
    session["tamu_link_baru"] = {"nama": nama, "link": link, "titik": family().people[titik]["nama"],
                                 "link_titik": link + f"?ke={titik}"}
    flash(f"Akses tamu untuk {nama} dibuat. Salin linknya dan berikan kepada admin silsilah asalnya.", "success")
    return redirect(url_for("admin_tamu"))


@app.route("/admin/tamu/<int:aid>/tautan", methods=["POST"])
@admin_required
def admin_tamu_tautan(aid):
    db = get_db()
    a = db.execute("SELECT a.*, u.nama FROM akses_tamu a JOIN users u ON u.id=a.user_id WHERE a.id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    url = request.form.get("link_asal")
    if pasang_tautan_balik(db, a["titik_id"], a["asal"], url, a["nama"]):
        db.execute("UPDATE akses_tamu SET link_asal=? WHERE id=?", (_url_aman(url), aid))
        db.commit()
        flash("Tautan balik terpasang di profil titik masuk 🔗", "success")
    else:
        flash("Link harus diawali http:// atau https://", "warning")
    return redirect(url_for("admin_tamu"))


@app.route("/admin/tamu/<int:aid>/<aksi>", methods=["POST"])
@admin_required
def admin_tamu_aksi(aid, aksi):
    db = get_db()
    if aksi == "cabut":
        db.execute("UPDATE akses_tamu SET aktif=0 WHERE id=?", (aid,))
        flash("Akses tamu dicabut.", "info")
    elif aksi == "aktifkan":
        db.execute("UPDATE akses_tamu SET aktif=1 WHERE id=?", (aid,))
        flash("Akses tamu diaktifkan kembali.", "success")
    elif aksi == "tolak":     # aid = id permintaan
        db.execute("UPDATE permintaan_tamu SET status='ditolak' WHERE id=?", (aid,))
        flash("Permintaan akses ditolak.", "info")
    else:
        abort(404)
    db.commit()
    return redirect(url_for("admin_tamu"))


@app.route("/keluarga")
@login_required
def keluarga_daftar():
    fam = family()
    rows = get_db().execute("SELECT * FROM keluarga ORDER BY judul COLLATE NOCASE").fetchall()
    if is_tamu():
        rows = [r for r in rows if r["leluhur_id"] in tamu_scope()]
    me = current_user()["person_id"]
    induk = {r["id"]: story_parent(fam, rows, r) for r in rows}
    induk_semua = {r["id"]: story_parents(fam, rows, r) for r in rows}

    def susun(parent_id, level):
        out = []
        for r in rows:
            if (induk[r["id"]]["id"] if induk[r["id"]] else None) == parent_id:
                out.append({"k": r, "level": level, "stats": keluarga_stats(fam, r["leluhur_id"]),
                            "posisi": lineage_info(fam, me, r["leluhur_id"]), "induk": induk_semua[r["id"]]})
                out += susun(r["id"], level + 1)
        return out
    items = susun(None, 0)
    return render_template("keluarga_daftar.html", items=items, fam=fam, kosong=garis_kosong(fam, me, rows))


@app.route("/keluarga/<int:kid>")
@login_required
def keluarga_lihat(kid):
    db, fam = get_db(), family()
    k = db.execute("SELECT * FROM keluarga WHERE id=?", (kid,)).fetchone()
    if not k or not tamu_boleh(k["leluhur_id"]):
        abort(404)
    penulis = db.execute("SELECT nama FROM users WHERE id=?", (k["updated_by"] or k["created_by"],)).fetchone()
    penjaga = db.execute("SELECT nama, person_id FROM users WHERE id=?", (k["created_by"],)).fetchone()
    rows = db.execute("SELECT * FROM keluarga").fetchall()
    induk = story_parents(fam, rows, k)
    cabang_cerita = story_children(fam, rows, k)
    # cerita cabang per anak leluhur (agar kartu cabang bisa langsung menuju ceritanya)
    per_anak = {}
    for r in cabang_cerita:
        for c in fam.children(k["leluhur_id"]):
            if r["leluhur_id"] == c or r["leluhur_id"] in fam.spouses(c) or r["leluhur_id"] in fam.descendants(c):
                per_anak.setdefault(c, []).append(r)
    me = current_user()["person_id"]
    penjaga_rel = None
    if penjaga and penjaga["person_id"] in fam.people:
        r = fam.relation(penjaga["person_id"], k["leluhur_id"]) if k["leluhur_id"] in fam.people else {}
        penjaga_rel = r["ab"]["label"].lower() if r.get("ab") else None
    return render_template("keluarga.html", k=k, fam=fam, stats=keluarga_stats(fam, k["leluhur_id"]),
                           tonggak=parse_tonggak(k["tonggak"]), penulis=penulis, penjaga=penjaga,
                           penjaga_rel=penjaga_rel, induk=induk, cabang_cerita=cabang_cerita, per_anak=per_anak,
                           posisi=lineage_info(fam, me, k["leluhur_id"]))


def _simpan_keluarga(k=None):
    f, db, user = request.form, get_db(), current_user()
    judul = _clean(f.get("judul"), 120)
    lid = _int(f.get("leluhur_lain")) or _int(f.get("leluhur_id"))
    errors = []
    if not judul:
        errors.append("Judul keluarga wajib diisi, mis. “Keluarga Besar Abdul Djabar”.")
    if lid is not None and lid not in family().people:
        errors.append("Leluhur yang dipilih tidak ditemukan.")
    if lid is None:
        errors.append("Pilih leluhur (kepala keluarga) agar cerita terhubung ke garis keturunannya.")
    else:
        fam = family()
        for r in get_db().execute("SELECT * FROM keluarga").fetchall():
            if k is not None and r["id"] == k["id"]:
                continue
            if r["leluhur_id"] == lid or r["leluhur_id"] in fam.spouses(lid):
                penjaga = get_db().execute("SELECT nama FROM users WHERE id=?", (r["created_by"],)).fetchone()
                errors.append(f"Keluarga ini sudah punya cerita: “{r['judul']}”"
                              f"{' yang dijaga ' + penjaga['nama'] if penjaga else ''}. Silakan lengkapi cerita tersebut.")
                break
    if not can_tulis_cerita(k, leluhur_id=lid):
        errors.append("Super Family hanya bisa menulis cerita garis keturunannya sendiri "
                      "(leluhur tersebut adalah orang tua, kakek, atau leluhur Anda).")
    if errors:
        return None, errors
    data = dict(judul=judul, tagline=_clean(f.get("tagline"), 160), asal=_clean(f.get("asal"), 120), leluhur_id=lid,
                mukadimah=(f.get("mukadimah") or "").strip()[:12000] or None, kutipan=_clean(f.get("kutipan"), 500),
                kutipan_oleh=_clean(f.get("kutipan_oleh"), 120), tonggak=(f.get("tonggak") or "").strip()[:4000] or None)
    file = request.files.get("sampul")
    if file and file.filename:
        try:
            data["sampul"] = process_cover(file)
        except ValueError as e:
            return None, [str(e)]
        if k and k["sampul"]:
            remove_photo(k["sampul"])
    elif k and f.get("hapus_sampul"):
        remove_photo(k["sampul"])
        data["sampul"] = None
    if k:
        data.update(updated_by=user["id"], updated_at=now())
        db.execute(f"UPDATE keluarga SET {', '.join(f'{c}=?' for c in data)} WHERE id=?", (*data.values(), k["id"]))
        kid = k["id"]
    else:
        data.update(created_by=user["id"], updated_by=user["id"])
        kid = db.execute(f"INSERT INTO keluarga ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
                         tuple(data.values())).lastrowid
    db.commit()
    return kid, []


@app.route("/keluarga/tulis", methods=["GET", "POST"])
@login_required
def keluarga_tulis():
    if not can_tulis_cerita():
        flash("Cerita keluarga ditulis oleh Super Family atau admin. Anda tetap bisa membacanya 😊", "info")
        return redirect(url_for("keluarga_daftar"))
    if request.method == "POST":
        kid, errors = _simpan_keluarga()
        for e in errors:
            flash(e, "warning")
        if kid:
            flash("Cerita keluarga tersimpan ✨", "success")
            return redirect(url_for("keluarga_lihat", kid=kid))
    fam, db = family(), get_db()
    rows = db.execute("SELECT * FROM keluarga").fetchall()
    me = current_user()["person_id"]
    kandidat = [c for c in kandidat_leluhur(fam, me, rows) if current_user()["is_admin"] or can_tulis_cerita(leluhur_id=c["id"])]
    if request.method == "POST":
        v = dict(request.form)
    else:
        pilih = request.args.get("leluhur", type=int)
        if pilih is None:   # bawaan: leluhur garis yang belum punya cerita, lalu kandidat pertama yang kosong
            gk = garis_kosong(fam, me, rows)
            pilih = gk[0]["leluhur"] if gk else next((c["id"] for c in kandidat[1:] + kandidat[:1] if not c["cerita"]), None)
        v = {"leluhur_id": pilih}
        if pilih in fam.people and not v.get("judul"):
            v["judul_saran"] = f"Keluarga Besar {nama_pendek(fam.people[pilih]['nama'])}"
    return render_template("keluarga_form.html", k=None, v=v, fam=fam, kandidat=kandidat)


@app.route("/keluarga/<int:kid>/ubah", methods=["GET", "POST"])
@login_required
def keluarga_ubah(kid):
    k = get_db().execute("SELECT * FROM keluarga WHERE id=?", (kid,)).fetchone()
    if not k:
        abort(404)
    if not can_tulis_cerita(k):
        abort(403)
    if request.method == "POST":
        ok, errors = _simpan_keluarga(k)
        for e in errors:
            flash(e, "warning")
        if ok:
            flash("Cerita keluarga diperbarui ✨", "success")
            return redirect(url_for("keluarga_lihat", kid=kid))
    v = dict(request.form) if request.method == "POST" else dict(k)
    return render_template("keluarga_form.html", k=k, v=v, fam=family(), kandidat=[])


@app.route("/keluarga/<int:kid>/hapus", methods=["POST"])
@login_required
def keluarga_hapus(kid):
    db = get_db()
    k = db.execute("SELECT * FROM keluarga WHERE id=?", (kid,)).fetchone()
    user = current_user()
    if not k or not (user["is_admin"] or k["created_by"] == user["id"]):
        abort(403)
    remove_photo(k["sampul"])
    db.execute("DELETE FROM keluarga WHERE id=?", (kid,))
    db.commit()
    flash("Cerita keluarga dihapus.", "info")
    return redirect(url_for("keluarga_daftar"))


# === BUKU SILSILAH (siap cetak / simpan PDF) ===
ROMAWI = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV",
          "XVI", "XVII", "XVIII", "XIX", "XX"]


@app.template_global()
def generasi_sebutan(n):
    from family import descendant_term
    return "Leluhur" if n == 0 else descendant_term(n)


def pilihan_buku(fam, rows):
    """Leluhur yang bisa dijadikan pangkal buku: leluhur cerita keluarga & leluhur teratas yang punya keturunan."""
    out, seen = [], set()
    for r in rows:
        if r["leluhur_id"] in fam.people and r["leluhur_id"] not in seen:
            seen.add(r["leluhur_id"])
            out.append({"id": r["leluhur_id"], "judul": r["judul"], "n": len(fam.descendants(r["leluhur_id"]))})
    for pid in fam.people:
        if pid in seen or fam.parents(pid) or not fam.children(pid):
            continue
        if any(s in seen for s in fam.spouses(pid)):   # pasangan leluhur yang sudah terdaftar
            continue
        seen.add(pid)
        out.append({"id": pid, "judul": f"Keluarga Besar {nama_pendek(fam.people[pid]['nama'])}",
                    "n": len(fam.descendants(pid))})
    return sorted(out, key=lambda x: -x["n"])


def susun_buku(fam, lid, rows):
    """Isi buku: setiap keturunan diberi nomor silsilah (1, 1.2, 1.2.3 — anak ke-3 dari anak ke-2 leluhur),
    dikelompokkan per bab (satu bab untuk setiap anak leluhur), lengkap dengan pasangan & anak-anaknya."""
    nomor, urut, gen = {lid: "1"}, [lid], {lid: 0}
    for x in urut:                                # BFS memberi nomor; keturunan yang muncul dua kali pakai nomor pertama
        for i, c in enumerate(fam.children(x), 1):
            if c not in nomor:
                nomor[c], gen[c] = f"{nomor[x]}.{i}", gen[x] + 1
                urut.append(c)

    def kunci(n):
        return [int(t) for t in n.split(".")]

    cerita_of = {}
    for r in rows:
        if r["leluhur_id"] in fam.people:
            for x in [r["leluhur_id"]] + fam.spouses(r["leluhur_id"]):
                cerita_of.setdefault(x, r)

    def ortu_teks(pid):
        p = fam.people[pid]
        ortu = [nama_pendek(fam.people[x]["nama"], 4) for x in fam.parents(pid)]
        if not ortu:
            return None
        kata = {"L": "putra", "P": "putri"}.get(fam.gender(pid), "anak")
        return f"{kata} dari {' & '.join(ortu)}"

    def entri(pid):
        nikah, tercakup = [], set()
        for m in fam.marriages(pid):
            anak = [(nomor[c], fam.people[c]) for c in m["children"] if c in nomor]
            tercakup |= set(m["children"])
            s = m["spouse"]
            nikah.append({"p": fam.people[s], "ke": m["ke"], "status": m["status"], "status_label": m["status_label"],
                          "tanggal": m.get("tanggal_nikah"), "tanggal_selesai": m.get("tanggal_selesai"),
                          "nomor": nomor.get(s), "ortu": ortu_teks(s) if s not in nomor else None, "anak": anak})
        lain = [(nomor[c], fam.people[c]) for c in fam.children(pid) if c not in tercakup and c in nomor]
        if lain:
            nikah.append({"p": None, "ke": None, "anak": lain})
        p = fam.people[pid]
        return {"nomor": nomor[pid], "gen": gen[pid], "p": p, "nikah": nikah, "jumlah_nikah": len(fam.marriages(pid)),
                "ortu": ortu_teks(pid) if pid != lid else None, "cerita": cerita_of.get(pid) if pid != lid else None,
                "anak_ke": nomor[pid].split(".")[-1] if pid != lid else None}

    semua = sorted(nomor, key=lambda x: kunci(nomor[x]))
    bab = [{"judul": "Sang Leluhur", "kepala": lid, "entri": [entri(lid)]}]
    for c in fam.children(lid):
        if nomor.get(c, "").count(".") != 1:
            continue
        isi = [entri(x) for x in semua if nomor[x] == nomor[c] or nomor[x].startswith(nomor[c] + ".")]
        bab.append({"judul": f"Keluarga {nama_pendek(fam.people[c]['nama'], 3)}", "kepala": c, "entri": isi})
    for i, b in enumerate(bab):
        b["romawi"] = ROMAWI[i] if i < len(ROMAWI) else str(i + 1)

    # pasangan keturunan (yang bukan keturunan) juga dimuat di indeks
    pasangan = {}
    for x in semua:
        for s in fam.spouses(x):
            if s not in nomor:
                pasangan.setdefault(s, []).append(nomor[x])
    indeks = [{"p": fam.people[x], "ref": nomor[x], "pasangan": False} for x in semua]
    indeks += [{"p": fam.people[s], "ref": ", ".join(refs), "pasangan": True} for s, refs in pasangan.items()]
    indeks.sort(key=lambda r: r["p"]["nama"].lower())
    huruf = {}
    for r in indeks:
        huruf.setdefault((r["p"]["nama"][:1] or "#").upper(), []).append(r)

    orang = [fam.people[x] for x in semua] + [fam.people[s] for s in pasangan]
    per_gen = {}
    for x in semua:
        d = per_gen.setdefault(gen[x], {"gen": gen[x], "nama": generasi_sebutan(gen[x]), "jumlah": 0, "hidup": 0, "pasangan": 0})
        d["jumlah"] += 1
        d["hidup"] += 0 if fam.people[x]["wafat"] else 1
        d["pasangan"] += sum(1 for s in fam.spouses(x) if s not in nomor)
    kalender = {}
    for p in orang:
        d = _date(p["tanggal_lahir"])
        if d and not p["wafat"]:
            kalender.setdefault(d.month, []).append({"p": p, "hari": d.day, "nomor": nomor.get(p["id"])})
    for v in kalender.values():
        v.sort(key=lambda t: t["hari"])
    berpulang = sorted([p for p in orang if p["wafat"]], key=lambda p: (p["tahun_wafat"] or 9999, p["nama"]))

    def bagan(pid, depth=0, seen=None):
        seen = seen if seen is not None else set()
        seen.add(pid)
        out = [{"nomor": nomor[pid], "p": fam.people[pid], "depth": depth,
                "pasangan": [fam.people[s] for s in fam.spouses(pid)]}]
        for c in fam.children(pid):
            if c not in seen and nomor.get(c, "").rsplit(".", 1)[0] == nomor[pid]:
                out += bagan(c, depth + 1, seen)
        return out

    return {"nomor": nomor, "bab": bab, "indeks": huruf, "per_gen": [per_gen[k] for k in sorted(per_gen)],
            "kalender": [(BULAN[m - 1], kalender[m]) for m in sorted(kalender)], "berpulang": berpulang,
            "bagan": bagan(lid), "jumlah": len(semua), "keturunan": len(semua) - 1,
            "rujukan": {**{s: "psg. " + ", ".join(r) for s, r in pasangan.items()}, **nomor}, "jumlah_pasangan": len(pasangan),
            "generasi": max(gen.values()) + 1, "hidup": sum(1 for x in semua if not fam.people[x]["wafat"])}


@app.route("/buku")
@login_required
def buku():
    db, fam, user = get_db(), family(), current_user()
    rows = db.execute("SELECT * FROM keluarga ORDER BY id").fetchall()
    pilihan = pilihan_buku(fam, rows)
    lid = request.args.get("leluhur", type=int)
    if lid not in fam.people:
        lid = pilihan[0]["id"] if pilihan else user["person_id"]
    if lid not in fam.people:
        flash("Belum ada data silsilah untuk dijadikan buku.", "info")
        return redirect(url_for("beranda"))
    cerita = next((r for r in rows if r["leluhur_id"] == lid), None) or \
        next((r for r in rows if r["leluhur_id"] in fam.spouses(lid)), None)
    opsi = {"foto": request.args.get("foto", "1") == "1",
            "lahir": request.args.get("lahir", "lengkap"),                         # lengkap | tahun
            "kontak": bool(user["is_admin"]) and request.args.get("kontak") == "1"}  # kontak hanya atas pilihan admin
    L = fam.people[lid]
    judul = cerita["judul"] if cerita else f"Keluarga Besar {nama_pendek(L['nama'], 3)}"
    return render_template("buku.html", fam=fam, L=L, lid=lid, cerita=cerita, judul=judul, opsi=opsi,
                           pilihan=pilihan, buku=susun_buku(fam, lid, rows),
                           tonggak=parse_tonggak(cerita["tonggak"]) if cerita else [],
                           penyusun=user["nama"], dicetak=datetime.now(), BULAN=BULAN)


@app.template_global()
def lahir_buku(p, mode="lengkap"):
    """Tanggal lahir di buku; untuk yang masih hidup bisa diringkas menjadi tahun saja (privasi)."""
    if mode == "tahun" and not p["wafat"]:
        return str(p["tahun_lahir"]) if p["tahun_lahir"] else ""
    return lahir(p)


@app.route("/pohon/<int:pid>")
@login_required
def pohon(pid):
    fam = family()
    if pid not in fam.people or not tamu_boleh(pid):
        abort(404)
    mode = request.args.get("mode", "keluarga")
    depth = max(1, min(request.args.get("kedalaman", 4, type=int), 10))
    saudara = request.args.get("saudara", "1") != "0"
    pasangan = request.args.get("pasangan", "1") != "0"
    if mode == "leluhur":
        tree = fam.ancestor_tree(pid, depth, with_siblings=saudara, with_spouses=pasangan)
    elif mode == "keluarga":   # bawaan: orang tua, saudara, dan anak-anak
        tree = fam.family_tree(pid)
    else:
        mode = "keturunan"
        tree = fam.descendant_tree(pid, depth)
    # cari leluhur tertinggi dari garis ayah sebagai titik awal yang disarankan
    top, seen = pid, {pid}
    while fam.parents(top) and fam.parents(top)[0] not in seen:
        top = fam.parents(top)[0]
        seen.add(top)
    return render_template("pohon.html", p=fam.get(pid), tree=tree, mode=mode, depth=depth, fam=fam,
                           top=fam.get(top), saudara=saudara, pasangan=pasangan)


@app.route("/bandingkan")
@login_required
def bandingkan():
    fam, me = family(), current_user()
    a = request.args.get("a", type=int) or me["person_id"]
    b = request.args.get("b", type=int)
    if is_tamu():
        a = a if a in tamu_scope() else (akses_tamu_saya()["titik_id"] if akses_tamu_saya() else None)
        b = b if b in tamu_scope() else None
    pa, pb = fam.get(a), fam.get(b)
    rel = fam.relation(a, b) if pa and pb else None
    return render_template("bandingkan.html", pa=pa, pb=pb, rel=rel, fam=fam)


@app.route("/api/cari")
@login_required
def api_cari():
    """Pencarian anggota yang toleran salah ketik/ejaan; bila `peran` diisi, kandidat diurutkan
    dengan membaca konteks form (nama, tahun lahir, ayah/ibu/pasangan yang sudah diisi)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    fam, a = family(), request.args
    role = a.get("peran") if a.get("peran") in ("ayah", "ibu", "pasangan", "diri") else None
    jk = a.get("jk")
    self_id = _int(a.get("exclude"))
    exclude = set()
    if self_id in fam.people:
        exclude = {self_id} | (fam.descendants(self_id) if role in ("ayah", "ibu") else set())
    if role:
        ctx = form_ctx(a, self_id)
        ctx["jk"] = a.get("jk_diri") if a.get("jk_diri") in ("L", "P") else None
        ctx["need_jk"] = jk if jk in ("L", "P") else None
        found = matching.rank(fam, role, q, ctx, exclude=exclude, limit=10, min_score=0.62)
    else:
        found = []
        for pid, p in fam.people.items():
            if pid in exclude or (jk in ("L", "P") and p["jenis_kelamin"] not in (jk, None)):
                continue
            sc = matching.quick_match(q, p["nama"])
            if sc >= 0.5:
                found.append({"id": pid, "score": sc, "reasons": []})
        found.sort(key=lambda x: -x["score"])
        found = found[:10]
    out = []
    if is_tamu():
        found = [c for c in found if c["id"] in tamu_scope()]
    for c in found:
        p = fam.people[c["id"]]
        out.append({"id": p["id"], "nama": p["nama"], "info": matching.describe(fam, p["id"]),
                    "score": c["score"], "reasons": [r for r in c["reasons"] if not r.startswith("Nama mirip")],
                    "hue": hue(p["id"]), "inisial": initials(p["nama"]),
                    "foto": url_for("foto", name=p["foto"]) if p["foto"] else None})
    return jsonify(out)


# === ADMIN ===
@app.route("/admin")
@admin_required
def admin():
    users = get_db().execute(
        "SELECT u.*, p.nama AS person_nama FROM users u LEFT JOIN persons p ON p.id=u.person_id WHERE u.peran<>'tamu' "
        "ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, u.created_at DESC"
    ).fetchall()
    groups = {k: [u for u in users if u["status"] == k]
              for k in ("pending", "approved", "active", "rejected", "disabled")}
    fam = family()
    dupes = matching.find_duplicates(fam, claimed=claimed_ids())
    minta_tamu = get_db().execute("SELECT COUNT(*) FROM permintaan_tamu WHERE status='menunggu'").fetchone()[0]
    return render_template("admin/admin.html", groups=groups, last_link=session.pop("last_link", None), minta_tamu=minta_tamu,
                           akses_baru=session.pop("akses_baru", None),
                           dupes=dupes, fam=fam)


@app.template_global()
def can_merge(a, b):
    """Admin, atau keluarga dekat yang berhak mengubah kedua data."""
    user = current_user()
    if not user:
        return False
    if user["is_admin"]:
        return True
    pa, pb = family().get(a), family().get(b)
    if not (pa and pb):
        return False
    def boleh(keep, drop):
        # data kembar dilebur ke orang yang berhak saya kelola; kembarannya belum punya akun dan
        # tidak sedang dikelola kerabat yang lebih dekat daripada saya terhadap orang tersebut
        return can_edit(keep) and my_tier(keep) <= 2 and drop["id"] not in claimed_ids() and my_tier(keep) <= _lock(drop)
    return boleh(pa, pb) or boleh(pb, pa)


@app.route("/gabung", methods=["POST"])
@login_required
def gabung():
    keep, drop = _int(request.form.get("simpan")), _int(request.form.get("hapus"))
    fam, user = family(), current_user()
    pk, pd = fam.get(keep), fam.get(drop)
    if not (pk and pd and (user["is_admin"] or (can_edit(pk) and my_tier(pk) <= 2 and drop not in claimed_ids()
                                                and my_tier(pk) <= _lock(pd)))):
        abort(403)
    return _gabung(keep, drop, back=url_for("anggota_detail", pid=keep))


@app.route("/admin/gabung", methods=["POST"])
@admin_required
def admin_gabung():
    return _gabung(_int(request.form.get("simpan")), _int(request.form.get("hapus")), back=url_for("admin"))


def _gabung(keep, drop, back):
    """Gabungkan dua data orang yang sama: `drop` dilebur ke `keep`."""
    db, fam = get_db(), family()
    if not (keep in fam.people and drop in fam.people) or keep == drop:
        abort(400)
    if keep in claimed_ids() and drop in claimed_ids():
        flash("Keduanya sudah punya akun masing-masing, tidak bisa digabung.", "warning")
        return redirect(back)
    if drop in fam.descendants(keep) or keep in fam.descendants(drop):
        flash("Salah satunya tercatat sebagai keturunan yang lain — tidak bisa digabung.", "warning")
        return redirect(back)
    k, d = fam.people[keep], fam.people[drop]
    # kolom kosong di data yang disimpan diisi dari data yang dilebur
    fill = {col: d[col] for col in ("jenis_kelamin", "tahun_lahir", "tanggal_lahir", "urutan_lahir", "alamat", "wa", "pekerjaan", "bio",
                                     "ayah_id", "ibu_id", "foto")
            if not k[col] and d[col] and d[col] != keep}
    if fill:
        db.execute(f"UPDATE persons SET {', '.join(f'{c}=?' for c in fill)}, updated_at=? WHERE id=?",
                   (*fill.values(), now(), keep))
    for col in ("ayah_id", "ibu_id"):
        db.execute(f"UPDATE persons SET {col}=? WHERE {col}=? AND id<>?", (keep, drop, keep))
    for m in db.execute("SELECT * FROM pernikahan WHERE ? IN (orang1_id, orang2_id)", (drop,)).fetchall():
        other = m["orang2_id"] if m["orang1_id"] == drop else m["orang1_id"]
        ensure_marriage(db, keep, other, m["status"], m["tanggal_nikah"], m["tanggal_selesai"])
    db.execute("DELETE FROM pernikahan WHERE ? IN (orang1_id, orang2_id)", (drop,))
    db.execute("UPDATE users SET person_id=? WHERE person_id=?", (keep, drop))
    db.execute("UPDATE riwayat SET person_id=? WHERE person_id=?", (keep, drop))
    db.execute("UPDATE usulan SET person_id=? WHERE person_id=?", (keep, drop))
    log_riwayat(db, keep, [f"Digabung dengan data kembar “{d['nama']}”"])
    db.execute("DELETE FROM persons WHERE id=?", (drop,))
    db.commit()
    if d["foto"] and d["foto"] != fill.get("foto"):
        remove_photo(d["foto"])
    flash(f"Data “{d['nama']}” sudah digabung ke “{k['nama']}”. Cabang keluarganya kini tersambung 🌳", "success")
    return redirect(url_for("anggota_detail", pid=keep))


@app.route("/admin/user/<int:uid>/<aksi>", methods=["POST"])
@admin_required
def admin_aksi(uid, aksi):
    db, me = get_db(), current_user()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        abort(404)
    if user["id"] == me["id"] and aksi in ("nonaktifkan", "cabut-admin", "hapus", "tolak"):
        flash("Anda tidak bisa melakukan itu pada akun sendiri.", "warning")
        return redirect(url_for("admin"))

    if aksi in ("setujui", "kirim-ulang"):
        db.execute("UPDATE users SET status='approved', approved_at=? WHERE id=?", (now(), uid))
        db.commit()
        link = activation_link(uid)
        sent = send_mail(user["email"], "Pendaftaran disetujui — aktifkan akun Anda",
                         mail_body(f"Kabar baik, {user['nama']}! 🎉",
                                   "Pendaftaran Anda telah disetujui admin keluarga. Klik tombol di bawah untuk "
                                   "mengaktifkan akun. Tautan berlaku 14 hari.", "Aktifkan akun saya", link))
        if sent:
            flash(f"{user['nama']} disetujui. Tautan aktivasi sudah dikirim ke {user['email']}.", "success")
        else:
            session["last_link"] = {"nama": user["nama"], "link": link}
            flash(f"{user['nama']} disetujui. Email belum terkirim — salin tautan aktivasi "
                  "di halaman admin dan kirimkan secara pribadi.", "info")
    elif aksi == "tolak":
        db.execute("UPDATE users SET status='rejected' WHERE id=?", (uid,))
        db.commit()
        send_mail(user["email"], "Tentang pendaftaran Anda",
                  mail_body(f"Halo {user['nama']},",
                            "Mohon maaf, admin belum dapat memverifikasi pendaftaran Anda saat ini. "
                            "Bila Anda merasa bagian dari keluarga ini, silakan hubungi kerabat yang "
                            "mengelola silsilah untuk konfirmasi. Terima kasih atas pengertiannya 🙏"))
        flash(f"Pendaftaran {user['nama']} ditolak.", "info")
    elif aksi == "nonaktifkan":
        db.execute("UPDATE users SET status='disabled' WHERE id=?", (uid,))
        db.commit()
        flash(f"Akun {user['nama']} dinonaktifkan.", "info")
    elif aksi == "aktifkan":
        db.execute("UPDATE users SET status='active' WHERE id=?", (uid,))
        db.commit()
        flash(f"Akun {user['nama']} aktif kembali.", "success")
    elif aksi == "jadikan-admin":
        db.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
        db.commit()
        flash(f"{user['nama']} sekarang admin.", "success")
    elif aksi == "cabut-admin":
        db.execute("UPDATE users SET is_admin=0 WHERE id=?", (uid,))
        db.commit()
        flash(f"Hak admin {user['nama']} dicabut.", "info")
    elif aksi == "peran":
        peran = request.form.get("peran")
        if peran not in PERAN:
            abort(400)
        db.execute("UPDATE users SET peran=? WHERE id=?", (peran, uid))
        db.commit()
        flash(f"Peran {user['nama']} sekarang {PERAN[peran]}.", "success")
    elif aksi == "hapus":
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        db.commit()
        flash(f"Akun {user['nama']} dihapus (data silsilahnya tetap tersimpan).", "info")
    else:
        abort(404)
    return redirect(url_for("admin"))


@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(400)
@app.errorhandler(413)
def error_page(e):
    pesan = {403: "Halaman ini khusus untuk pihak tertentu.",
             404: "Halaman yang Anda cari tidak ditemukan.",
             413: "Ukuran file terlalu besar (maksimal 8 MB)."}.get(e.code, getattr(e, "description", ""))
    return render_template("error.html", code=e.code, pesan=pesan), e.code


def backfill_kelola():
    """Data lama tanpa pengelola: berikan kepada pencatatnya sesuai kedekatan."""
    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT p.id, u.id AS uid, u.person_id FROM persons p JOIN users u ON u.id=p.created_by "
                          "WHERE p.kelola_tier IS NULL").fetchall()
        if rows:
            fam = family()
            for r in rows:
                db.execute("UPDATE persons SET kelola_tier=?, kelola_user=? WHERE id=?",
                           (fam.kin_tier(r["person_id"], r["id"]), r["uid"], r["id"]))
            db.commit()


init_db()
backfill_kelola()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
