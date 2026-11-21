import os
import time
import smtplib
import secrets
import sqlite3
import requests
from email.mime.text import MIMEText
from functools import wraps
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.db"))

# ---------------- PRIVATE API TOKEN & UPSTREAM ENDPOINTS ----------------
PRIVATE_API_TOKEN = os.environ.get("UPSTREAM_API_KEY", "YOUR_PRIVATE_API_TOKEN_HERE").strip()

UPSTREAM_BASE_URL = os.environ.get(
    "UPSTREAM_BASE_URL",
    "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
).rstrip("/")

GETNUM_API_URL = f"{UPSTREAM_BASE_URL}/getnum"
LIVEACCESS_API_URL = f"{UPSTREAM_BASE_URL}/liveaccess"
SUCCESS_OTP_API_URL = f"{UPSTREAM_BASE_URL}/success-otp"
CONSOLE_API_URL = f"{UPSTREAM_BASE_URL}/console"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "mdrifatahmmed809@gmail.com").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "54wx52@@")
DEFAULT_AGENT_PASS = os.environ.get("AGENT_PASSWORD", "54wx52@@")

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip()

DEFAULT_COUNTRY_RATES = {
    "United Kingdom": 0.05,
    "Guinea": 0.10,
    "Ivory Coast": 0.05,
    "Bangladesh": 0.08,
    "Default": 0.05,
}

DEFAULT_SERVICE_RATES = {
    "WhatsApp": 0.05,
    "Facebook": 0.02,
    "TikTok": 0.12,
    "Telegram": 0.15,
}

NUMBER_TTL_SECONDS = 15 * 60
MAX_ACCOUNTS_PER_AGENT = 200

SIDEBAR_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "group": None},
    {"key": "get-number", "label": "Get Number", "group": "Dialer Panel"},
    {"key": "console", "label": "Console", "group": "Dialer Panel"},
    {"key": "access-list", "label": "Access List", "group": "Dialer Panel"},
    {"key": "profile", "label": "Profile", "group": "Account"},
    {"key": "payment", "label": "Payment", "group": "Account"},
    {"key": "api-docs", "label": "API Docs", "group": "Extra"},
]


