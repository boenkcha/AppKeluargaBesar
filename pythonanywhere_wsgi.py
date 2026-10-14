"""Contoh isi file WSGI untuk PythonAnywhere.

Salin isi file ini ke file WSGI web app Anda
(tab Web → bagian "Code" → klik tautan "WSGI configuration file"),
ganti seluruh isinya, simpan, lalu klik "Reload".
"""
import os
import sys

USERNAME = "amatkasbifam"
PROJECT = f"/home/{USERNAME}/AppKeluargaBesar"

if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

# --- Opsional: email aktivasi via Gmail (akun gratis PythonAnywhere hanya boleh ke Gmail) ---
# os.environ["SMTP_HOST"] = "smtp.gmail.com"
# os.environ["SMTP_PORT"] = "587"
# os.environ["SMTP_USER"] = "alamat@gmail.com"
# os.environ["SMTP_PASS"] = "app-password-16-digit"
# os.environ["MAIL_FROM"] = "Silsilah Keluarga Besar <alamat@gmail.com>"

from app import app as application  # noqa: E402
