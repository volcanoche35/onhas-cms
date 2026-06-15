#!/usr/bin/env python3
"""
Onhas Yapı CMS — Flask + SQLite + Admin Panel
"""
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse, urljoin

import bcrypt
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, g, send_from_directory, jsonify, abort
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# App & Config
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Güvenli secret key — production'da SECRET_KEY env zorunlu
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    _secret = secrets.token_hex(64)
    print("WARNING: SECRET_KEY not set, using random key (sessions reset on restart)")
app.secret_key = _secret

# Session güvenlik ayarları
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=3600,
    REMEMBER_COOKIE_SECURE=True,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_DURATION=timedelta(days=7),
)

# Brute-force tracking
login_attempts = defaultdict(list)
BRUTE_FORCE_MAX = 5       # 5 başarısız deneme
BRUTE_FORCE_WINDOW = 60   # 60 saniye içinde

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "onhas.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "pdf"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    """Create tables and seed default data."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    cur = db.cursor()

    # --- users table ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # --- contents table ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT UNIQUE NOT NULL,
            value       TEXT,
            type        TEXT DEFAULT 'text',
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # --- settings table ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT UNIQUE NOT NULL,
            value       TEXT
        )
    """)

    # --- projects table ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT,
            image       TEXT,
            status      TEXT DEFAULT 'devam',
            order_num   INTEGER DEFAULT 0
        )
    """)

    # --- seed admin user ---
    cur.execute("SELECT id FROM users WHERE username = ?", ("admin",))
    if not cur.fetchone():
        hashed = bcrypt.hashpw("Onhas2024!".encode("utf-8"), bcrypt.gensalt())
        cur.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            ("admin", hashed.decode("utf-8")),
        )

    # --- seed contents ---
    default_contents = {
        "hero_title":       "Onhas Yapı",
        "hero_subtitle":    "Geleceği İnşa Ediyoruz",
        "hero_description": "Güzelbahçe ve İzmir genelinde modern, güvenilir ve kaliteli inşaat çözümleri sunuyoruz.",
        "about_title":      "Hakkımızda",
        "about_text":       "Onhas Yapı olarak yılların verdiği tecrübeyle, her projede mükemmelliği hedefliyoruz.",
        "contact_phone":    "+90 533 640 09 37",
        "contact_address":  "Güzelbahçe / İzmir",
        "whatsapp_number":  "905336400937",
        "instagram_url":    "https://instagram.com/onhasyapi",
        "footer_text":      "© 2024 Onhas Yapı. Tüm hakları saklıdır.",
    }
    for key, value in default_contents.items():
        cur.execute(
            "INSERT OR IGNORE INTO contents (key, value) VALUES (?, ?)",
            (key, value),
        )

    # --- seed settings ---
    default_settings = {
        "site_title":       "Onhas Yapı",
        "site_description": "Onhas Yapı — Güzelbahçe ve İzmir'de inşaat",
        "email":            "info@onhasyapi.com",
    }
    for key, value in default_settings.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # --- seed sample projects ---
    cur.execute("SELECT COUNT(*) as cnt FROM projects")
    if cur.fetchone()["cnt"] == 0:
        sample_projects = [
            ("Villa Projesi", "Güzelbahçe'de modern villa inşaatı", "", "devam", 1),
            ("Toplu Konut", "İzmir'de 50 daireli konut projesi", "", "gelecek", 2),
            ("Ofis Binası", "Güzelbahçe merkezde A sınıfı ofis", "", "bitti", 3),
        ]
        cur.executemany(
            "INSERT INTO projects (title, description, image, status, order_num) VALUES (?,?,?,?,?)",
            sample_projects,
        )

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Flask-Login setup
# ---------------------------------------------------------------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "admin_login"
login_manager.login_message = "Bu sayfaya erişmek için giriş yapmalısınız."
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row:
        return User(row["id"], row["username"])
    return None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_contents_dict():
    """Return all contents as {key: (value, type)} dict."""
    db = get_db()
    rows = db.execute("SELECT key, value, type FROM contents").fetchall()
    return {r["key"]: (r["value"], r["type"]) for r in rows}