# ---------------- DATABASE HELPERS ----------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            email TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            agent_email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            uid TEXT NOT NULL,
            api_key TEXT,
            api_key_created TEXT,
            member_since TEXT,
            last_login TEXT,
            balance REAL NOT NULL DEFAULT 0.0,
            lifetime_earning REAL NOT NULL DEFAULT 0.0,
            otp_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_numbers (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            range_val TEXT,
            full_number TEXT,
            national_number TEXT,
            no_plus_number TEXT,
            country TEXT,
            operator TEXT,
            country_rate REAL DEFAULT 0.0,
            created_ts REAL NOT NULL,
            expires_ts REAL NOT NULL,
            otp_message TEXT,
            earned_amount REAL DEFAULT 0.0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS otp_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            created_ts REAL NOT NULL,
            user_email TEXT NOT NULL,
            agent_email TEXT NOT NULL,
            number_val TEXT,
            country TEXT,
            service TEXT,
            rate REAL DEFAULT 0.0,
            message TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            user_name TEXT NOT NULL,
            method TEXT NOT NULL,
            wallet_number TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            decided_at TEXT DEFAULT ''
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_addresses (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            gateway TEXT NOT NULL,
            label TEXT NOT NULL,
            address TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS country_rates (
            country TEXT PRIMARY KEY,
            rate REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS service_rates (
            service TEXT PRIMARY KEY,
            rate REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS otp_rate_limits (
            service TEXT PRIMARY KEY,
            max_rate REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_service_rates (
            agent_email TEXT NOT NULL,
            service TEXT NOT NULL,
            rate REAL NOT NULL,
            PRIMARY KEY (agent_email, service)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            email TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            expires_ts REAL NOT NULL
        )
    """)

    cur.execute("INSERT OR IGNORE INTO settings (k, v) VALUES ('agent_mail_auto_account', '1')")

    for c, r in DEFAULT_COUNTRY_RATES.items():
        cur.execute("INSERT OR IGNORE INTO country_rates (country, rate) VALUES (?, ?)", (c, r))

    for s, r in DEFAULT_SERVICE_RATES.items():
        cur.execute("INSERT OR IGNORE INTO service_rates (service, rate) VALUES (?, ?)", (s, r))

    conn.commit()
    conn.close()


def get_auto_account_flag() -> bool:
    conn = get_db()
    row = conn.execute("SELECT v FROM settings WHERE k = 'agent_mail_auto_account'").fetchone()
    conn.close()
    return (row["v"] == "1") if row else True


def set_auto_account_flag(enabled: bool):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (k, v) VALUES ('agent_mail_auto_account', ?)",
        ("1" if enabled else "0",)
    )
    conn.commit()
    conn.close()


def get_country_rates() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT country, rate FROM country_rates").fetchall()
    conn.close()
    return {r["country"]: float(r["rate"]) for r in rows}


def get_service_rates() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT service, rate FROM service_rates").fetchall()
    conn.close()
    return {r["service"]: float(r["rate"]) for r in rows}


def get_otp_rate_limits() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT service, max_rate FROM otp_rate_limits").fetchall()
    conn.close()
    return {r["service"]: float(r["max_rate"]) for r in rows}


def get_agent_service_rates(agent_email: str) -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT service, rate FROM agent_service_rates WHERE agent_email = ?",
        ((agent_email or "").strip().lower(),)
    ).fetchall()
    conn.close()
    return {r["service"]: float(r["rate"]) for r in rows}


def get_user_dict(email: str):
    if not email:
        return None
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_users_dict() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY rowid DESC").fetchall()
    conn.close()
    return {r["email"]: dict(r) for r in rows}


def get_agent_row(email: str):
    if not email:
        return None
    conn = get_db()
    row = conn.execute("SELECT * FROM agents WHERE email = ?", (email.lower(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def ensure_agent_exists(agent_email: str, password_plain: str = None):
    agent_email = (agent_email or "").strip().lower()
    if not agent_email:
        return
    if not get_agent_row(agent_email):
        pw = password_plain or DEFAULT_AGENT_PASS
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO agents (email, password_hash, created_at) VALUES (?, ?, ?)",
            (agent_email, generate_password_hash(pw), datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        )
        conn.commit()
        conn.close()


# ---------------- COMMON HELPERS ----------------

def get_base_url():
    if BASE_URL:
        return BASE_URL.rstrip("/")
    try:
        return request.host_url.rstrip("/")
    except RuntimeError:
        return ""


def get_api_token() -> str:
    return (PRIVATE_API_TOKEN or os.environ.get("UPSTREAM_API_KEY", "")).strip()


def upstream_headers(token: str = None) -> dict:
    api_key = (token if token is not None else get_api_token()).strip()
    return {
        "mauthapi": api_key,
        "Content-Type": "application/json",
    }


def calculate_rate(country: str, service_sid: str = "", agent_email: str = "") -> float:
    service_rates = get_service_rates()
    country_rates = get_country_rates()
    limits = get_otp_rate_limits()

    if service_sid:
        svc_key = None
        for k in service_rates.keys():
            if k.lower() == service_sid.strip().lower():
                svc_key = k
                break

        if agent_email:
            ag_rates = get_agent_service_rates(agent_email)
            for ak, ar in ag_rates.items():
                if ak.lower() == service_sid.strip().lower():
                    rate_val = float(ar)
                    for lk, lv in limits.items():
                        if lk.lower() == service_sid.strip().lower():
                            rate_val = min(rate_val, float(lv))
                    return rate_val

        if svc_key:
            rate_val = float(service_rates[svc_key])
            for lk, lv in limits.items():
                if lk.lower() == svc_key.lower():
                    rate_val = min(rate_val, float(lv))
            return rate_val

    if country:
        for k, v in country_rates.items():
            if k.lower() == country.strip().lower():
                return float(v)
    return float(country_rates.get("Default", 0.05))


def detect_service_from_message(msg_text: str) -> str:
    low = (msg_text or "").lower()
    for s in get_service_rates().keys():
        if s.lower() in low:
            return s
    return "OTP"


def send_email_smtp(to_email: str, subject: str, body: str) -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        print("SMTP Warning: SMTP_HOST, SMTP_USER, or SMTP_PASS is not configured.")
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=5) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, [to_email], msg.as_string())
        return True
    except Exception as e:
        print("Email send failed, continuing approval:", repr(e))
        return False


def send_agent_mail(user: dict):
    agent_email = user["agent_email"]
    login_url = f"{get_base_url()}/login"
    subject = f"Auto Account Created: {user['name']} ({user['email']})"
    body = (
        f"A new user account has been created automatically.\n\n"
        f"User Name: {user['name']}\n"
        f"User Email: {user['email']}\n"
        f"Status: {user['status']}\n"
        f"Created At: {user['created_at']}\n\n"
        f"Login URL: {login_url}\n"
    )
    return send_email_smtp(agent_email, subject, body)


# ---------------- LOGIN GUARDS ----------------

def user_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        user = get_user_dict(email) if email else None
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "message": "Login required."}), 401
            flash("Please login to your account first.", "error")
            return redirect(url_for("login"))
        if user["status"] != "ACTIVE":
            session.pop("user_email", None)
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "message": f"Account is {user['status']}."}), 403
            flash(f"Your account is {user['status']}. Contact Agent/Admin.", "error")
            return redirect(url_for("login"))
        return f(user, *args, **kwargs)
    return decorated


def agent_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        agent_email = session.get("agent_email")
        if not agent_email:
            flash("Agent login required.", "error")
            return redirect(url_for("login"))
        return f(agent_email, *args, **kwargs)
    return decorated


def admin_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin login required.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------- AUTH & FORGOT PASSWORD ----------------

@app.route("/")
def index():
    if session.get("is_admin"):
        return redirect(url_for("admin_panel"))
    if session.get("agent_email"):
        return redirect(url_for("agent_panel"))
    if session.get("user_email") and get_user_dict(session.get("user_email")):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    auto_flag = get_auto_account_flag()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))

        agent_row = get_agent_row(email)
        if agent_row and check_password_hash(agent_row["password_hash"], password):
            session.clear()
            session["agent_email"] = email
            return redirect(url_for("agent_panel"))

        user = get_user_dict(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        if user["status"] == "DEACTIVATED":
            flash("Your account has been DEACTIVATED by your Agent/Admin.", "error")
            return redirect(url_for("login"))
        if user["status"] == "PENDING":
            flash("Your account is PENDING Admin approval.", "error")
            return redirect(url_for("login"))
        if user["status"] == "REJECTED":
            flash("Your account was REJECTED by Admin.", "error")
            return redirect(url_for("login"))

        now_dt = datetime.now()
        last_login_str = now_dt.strftime("%d/%m/%Y, %H:%M:%S")
        member_since_str = user["member_since"] or now_dt.strftime("%b %Y")
        api_created_str = user["api_key_created"] or last_login_str

        conn = get_db()
        conn.execute(
            "UPDATE users SET last_login = ?, member_since = ?, api_key_created = ? WHERE email = ?",
            (last_login_str, member_since_str, api_created_str, email)
        )
        conn.commit()
        conn.close()

        session.clear()
        session["user_email"] = email
        return redirect(url_for("dashboard"))

    return render_template("login.html", agent_mail_auto_account=auto_flag)


@app.route("/register", methods=["GET", "POST"])
def register():
    auto_flag = get_auto_account_flag()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        agent_email = request.form.get("agent_email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not agent_email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for("register"))

        if not get_agent_row(agent_email):
            flash("Agent wrong", "error")
            return redirect(url_for("register"))

        if get_user_dict(email):
            flash("An account with this email already exists.", "error")
            return redirect(url_for("register"))

        conn = get_db()
        agent_acc_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE agent_email = ?",
            (agent_email,)
        ).fetchone()["cnt"]
        conn.close()

        if agent_acc_count >= MAX_ACCOUNTS_PER_AGENT:
            flash(f"Agent '{agent_email}' has reached the maximum limit of {MAX_ACCOUNTS_PER_AGENT} accounts.", "error")
            return redirect(url_for("register"))

        status = "ACTIVE" if auto_flag else "PENDING"
        now_dt = datetime.now()
        now_str = now_dt.strftime("%d/%m/%Y, %H:%M:%S")
        created_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        user_data = {
            "email": email,
            "name": name,
            "agent_email": agent_email,
            "password_hash": generate_password_hash(password),
            "status": status,
            "uid": secrets.token_hex(5).upper(),
            "api_key": f"M{secrets.token_hex(6).upper()}",
            "api_key_created": now_str,
            "member_since": now_dt.strftime("%b %Y"),
            "last_login": now_str,
            "balance": 0.0,
            "lifetime_earning": 0.0,
            "otp_count": 0,
            "created_at": created_utc,
        }

        conn = get_db()
        conn.execute("""
            INSERT INTO users (
                email, name, agent_email, password_hash, status, uid, api_key,
                api_key_created, member_since, last_login, balance, lifetime_earning,
                otp_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_data["email"], user_data["name"], user_data["agent_email"],
            user_data["password_hash"], user_data["status"], user_data["uid"],
            user_data["api_key"], user_data["api_key_created"], user_data["member_since"],
            user_data["last_login"], user_data["balance"], user_data["lifetime_earning"],
            user_data["otp_count"], user_data["created_at"]
        ))
        conn.commit()
        conn.close()

        if auto_flag:
            send_agent_mail(user_data)
            session.clear()
            session["user_email"] = email
            flash("Account created automatically! Welcome to Dashboard.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Account submitted! Waiting for Admin approval.", "success")
            return redirect(url_for("login"))

    return render_template("register.html", agent_mail_auto_account=auto_flag)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    step = "request_code"
    target_email = request.args.get("email", "").strip().lower()

    if request.method == "POST":
        action = request.form.get("action", "send_code")
        email = request.form.get("email", "").strip().lower()
        target_email = email

        if action == "send_code":
            if not email:
                flash("Please enter your email address.", "error")
                return redirect(url_for("forgot_password"))

            user = get_user_dict(email)
            agent = get_agent_row(email)
            if not user and not agent:
                flash("No account found with this email.", "error")
                return redirect(url_for("forgot_password"))

            code = f"{secrets.randbelow(900000) + 100000}"
            expires_ts = time.time() + 15 * 60

            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO password_resets (email, code, expires_ts) VALUES (?, ?, ?)",
                (email, code, expires_ts)
            )
            conn.commit()
            conn.close()

            sent = send_email_smtp(
                email,
                "Password Reset Verification Code",
                f"Your password reset code is: {code}\n\nThis code will expire in 15 minutes."
            )
            if sent:
                flash("Verification code sent to your email!", "success")
            else:
                flash(f"SMTP not configured. Test Reset Code: {code}", "success")
            step = "reset_password"

        elif action == "reset_password":
            code = request.form.get("code", "").strip()
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not email or not code or not new_password:
                flash("Email, code, and new password are required.", "error")
                step = "reset_password"
            elif new_password != confirm_password:
                flash("Passwords do not match.", "error")
                step = "reset_password"
            else:
                conn = get_db()
                row = conn.execute("SELECT * FROM password_resets WHERE email = ?", (email,)).fetchone()
                if not row or row["code"] != code or time.time() > row["expires_ts"]:
                    conn.close()
                    flash("Invalid or expired reset code.", "error")
                    step = "reset_password"
                else:
                    pw_hash = generate_password_hash(new_password)
                    conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (pw_hash, email))
                    conn.execute("UPDATE agents SET password_hash = ? WHERE email = ?", (pw_hash, email))
                    conn.execute("DELETE FROM password_resets WHERE email = ?", (email,))
                    conn.commit()
                    conn.close()
                    flash("Password updated successfully! Please login.", "success")
                    return redirect(url_for("login"))

    return render_template("forgot_password.html", step=step, email=target_email)


# ---------------- AGENT PANEL ----------------

@app.route("/agent-panel")
@agent_login_required
def agent_panel(agent_email):
    conn = get_db()
    agent_users = [dict(r) for r in conn.execute(
        "SELECT * FROM users WHERE agent_email = ? ORDER BY rowid DESC", (agent_email,)
    ).fetchall()]

    now_ts = time.time()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    month_start = now_ts - (30 * 24 * 3600)

    total_otp = conn.execute(
        "SELECT COUNT(*) AS cnt FROM otp_logs WHERE agent_email = ?", (agent_email,)
    ).fetchone()["cnt"]

    today_otp = conn.execute(
        "SELECT COUNT(*) AS cnt FROM otp_logs WHERE agent_email = ? AND created_ts >= ?",
        (agent_email, today_start)
    ).fetchone()["cnt"]

    month_otp = conn.execute(
        "SELECT COUNT(*) AS cnt FROM otp_logs WHERE agent_email = ? AND created_ts >= ?",
        (agent_email, month_start)
    ).fetchone()["cnt"]

    conn.close()

    return render_template(
        "agent_panel.html",
        agent_email=agent_email,
        requests_list=agent_users,
        user_count=len(agent_users),
        max_accounts_per_agent=MAX_ACCOUNTS_PER_AGENT,
        total_otp=total_otp,
        today_otp=today_otp,
        month_otp=month_otp,
        otp_rate_limits=get_otp_rate_limits(),
        agent_service_rates=get_agent_service_rates(agent_email),
        base_service_rates=get_service_rates(),
    )


@app.route("/agent/set-service-rate", methods=["POST"])
@agent_login_required
def agent_set_service_rate(agent_email):
    service = request.form.get("service", "").strip()
    try:
        rate = float(request.form.get("rate", "0"))
    except ValueError:
        flash("Invalid rate amount.", "error")
        return redirect(url_for("agent_panel"))

    if not service or rate < 0:
        flash("Please provide a valid service name and rate.", "error")
        return redirect(url_for("agent_panel"))

    limits = get_otp_rate_limits()
    max_cap = None
    for k, v in limits.items():
        if k.lower() == service.lower():
            max_cap = float(v)
            break

    if max_cap is not None and rate > max_cap:
        flash(f"Rate (${rate:.3f}) exceeds Admin max limit (${max_cap:.3f}) for {service}.", "error")
        return redirect(url_for("agent_panel"))

    conn = get_db()
    conn.execute(
        """INSERT INTO agent_service_rates (agent_email, service, rate)
           VALUES (?, ?, ?)
           ON CONFLICT(agent_email, service) DO UPDATE SET rate = excluded.rate""",
        (agent_email.lower(), service, rate)
    )
    conn.commit()
    conn.close()

    flash(f"Service rate updated for {service} (${rate:.3f}).", "success")
    return redirect(url_for("agent_panel"))


@app.route("/agent/user/<action>", methods=["POST"])
@agent_login_required
def agent_toggle_user_status(agent_email, action):
    user_email = request.form.get("user_email", "").strip().lower()
    user = get_user_dict(user_email)

    if not user or user["agent_email"] != agent_email:
        flash("User not found or not under your agent email.", "error")
        return redirect(url_for("agent_panel"))

    if action == "deactivate":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'DEACTIVATED' WHERE email = ?", (user_email,))
        conn.commit()
        conn.close()
        flash(f"User {user_email} has been deactivated.", "success")
    elif action == "activate":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'ACTIVE' WHERE email = ?", (user_email,))
        conn.commit()
        conn.close()
        flash(f"User {user_email} has been activated.", "success")

    return redirect(url_for("agent_panel"))


# ---------------- ADMIN PANEL ----------------

@app.route("/admin")
@admin_login_required
def admin_panel():
    conn = get_db()
    users_dict = get_all_users_dict()
    agents_list = [dict(r) for r in conn.execute("SELECT * FROM agents ORDER BY rowid DESC").fetchall()]
    otp_logs = [dict(r) for r in conn.execute("SELECT * FROM otp_logs ORDER BY id DESC LIMIT 200").fetchall()]
    withdrawals = [dict(r) for r in conn.execute("SELECT * FROM withdrawals ORDER BY rowid DESC").fetchall()]

    count_rows = conn.execute(
        "SELECT agent_email, COUNT(*) AS cnt FROM users GROUP BY agent_email"
    ).fetchall()
    agent_account_counts = {r["agent_email"]: r["cnt"] for r in count_rows}
    conn.close()

    pending_by_agent = {}
    for u in users_dict.values():
        if u.get("status") == "PENDING":
            ag_mail = (u.get("agent_email") or "unknown").strip().lower()
            pending_by_agent.setdefault(ag_mail, []).append(u)

    return render_template(
        "admin_panel.html",
        agent_mail_auto_account=get_auto_account_flag(),
        country_rates=get_country_rates(),
        service_rates=get_service_rates(),
        otp_rate_limits=get_otp_rate_limits(),
        otp_logs=otp_logs,
        withdrawals=withdrawals,
        users=users_dict,
        agents=agents_list,
        pending_by_agent=pending_by_agent,
        agent_account_counts=agent_account_counts,
        max_accounts_per_agent=MAX_ACCOUNTS_PER_AGENT,
    )


@app.route("/admin/add-agent", methods=["POST"])
@admin_login_required
def admin_add_agent():
    agent_email = request.form.get("agent_email", "").strip().lower()
    agent_pass = request.form.get("agent_password", "").strip() or DEFAULT_AGENT_PASS
    if not agent_email:
        flash("Agent email is required.", "error")
        return redirect(url_for("admin_panel"))

    ensure_agent_exists(agent_email, agent_pass)
    flash(f"Agent email '{agent_email}' added/available.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/remove-agent", methods=["POST"])
@admin_login_required
def admin_remove_agent():
    agent_email = request.form.get("agent_email", "").strip().lower()
    if not agent_email:
        flash("Agent email is required.", "error")
        return redirect(url_for("admin_panel"))

    conn = get_db()
    conn.execute("DELETE FROM agents WHERE email = ?", (agent_email,))
    conn.commit()
    conn.close()

    flash(f"Agent email '{agent_email}' removed.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/user/<decision>", methods=["POST"])
@admin_login_required
def decide_user_account(decision):
    email = request.form.get("email", "").strip().lower()
    user = get_user_dict(email)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin_panel"))

    if decision == "approve":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'ACTIVE' WHERE email = ?", (email,))
        conn.commit()
        conn.close()
        user["status"] = "ACTIVE"
        send_agent_mail(user)
        flash(f"User {email} approved (ACTIVE).", "success")
    elif decision == "reject":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'REJECTED' WHERE email = ?", (email,))
        conn.commit()
        conn.close()
        flash(f"User {email} rejected.", "success")

    return redirect(url_for("admin_panel"))


@app.route("/admin/toggle-agent-mail-account", methods=["POST"])
@admin_login_required
def toggle_agent_mail_account():
    new_state = not get_auto_account_flag()
    set_auto_account_flag(new_state)
    state_text = "ON (Auto Account Creation Active)" if new_state else "OFF (Pending Approval Required)"
    flash(f"Agent Mail Auto Account is now {state_text}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/set-country-rate", methods=["POST"])
@admin_login_required
def set_country_rate():
    country = request.form.get("country", "").strip()
    try:
        rate = float(request.form.get("rate", "0"))
    except ValueError:
        flash("Invalid rate amount.", "error")
        return redirect(url_for("admin_panel"))

    if country and rate >= 0:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO country_rates (country, rate) VALUES (?, ?)", (country, rate))
        conn.commit()
        conn.close()
        flash(f"Country rate updated for {country}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/set-service-rate", methods=["POST"])
@admin_login_required
def set_service_rate():
    service = request.form.get("service", "").strip()
    try:
        rate = float(request.form.get("rate", "0"))
    except ValueError:
        flash("Invalid rate amount.", "error")
        return redirect(url_for("admin_panel"))

    if service and rate >= 0:
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO service_rates (service, rate) VALUES (?, ?)", (service, rate))
        conn.commit()
        conn.close()
        flash(f"Service rate updated for {service}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/set-otp-rate-limit", methods=["POST"])
@admin_login_required
def set_otp_rate_limit():
    service = request.form.get("service", "").strip()
    try:
        max_rate = float(request.form.get("max_rate", "0"))
    except ValueError:
        flash("Invalid max rate amount.", "error")
        return redirect(url_for("admin_panel"))

    if service and max_rate >= 0:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO otp_rate_limits (service, max_rate) VALUES (?, ?)",
            (service, max_rate)
        )
        conn.commit()
        conn.close()
        flash(f"OTP rate limit updated for {service}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/withdraw/<req_id>/<decision>", methods=["POST"])
@admin_login_required
def decide_withdraw(req_id, decision):
    conn = get_db()
    w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (req_id,)).fetchone()
    if w and w["status"] == "PENDING":
        decided_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        if decision == "approve":
            conn.execute(
                "UPDATE withdrawals SET status = 'APPROVED', decided_at = ? WHERE id = ?",
                (decided_at, req_id)
            )
            flash(f"Withdraw {req_id} approved.", "success")
        elif decision == "reject":
            reason = request.form.get("reason", "").strip() or "Rejected by Admin."
            conn.execute(
                "UPDATE withdrawals SET status = 'REJECTED', reason = ?, decided_at = ? WHERE id = ?",
                (reason, decided_at, req_id)
            )
            conn.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 4) WHERE email = ?",
                (float(w["amount"]), w["user_email"])
            )
            flash(f"Withdraw {req_id} rejected and balance refunded.", "success")
        conn.commit()
    conn.close()
    return redirect(url_for("admin_panel"))


# ---------------- PROFILE API KEY ACTIONS ----------------

@app.route("/api/profile/api-key/<action>", methods=["POST"])
@user_login_required
def manage_user_api_key(user, action):
    now_str = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
    conn = get_db()
    if action == "regenerate":
        new_key = f"M{secrets.token_hex(6).upper()}"
        conn.execute(
            "UPDATE users SET api_key = ?, api_key_created = ? WHERE email = ?",
            (new_key, now_str, user["email"])
        )
        conn.commit()
        conn.close()
        return jsonify({
            "ok": True,
            "api_key": new_key,
            "created_at": now_str,
            "message": "New API key generated successfully."
        })
    elif action == "revoke":
        conn.execute("UPDATE users SET api_key = NULL WHERE email = ?", (user["email"],))
        conn.commit()
        conn.close()
        return jsonify({
            "ok": True,
            "api_key": None,
            "message": "API key has been revoked."
        })
    conn.close()
    return jsonify({"ok": False, "message": "Invalid action."}), 400


# ---------------- BRIDGE & OTP EARNING LOGIC ----------------

@app.route("/api/bridge/console")
@user_login_required
def bridge_console(user):
    try:
        r = requests.get(CONSOLE_API_URL, headers=upstream_headers(), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"meta": {"code": 500, "status": "error"}, "message": str(e), "data": {"hits": []}})