def get_setting(key, default=""):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_content(key, default=""):
    db = get_db()
    row = db.execute("SELECT value FROM contents WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


class SiteNamespace:
    """Template'lerde {{ site.xxx }} diye erişilebilen namespace."""
    def __getattr__(self, name):
        return None  # attribute yoksa None dönsün, UndefinedError vermesin


@app.context_processor
def inject_site():
    """Tüm template'lere 'site' değişkenini enjekte et."""
    try:
        db = get_db()
    except Exception:
        return {"site": SiteNamespace()}
    
    # Settings ve contents'i birleştir
    attrs = {}
    for row in db.execute("SELECT key, value FROM settings").fetchall():
        attrs[row["key"]] = row["value"]
    for row in db.execute("SELECT key, value FROM contents").fetchall():
        attrs[row["key"]] = row["value"]
    
    class DynamicSite:
        def __getattr__(self, name):
            return attrs.get(name)
    
    return {"site": DynamicSite()}


# ---------------------------------------------------------------------------
# FRONTEND ROUTES (Ziyaretçi)
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    contents = get_contents_dict()
    db = get_db()
    projects = db.execute(
        "SELECT * FROM projects ORDER BY order_num ASC"
    ).fetchall()
    return render_template(
        "index.html",
        contents=contents,
        projects=projects,
        get_content=get_content,
        get_setting=get_setting,
        istatistikler={
            "yil": get_content("istatistik_yil", "20+"),
            "proje": get_content("istatistik_proje", "150+"),
            "musteri": get_content("istatistik_musteri", "200+"),
            "ekip": get_content("istatistik_ekip", "50+"),
        },
        one_cikan_projeler=projects,
        hizmetler=[
            {"icon": "building", "title": "Konut İnşaatı", "description": "Modern yaşam alanları, villa ve apartman projeleri."},
            {"icon": "geo-alt", "title": "Zemin Etüdü", "description": "Profesyonel zemin etüdü ve danışmanlık hizmetleri."},
            {"icon": "hammer", "title": "Tadilat & Yenileme", "description": "Eski yapıların modernizasyonu ve güçlendirme çalışmaları."},
            {"icon": "clipboard-check", "title": "Proje Yönetimi", "description": "Baştan sona profesyonel proje planlama ve yürütme."},
            {"icon": "house-door", "title": "Anahtar Teslim", "description": "Hayalinizdeki eve anahtar teslim çözümlerle kavuşun."},
            {"icon": "rulers", "title": "Mimari Tasarım", "description": "Estetik ve fonksiyonelliği birleştiren özgün tasarımlar."},
        ],
    )


@app.route("/hakkimizda")
def hakkimizda():
    contents = get_contents_dict()
    return render_template(
        "hakkimizda.html",
        contents=contents,
        get_content=get_content,
        get_setting=get_setting,
    )


@app.route("/projeler")
def projeler():
    db = get_db()
    projects = db.execute(
        "SELECT * FROM projects ORDER BY order_num ASC"
    ).fetchall()
    contents = get_contents_dict()
    return render_template(
        "projeler.html",
        projects=projects,
        contents=contents,
        get_content=get_content,
        get_setting=get_setting,
    )


@app.route("/neler-yaptik")
def neler_yaptik():
    db = get_db()
    # Show completed projects
    projects = db.execute(
        "SELECT * FROM projects WHERE status = 'bitti' ORDER BY order_num ASC"
    ).fetchall()
    contents = get_contents_dict()
    return render_template(
        "neler-yaptik.html",
        projects=projects,
        contents=contents,
        get_content=get_content,
        get_setting=get_setting,
    )


@app.route("/iletisim")
def iletisim():
    contents = get_contents_dict()
    return render_template(
        "iletisim.html",
        contents=contents,
        get_content=get_content,
        get_setting=get_setting,
    )


# ---------------------------------------------------------------------------
# ADMIN ROUTES (/yonetim)
# ---------------------------------------------------------------------------

@app.route("/yonetim", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for("admin_panel"))

    if request.method == "POST":
        ip = request.remote_addr
        
        # Brute-force kontrolü
        now = time.time()
        attempts = [t for t in login_attempts.get(ip, []) if now - t < BRUTE_FORCE_WINDOW]
        login_attempts[ip] = attempts
        if len(attempts) >= BRUTE_FORCE_MAX:
            flash("Çok fazla başarısız deneme. Lütfen 1 dakika sonra tekrar deneyin.", "danger")
            return render_template("admin/login.html", get_setting=get_setting)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        db = get_db()
        row = db.execute(
            "SELECT id, username, password FROM users WHERE username = ?", (username,)
        ).fetchone()

        if row and bcrypt.checkpw(
            password.encode("utf-8"), row["password"].encode("utf-8")
        ):
            user = User(row["id"], row["username"])
            login_user(user, remember=request.form.get("remember"))
            flash("Başarıyla giriş yaptınız.", "success")
            
            # Open redirect önlemi
            next_page = request.args.get("next")
            if next_page:
                ref_url = urlparse(request.host_url)
                test_url = urlparse(urljoin(request.host_url, next_page))
                if test_url.netloc != ref_url.netloc:
                    next_page = None  # harici siteye redirect'e izin verme
            
            return redirect(next_page or url_for("admin_panel"))
        else:
            login_attempts[ip].append(time.time())
            flash("Kullanıcı adı veya şifre hatalı.", "danger")

    return render_template(
        "admin/login.html",
        get_setting=get_setting,
    )


@app.route("/yonetim/cikis")
@login_required
def admin_logout():
    logout_user()
    flash("Çıkış yaptınız.", "info")
    return redirect(url_for("admin_login"))


@app.route("/yonetim/panel")
@login_required
def admin_panel():
    db = get_db()
    content_count = db.execute("SELECT COUNT(*) as cnt FROM contents").fetchone()["cnt"]
    project_count = db.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()["cnt"]
    setting_count = db.execute("SELECT COUNT(*) as cnt FROM settings").fetchone()["cnt"]

    return render_template(
        "admin/dashboard.html",
        content_count=content_count,
        project_count=project_count,
        setting_count=setting_count,
        get_setting=get_setting,
        istatistikler={
            "yil": get_content("istatistik_yil", "20+"),
            "proje": get_content("istatistik_proje", "150+"),
            "musteri": get_content("istatistik_musteri", "200+"),
            "ekip": get_content("istatistik_ekip", "50+"),
        },
    )


@app.route("/yonetim/panel/icerik", methods=["GET", "POST"])
@login_required
def admin_icerik():
    db = get_db()

    if request.method == "POST":
        for key in request.form:
            if key.startswith("content_"):
                content_key = key.replace("content_", "")
                value = request.form.get(key, "")
                content_type = request.form.get(f"type_{content_key}", "text")

                db.execute(
                    """UPDATE contents SET value = ?, type = ?, updated_at = datetime('now','localtime')
                       WHERE key = ?""",
                    (value, content_type, content_key),
                )
        db.commit()
        flash("İçerikler başarıyla güncellendi.", "success")
        return redirect(url_for("admin_icerik"))

    contents = db.execute(
        "SELECT * FROM contents ORDER BY key ASC"
    ).fetchall()

    return render_template(
        "admin/icerik.html",
        contents=contents,
        get_setting=get_setting,
    )


@app.route("/yonetim/panel/projeler", methods=["GET", "POST"])
@login_required
def admin_projeler():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "add":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            image = request.form.get("image", "").strip()
            status = request.form.get("status", "devam")
            order_num = request.form.get("order_num", "0")

            if title:
                db.execute(
                    """INSERT INTO projects (title, description, image, status, order_num)
                       VALUES (?, ?, ?, ?, ?)""",
                    (title, description, image, status, int(order_num)),
                )
                db.commit()
                flash("Proje eklendi.", "success")

        elif action == "edit":
            project_id = request.form.get("id", "")
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            image = request.form.get("image", "").strip()
            status = request.form.get("status", "devam")
            order_num = request.form.get("order_num", "0")

            if title and project_id:
                db.execute(
                    """UPDATE projects
                       SET title=?, description=?, image=?, status=?, order_num=?
                       WHERE id=?""",
                    (title, description, image, status, int(order_num), int(project_id)),
                )
                db.commit()
                flash("Proje güncellendi.", "success")

        elif action == "delete":
            project_id = request.form.get("id", "")
            if project_id:
                db.execute("DELETE FROM projects WHERE id = ?", (int(project_id),))
                db.commit()
                flash("Proje silindi.", "info")

        return redirect(url_for("admin_projeler"))

    projects = db.execute(
        "SELECT * FROM projects ORDER BY order_num ASC"
    ).fetchall()

    return render_template(
        "admin/projeler.html",
        projects=projects,
        get_setting=get_setting,
    )


@app.route("/yonetim/panel/ayarlar", methods=["GET", "POST"])
@login_required
def admin_ayarlar():
    db = get_db()

    if request.method == "POST":
        # Update site settings
        for key in request.form:
            if key.startswith("setting_"):
                setting_key = key.replace("setting_", "")
                value = request.form.get(key, "")
                db.execute(
                    """INSERT INTO settings (key, value) VALUES (?, ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (setting_key, value),
                )

        # Change password
        new_password = request.form.get("new_password", "").strip()
        new_password_confirm = request.form.get("new_password_confirm", "").strip()

        if new_password:
            if new_password != new_password_confirm:
                flash("Şifreler eşleşmiyor.", "danger")
            elif len(new_password) < 6:
                flash("Şifre en az 6 karakter olmalıdır.", "danger")
            else:
                hashed = bcrypt.hashpw(
                    new_password.encode("utf-8"), bcrypt.gensalt()
                )
                db.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (hashed.decode("utf-8"), current_user.id),
                )
                db.commit()
                flash("Şifre başarıyla güncellendi.", "success")

        db.commit()
        flash("Ayarlar güncellendi.", "success")
        return redirect(url_for("admin_ayarlar"))

    settings = db.execute("SELECT * FROM settings ORDER BY key ASC").fetchall()

    return render_template(
        "admin/ayarlar.html",
        settings=settings,
        get_setting=get_setting,
    )


@app.route("/yonetim/panel/upload", methods=["POST"])
@login_required
def admin_upload():
    if "file" not in request.files:
        return jsonify({"success": False, "message": "Dosya seçilmedi."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "message": "Dosya seçilmedi."}), 400

    if file and allowed_file(file.filename):
        ext = file.filename.rsplit(".", 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        file.save(filepath)
        file_url = url_for("static", filename=f"uploads/{unique_name}")
        return jsonify({"success": True, "url": file_url, "filename": unique_name})

    return jsonify({"success": False, "message": "Geçersiz dosya türü."}), 400


# Static files helper
@app.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ---------------------------------------------------------------------------
# Security Headers
# ---------------------------------------------------------------------------
@app.after_request
def add_security_headers(response):
    """Her response'a güvenlik başlıkları ekle."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "img-src 'self' data: https:; "
        "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "frame-src 'self' https://www.google.com; "
        "connect-src 'self'"
    )
    if request.path.startswith("/yonetim"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)
else:
    # Initialize DB on import (for gunicorn/wsgi)
    with app.app_context():
        init_db()