@app.route("/api/bridge/liveaccess")
@user_login_required
def bridge_liveaccess(user):
    try:
        r = requests.get(LIVEACCESS_API_URL, headers=upstream_headers(), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"meta": {"code": 500, "status": "error"}, "message": str(e), "data": {"services": []}})


@app.route("/api/bridge/getnum", methods=["POST"])
@user_login_required
def bridge_getnum(user):
    payload = request.get_json(silent=True) or {}
    raw_range = str(payload.get("range", "")).strip()
    clean_rid = raw_range.upper().replace("X", "").replace("+", "").strip()

    if not clean_rid:
        return jsonify({"ok": False, "message": "Please enter a valid range."})

    try:
        r = requests.post(
            GETNUM_API_URL,
            headers=upstream_headers(),
            json={"rid": clean_rid},
            timeout=12,
        )
        res_json = r.json()
    except Exception as e:
        return jsonify({"ok": False, "message": f"Upstream API Error: {e}"})

    if res_json.get("meta", {}).get("code") != 200 or not res_json.get("data"):
        return jsonify({"ok": False, "message": res_json.get("message", "Out of stock or invalid range.")})

    data = res_json["data"]
    country = data.get("country", "Default")
    now = time.time()
    num_id = secrets.token_hex(6)
    range_str = raw_range or f"{clean_rid}XXX"
    rate_val = calculate_rate(country, "", user.get("agent_email", ""))

    entry = {
        "id": num_id,
        "range": range_str,
        "full_number": data.get("full_number", ""),
        "national_number": data.get("national_number", ""),
        "no_plus_number": data.get("no_plus_number", ""),
        "country": country,
        "operator": data.get("operator", ""),
        "country_rate": rate_val,
        "created_at": now,
        "expires_at": now + NUMBER_TTL_SECONDS,
        "otp_message": None,
        "earned_amount": 0.0,
    }

    conn = get_db()
    conn.execute("""
        INSERT INTO user_numbers (
            id, user_email, range_val, full_number, national_number, no_plus_number,
            country, operator, country_rate, created_ts, expires_ts, otp_message, earned_amount
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        num_id, user["email"], range_str, entry["full_number"], entry["national_number"],
        entry["no_plus_number"], country, entry["operator"], rate_val,
        entry["created_at"], entry["expires_at"], None, 0.0
    ))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "item": entry})


@app.route("/api/bridge/my-numbers")
@user_login_required
def bridge_my_numbers(user):
    now = time.time()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM user_numbers WHERE user_email = ? ORDER BY created_ts DESC",
        (user["email"],)
    ).fetchall()
    items = [dict(r) for r in rows]

    api_token = get_api_token()
    active_items = [it for it in items if it["expires_ts"] > now and not it["otp_message"]]

    if active_items and api_token:
        try:
            r = requests.get(SUCCESS_OTP_API_URL, headers=upstream_headers(api_token), timeout=10)
            otps = (r.json().get("data") or {}).get("otps") or []
            for it in active_items:
                num_clean = str(it["no_plus_number"] or "")
                if not num_clean:
                    continue
                for otp in otps:
                    otp_num = str(otp.get("number", ""))
                    if otp_num.endswith(num_clean) or num_clean.endswith(otp_num):
                        msg_text = otp.get("message", "")
                        service_sid = detect_service_from_message(msg_text)
                        rate_used = calculate_rate(it["country"], service_sid, user.get("agent_email", ""))
                        now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

                        it["otp_message"] = msg_text
                        it["earned_amount"] = rate_used

                        conn.execute(
                            "UPDATE user_numbers SET otp_message = ?, earned_amount = ? WHERE id = ?",
                            (msg_text, rate_used, it["id"])
                        )
                        conn.execute(
                            """UPDATE users
                               SET balance = ROUND(balance + ?, 4),
                                   lifetime_earning = ROUND(lifetime_earning + ?, 4),
                                   otp_count = otp_count + 1
                               WHERE email = ?""",
                            (rate_used, rate_used, user["email"])
                        )
                        conn.execute(
                            """INSERT INTO otp_logs (
                                created_at, created_ts, user_email, agent_email,
                                number_val, country, service, rate, message
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                now_utc, now, user["email"], user["agent_email"],
                                it["full_number"], it["country"], service_sid, rate_used, msg_text
                            )
                        )
                        conn.commit()
                        break
        except Exception:
            pass

    fresh_user = get_user_dict(user["email"])
    current_balance = float(fresh_user["balance"]) if fresh_user else float(user["balance"])
    conn.close()

    formatted = []
    for it in items:
        remaining = max(0, int(it["expires_ts"] - now))
        formatted.append({
            "id": it["id"],
            "range": it["range_val"],
            "full_number": it["full_number"],
            "national_number": it["national_number"],
            "no_plus_number": it["no_plus_number"],
            "country": it["country"],
            "operator": it["operator"],
            "country_rate": it["country_rate"],
            "created_at": it["created_ts"],
            "expires_at": it["expires_ts"],
            "otp_message": it["otp_message"],
            "earned_amount": it["earned_amount"],
            "remaining_seconds": remaining,
            "expired": remaining == 0,
            "balance": current_balance,
        })

    return jsonify({"ok": True, "numbers": formatted, "balance": current_balance})


# ---------------- USER PAYMENT (Saved Addresses & Withdraw) ----------------

@app.route("/payment/save-address", methods=["POST"])
@user_login_required
def save_address(user):
    gateway = request.form.get("gateway", "").strip()
    label = request.form.get("label", "").strip()
    wallet_address = request.form.get("wallet_address", "").strip()

    if gateway not in ("USDT SOL Network", "bKash", "Nagad"):
        flash("Please select a valid gateway.", "error")
        return redirect(url_for("payment_page"))
    if not label or not wallet_address:
        flash("Label and wallet/account number are required.", "error")
        return redirect(url_for("payment_page"))

    conn = get_db()
    conn.execute("""
        INSERT INTO payment_addresses (id, user_email, gateway, label, address, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        secrets.token_hex(5).upper(),
        user["email"],
        gateway,
        label,
        wallet_address,
        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    ))
    conn.commit()
    conn.close()

    flash("Payment address saved successfully!", "success")
    return redirect(url_for("payment_page"))


@app.route("/payment", methods=["GET", "POST"])
@user_login_required
def payment_page(user):
    conn = get_db()

    if request.method == "POST":
        address_id = request.form.get("address_id", "").strip()
        try:
            amount = float(request.form.get("amount", "0"))
        except ValueError:
            conn.close()
            flash("Invalid withdraw amount.", "error")
            return redirect(url_for("payment_page"))

        addr_row = conn.execute(
            "SELECT * FROM payment_addresses WHERE id = ? AND user_email = ?",
            (address_id, user["email"])
        ).fetchone()

        if not addr_row:
            conn.close()
            flash("Please select a valid saved payment address.", "error")
            return redirect(url_for("payment_page"))

        if amount < 0.50 or amount > float(user["balance"]):
            conn.close()
            flash("Minimum withdraw is $0.50 and must not exceed your balance.", "error")
            return redirect(url_for("payment_page"))

        conn.execute(
            "UPDATE users SET balance = ROUND(balance - ?, 4) WHERE email = ?",
            (amount, user["email"])
        )
        conn.execute("""
            INSERT INTO withdrawals (
                id, user_email, user_name, method, wallet_number, amount,
                status, reason, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', '', ?, '')
        """, (
            secrets.token_hex(5).upper(),
            user["email"],
            user["name"],
            addr_row["gateway"],
            f"{addr_row['label']} ({addr_row['address']})",
            amount,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        ))
        conn.commit()
        conn.close()

        flash("Withdraw request submitted! Waiting for Admin approval.", "success")
        return redirect(url_for("payment_page"))

    user_addresses = [dict(r) for r in conn.execute(
        "SELECT * FROM payment_addresses WHERE user_email = ? ORDER BY rowid DESC", (user["email"],)
    ).fetchall()]

    user_withdraws = [dict(r) for r in conn.execute(
        "SELECT * FROM withdrawals WHERE user_email = ? ORDER BY rowid DESC", (user["email"],)
    ).fetchall()]
    conn.close()

    fresh_user = get_user_dict(user["email"])
    return render_template(
        "payment.html",
        user=fresh_user,
        sidebar_items=SIDEBAR_ITEMS,
        active="payment",
        addresses=user_addresses,
        withdrawals=user_withdraws,
    )


# ---------------- PANEL PAGES ----------------

@app.route("/dashboard")
@user_login_required
def dashboard(user):
    return render_template("dashboard.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="dashboard")


@app.route("/page/<section_key>")
@user_login_required
def section_page(user, section_key):
    if section_key == "payment":
        return redirect(url_for("payment_page"))

    valid = {item["key"]: item for item in SIDEBAR_ITEMS}
    if section_key not in valid:
        return redirect(url_for("dashboard"))

    if section_key == "profile":
        return render_template("profile.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="profile")
    if section_key == "get-number":
        return render_template("get_number.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="get-number")
    if section_key == "console":
        return render_template("console.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="console")
    if section_key == "access-list":
        return render_template("access_list.html", user=user, sidebar_items=SIDEBAR_ITEMS, active=section_key, section=valid[section_key])
    if section_key == "api-docs":
        return render_template("api_docs.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="api-docs", base_path=f"{get_base_url()}/api/v1")

    return render_template("section.html", user=user, sidebar_items=SIDEBAR_ITEMS, active=section_key, section=valid[section_key])


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
