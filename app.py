import os
import json
import time
import smtplib
import secrets
import sqlite3
import threading
import requests
from email.mime.text import MIMEText
from functools import wraps
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.db"))

# ---------------- MASTER API & MULTI-PANEL CONFIG ----------------
MASTER_API_CONFIG = {
    "mode": "multi_panel_fallback",
    "panel_order": ["1", "2", "3", "4", "5"],
    "endpoints": {
        "master_getnum": "/api/v1/master/getnum",
        "get_number": "/getnum",
        "live_access": "/liveaccess",
        "success_otp": "/success-otp",
        "console": "/console",
    },
    "mapping": {
        "list_key": "data.hits",
        "range": "range",
        "service": "sid",
        "time": "time",
    },
}

PANELS = {
    "1": {
        "key": "1",
        "slug": "panel1",
        "name": "1 server",
        "setting_key": "voltx_panel_active",
        "base_url": os.environ.get(
            "PANEL1_BASE_URL",
            "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
        ).rstrip("/"),
        "api_key_env": "PANEL1_API_KEY",
        "fallback_key_env": "UPSTREAM_API_KEY",
        "auth_header": "mauthapi",
        "headers": {"mauthapi": os.environ.get("PANEL1_API_KEY", os.environ.get("UPSTREAM_API_KEY", "")).strip()},
        "endpoint": "/console",
        "number_endpoint": "/getnum",
        "mapping": {
            "list_key": "data.hits",
            "range": "range",
            "service": "sid",
            "time": "time",
        },
        "extra_endpoints": {
            "get_number": "/getnum",
            "live_access": "/liveaccess",
            "success_otp": "/success-otp",
        },
        "style": "sr1",
    },
    "2": {
        "key": "2",
        "slug": "panel2",
        "name": "2 server",
        "setting_key": "panel_2_active",
        "base_url": os.environ.get(
            "PANEL2_BASE_URL",
            "https://yesms.online/api"
        ).rstrip("/"),
        "api_key_env": "PANEL2_API_KEY",
        "fallback_key_env": "",
        "auth_header": "authkey",
        "headers": {"authkey": os.environ.get("PANEL2_API_KEY", "").strip()},
        "endpoint": "/console_data",
        "number_endpoint": "/allocate_number",
        "mapping": {
            "list_key": "table",
            "range": "range",
            "service": "service",
            "time": "ts",
        },
        "extra_endpoints": {
            "get_number": "/allocate_number",
            "live_access": "/live_access_data",
            "success_otp": "/user_numbers",
        },
        "style": "yehub",
    },
    "3": {
        "key": "3",
        "slug": "panel3",
        "name": "3 server",
        "setting_key": "panel_3_active",
        "base_url": os.environ.get(
            "PANEL3_BASE_URL",
            "https://slvirtualnumber.com/api/v1/developer"
        ).rstrip("/"),
        "api_key_env": "PANEL3_API_KEY",
        "fallback_key_env": "",
        "auth_header": "x-api-key",
        "headers": {"x-api-key": os.environ.get("PANEL3_API_KEY", "").strip()},
        "endpoint": "/live-console",
        "number_endpoint": "/rent",
        "mapping": {
            "list_key": "data",
            "range": "rangePrefix",
            "service": "service",
            "time": "time",
        },
        "extra_endpoints": {
            "get_number": "/rent",
            "live_access": "/available-countries",
            "success_otp": "/otp-monitor",
        },
        "style": "slvirtual",
    },
    "4": {
        "key": "4",
        "slug": "panel4",
        "name": "4 server",
        "command": "/ZENEX",
        "setting_key": "panel_4_active",
        "base_url": os.environ.get(
            "PANEL4_BASE_URL",
            "https://api.zenexnetwork.com"
        ).rstrip("/"),
        "api_key_env": "PANEL4_API_KEY",
        "fallback_key_env": "",
        "default_api_key": "ZNX_2WQM7CCV7VR8Y5R9P5IVZWDE",
        "auth_header": "mapikey",
        "headers": {"mapikey": os.environ.get("PANEL4_API_KEY", "ZNX_4MXS5N5JVJTLLDJY7GM87EKL").strip()},
        "endpoint": "/v1/active-ranges",
        "number_endpoint": "/v1/getnum",
        "otp_endpoint": "/v1/numsuccess/info",
        "mapping": {
            "list_key": "data.active_ranges",
            "range": "range",
            "service": "service",
            "time": "created_at",
        },
        "extra_endpoints": {
            "get_number": "/v1/getnum",
            "live_access": "/v1/active-ranges",
            "success_otp": "/v1/numsuccess/info",
        },
        "style": "zenex",
    },
    "5": {
        "key": "5",
        "slug": "panel5",
        "name": "5 server",
        "setting_key": "panel_5_active",
        "base_url": os.environ.get(
            "PANEL5_BASE_URL",
            "https://api.zebrasms.com/api/v1"
        ).rstrip("/"),
        "api_key_env": "PANEL5_API_KEY",
        "fallback_key_env": "",
        "default_api_key": "",
        "auth_header": "MAuth",
        "headers": {"MAuth": os.environ.get("PANEL5_API_KEY", "").strip()},
        "endpoint": "/publicapi/getupdate",
        "number_endpoint": "/publicapi/getnum",
        "mapping": {
            "list_key": "data.rows",
            "range": "range",
            "service": "sender",
            "time": "at_ms",
        },
        "extra_endpoints": {
            "get_number": "/publicapi/getnum",
            "live_access": "/publicapi/liveaccess",
            "success_otp": "/publicapi/getupdate",
        },
        "style": "zebrasms",
    },
}


PANEL_BRAND_NAME = PANELS["1"]["name"]
PANEL_OFF_MSG = f"{PANEL_BRAND_NAME} Panel is currently turned OFF by Admin."

# Backward compatibility constants for Panel 1
PRIVATE_API_TOKEN = os.environ.get("PANEL1_API_KEY", os.environ.get("UPSTREAM_API_KEY", "")).strip()
UPSTREAM_BASE_URL = PANELS["1"]["base_url"]
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
    "United Kingdom": 0.0032,
    "Guinea": 0.0032,
    "Ivory Coast": 0.0032,
    "Bangladesh": 0.0032,
    "Default": 0.0032,
}

DEFAULT_SERVICE_RATES = {
    "WhatsApp": 0.0032,
    "Facebook": 0.0032,
    "TikTok": 0.0032,
    "Telegram": 0.0032,
}

NUMBER_TTL_SECONDS = 15 * 60
LIVEACCESS_TTL_SECONDS = 5 * 60 * 60
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


# ---------------- FIREBASE FIRESTORE HELPERS ----------------

_FIRESTORE_CLIENT = None
_FIRESTORE_READY = False


def get_firestore_client():
    global _FIRESTORE_CLIENT, _FIRESTORE_READY
    if _FIRESTORE_READY:
        return _FIRESTORE_CLIENT
    _FIRESTORE_READY = True

    if firebase_admin is None or credentials is None or firestore is None:
        print("Firebase Warning: firebase-admin package is not installed.")
        return None

    raw_json = os.environ.get("FIREBASE_CREDENTIALS_JSON", "").strip()
    if not raw_json:
        print("Firebase Warning: FIREBASE_CREDENTIALS_JSON env var is empty.")
        return None

    try:
        cred_dict = json.loads(raw_json)
        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        _FIRESTORE_CLIENT = firestore.client()
        return _FIRESTORE_CLIENT
    except Exception as e:
        print("Firebase Init Error:", repr(e))
        _FIRESTORE_CLIENT = None
        return None


def fs_set_doc(collection_name: str, doc_id: str, data: dict, merge: bool = True):
    fs = get_firestore_client()
    if not fs or not doc_id:
        return
    try:
        payload = dict(data)
        payload["updated_at_ts"] = time.time()
        fs.collection(collection_name).document(str(doc_id)).set(payload, merge=merge)
    except Exception as e:
        print(f"Firestore set error [{collection_name}/{doc_id}]:", repr(e))


def fs_delete_doc(collection_name: str, doc_id: str):
    fs = get_firestore_client()
    if not fs or not doc_id:
        return
    try:
        fs.collection(collection_name).document(str(doc_id)).delete()
    except Exception as e:
        print(f"Firestore delete error [{collection_name}/{doc_id}]:", repr(e))


def sync_user_to_firestore(email: str):
    if not email:
        return
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    conn.close()
    if row:
        fs_set_doc("users", row["email"], dict(row), merge=True)


def sync_agent_to_firestore(email: str):
    if not email:
        return
    conn = get_db()
    row = conn.execute("SELECT * FROM agents WHERE email = ?", (email.lower(),)).fetchone()
    conn.close()
    if row:
        fs_set_doc("agents", row["email"], dict(row), merge=True)


def sync_setting_to_firestore(key: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM settings WHERE k = ?", (key,)).fetchone()
    conn.close()
    if row:
        fs_set_doc("settings", row["k"], dict(row), merge=True)


def sync_withdrawal_to_firestore(req_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (req_id,)).fetchone()
    conn.close()
    if row:
        fs_set_doc("withdrawals", row["id"], dict(row), merge=True)


def sync_payment_address_to_firestore(addr_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM payment_addresses WHERE id = ?", (addr_id,)).fetchone()
    conn.close()
    if row:
        fs_set_doc("payment_addresses", row["id"], dict(row), merge=True)


def sync_user_number_to_firestore(num_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM user_numbers WHERE id = ?", (num_id,)).fetchone()
    conn.close()
    if row:
        fs_set_doc("user_numbers", row["id"], dict(row), merge=True)


def sync_otp_log_to_firestore(log_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM otp_logs WHERE id = ?", (log_id,)).fetchone()
    conn.close()
    if row:
        d = dict(row)
        fs_set_doc("otp_logs", str(d["id"]), d, merge=True)


def restore_sqlite_from_firestore_if_empty():
    fs = get_firestore_client()
    if not fs:
        return
    try:
        conn = get_db()
        cur = conn.cursor()

        for doc in fs.collection("settings").stream():
            d = doc.to_dict() or {}
            k = d.get("k") or doc.id
            v = d.get("v")
            if k is not None and v is not None:
                cur.execute("INSERT OR IGNORE INTO settings (k, v) VALUES (?, ?)", (str(k), str(v)))

        for doc in fs.collection("agents").stream():
            d = doc.to_dict() or {}
            em = (d.get("email") or doc.id or "").strip().lower()
            if em and d.get("password_hash"):
                cur.execute(
                    "INSERT OR IGNORE INTO agents (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (em, d["password_hash"], d.get("created_at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
                )

        for doc in fs.collection("users").stream():
            d = doc.to_dict() or {}
            em = (d.get("email") or doc.id or "").strip().lower()
            if em and d.get("password_hash"):
                cur.execute("""
                    INSERT OR IGNORE INTO users (
                        email, name, agent_email, password_hash, status, uid, api_key,
                        api_enabled, api_key_created, member_since, last_login, balance,
                        lifetime_earning, otp_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    em,
                    d.get("name") or em.split("@")[0],
                    (d.get("agent_email") or "").strip().lower(),
                    d["password_hash"],
                    d.get("status") or "ACTIVE",
                    d.get("uid") or secrets.token_hex(5).upper(),
                    d.get("api_key"),
                    int(d.get("api_enabled") or 0),
                    d.get("api_key_created"),
                    d.get("member_since"),
                    d.get("last_login"),
                    float(d.get("balance") or 0.0),
                    float(d.get("lifetime_earning") or 0.0),
                    int(d.get("otp_count") or 0),
                    d.get("created_at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                ))

        for doc in fs.collection("country_rates").stream():
            d = doc.to_dict() or {}
            c = d.get("country") or doc.id
            if c and d.get("rate") is not None:
                cur.execute("INSERT OR IGNORE INTO country_rates (country, rate) VALUES (?, ?)", (str(c), float(d["rate"])))

        for doc in fs.collection("service_rates").stream():
            d = doc.to_dict() or {}
            s = d.get("service") or doc.id
            if s and d.get("rate") is not None:
                cur.execute("INSERT OR IGNORE INTO service_rates (service, rate) VALUES (?, ?)", (str(s), float(d["rate"])))

        for doc in fs.collection("otp_rate_limits").stream():
            d = doc.to_dict() or {}
            s = d.get("service") or doc.id
            if s and d.get("max_rate") is not None:
                cur.execute("INSERT OR IGNORE INTO otp_rate_limits (service, max_rate) VALUES (?, ?)", (str(s), float(d["max_rate"])))

        for doc in fs.collection("withdrawals").stream():
            d = doc.to_dict() or {}
            wid = d.get("id") or doc.id
            if wid and d.get("user_email"):
                cur.execute("""
                    INSERT OR IGNORE INTO withdrawals (
                        id, user_email, user_name, method, wallet_number, amount,
                        status, reason, created_at, decided_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(wid),
                    (d.get("user_email") or "").strip().lower(),
                    d.get("user_name") or "",
                    d.get("method") or "",
                    d.get("wallet_number") or "",
                    float(d.get("amount") or 0.0),
                    d.get("status") or "PENDING",
                    d.get("reason") or "",
                    d.get("created_at") or "",
                    d.get("decided_at") or "",
                ))

        for doc in fs.collection("payment_addresses").stream():
            d = doc.to_dict() or {}
            aid = d.get("id") or doc.id
            if aid and d.get("user_email"):
                cur.execute("""
                    INSERT OR IGNORE INTO payment_addresses (
                        id, user_email, gateway, label, address, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(aid),
                    (d.get("user_email") or "").strip().lower(),
                    d.get("gateway") or "",
                    d.get("label") or "",
                    d.get("address") or "",
                    d.get("created_at") or "",
                ))

        conn.commit()
        conn.close()
    except Exception as e:
        print("Firestore restore error:", repr(e))


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
            api_enabled INTEGER NOT NULL DEFAULT 0,
            api_key_created TEXT,
            member_since TEXT,
            last_login TEXT,
            balance REAL NOT NULL DEFAULT 0.0,
            lifetime_earning REAL NOT NULL DEFAULT 0.0,
            otp_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    user_cols = [r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "api_enabled" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN api_enabled INTEGER NOT NULL DEFAULT 0")

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
            earned_amount REAL DEFAULT 0.0,
            panel_key TEXT DEFAULT '1'
        )
    """)

    num_cols = [r["name"] for r in cur.execute("PRAGMA table_info(user_numbers)").fetchall()]
    if "panel_key" not in num_cols:
        cur.execute("ALTER TABLE user_numbers ADD COLUMN panel_key TEXT DEFAULT '1'")

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
            message TEXT,
            panel_key TEXT DEFAULT '1'
        )
    """)

    log_cols = [r["name"] for r in cur.execute("PRAGMA table_info(otp_logs)").fetchall()]
    if "panel_key" not in log_cols:
        cur.execute("ALTER TABLE otp_logs ADD COLUMN panel_key TEXT DEFAULT '1'")

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS liveaccess_seen (
            sid TEXT NOT NULL,
            range_val TEXT NOT NULL,
            first_seen_ts REAL NOT NULL,
            PRIMARY KEY (sid, range_val)
        )
    """)

    cur.execute("INSERT OR IGNORE INTO settings (k, v) VALUES ('agent_mail_auto_account', '1')")
    for p in PANELS.values():
        cur.execute("INSERT OR IGNORE INTO settings (k, v) VALUES (?, '1')", (p["setting_key"],))

    for c, r in DEFAULT_COUNTRY_RATES.items():
        cur.execute("INSERT OR IGNORE INTO country_rates (country, rate) VALUES (?, ?)", (c, r))

    for s, r in DEFAULT_SERVICE_RATES.items():
        cur.execute("INSERT OR IGNORE INTO service_rates (service, rate) VALUES (?, ?)", (s, r))

    conn.commit()
    conn.close()

    restore_sqlite_from_firestore_if_empty()

    sync_setting_to_firestore("agent_mail_auto_account")
    for p in PANELS.values():
        sync_setting_to_firestore(p["setting_key"])

    for c, r in get_country_rates().items():
        fs_set_doc("country_rates", c, {"country": c, "rate": r}, merge=True)
    for s, r in get_service_rates().items():
        fs_set_doc("service_rates", s, {"service": s, "rate": r}, merge=True)


# ---------------- PANEL & SETTINGS HELPERS ----------------

def resolve_panel(raw_panel=None) -> dict:
    val = str(raw_panel or "").strip().lower()
    if not val:
        try:
            val = str(
                request.args.get("panel")
                or request.headers.get("X-Panel")
                or ((request.get_json(silent=True) or {}).get("panel") if request.is_json else "")
                or ""
            ).strip().lower()
        except RuntimeError:
            val = ""

    if val in PANELS:
        return PANELS[val]
    for p in PANELS.values():
        aliases = [
            p["slug"].lower(),
            p["name"].lower(),
            f"panel{p['key']}",
            f"panel_{p['key']}",
        ]
        if p.get("command"):
            aliases.append(p["command"].lstrip("/").lower())
        if val in aliases:
            return p
    return PANELS["1"]


def get_panel_active_flag(panel_key: str = "1") -> bool:
    p = resolve_panel(panel_key)
    conn = get_db()
    row = conn.execute("SELECT v FROM settings WHERE k = ?", (p["setting_key"],)).fetchone()
    conn.close()
    return (row["v"] == "1") if row else True


def set_panel_active_flag(panel_key: str, enabled: bool):
    p = resolve_panel(panel_key)
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)",
        (p["setting_key"], "1" if enabled else "0")
    )
    conn.commit()
    conn.close()
    sync_setting_to_firestore(p["setting_key"])


def get_panels_status_map() -> dict:
    return {
        p["key"]: {
            "key": p["key"],
            "slug": p["slug"],
            "name": p["name"],
            "setting_key": p["setting_key"],
            "active": get_panel_active_flag(p["key"]),
            "configured": bool(get_api_token(p["key"])),
        }
        for p in PANELS.values()
    }


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
    sync_setting_to_firestore("agent_mail_auto_account")


def get_voltx_panel_flag() -> bool:
    return get_panel_active_flag("1")


def set_voltx_panel_flag(enabled: bool):
    set_panel_active_flag("1", enabled)


def panel_off_json(panel_key: str = "1", status_code: int = 503):
    p = resolve_panel(panel_key)
    msg = f"{p['name']} Panel is currently turned OFF by Admin."
    return jsonify({
        "meta": {"code": status_code, "status": "off"},
        "panel": p["key"],
        "panel_name": p["name"],
        "voltx_active": get_panel_active_flag("1"),
        "panel_active": False,
        "message": msg,
        "data": {"hits": [], "services": []},
    }), status_code


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
    if not row:
        return None
    u = dict(row)
    u["api_key_visible"] = u.get("api_key") if int(u.get("api_enabled") or 0) else None
    return u


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
        sync_agent_to_firestore(agent_email)


# ---------------- COMMON & UPSTREAM HELPERS ----------------

def get_base_url():
    if BASE_URL:
        return BASE_URL.rstrip("/")
    try:
        return request.host_url.rstrip("/")
    except RuntimeError:
        return ""


def get_api_token(panel_key: str = "1") -> str:
    p = resolve_panel(panel_key)
    token = os.environ.get(p["api_key_env"], "").strip()
    if not token and p.get("fallback_key_env"):
        token = os.environ.get(p["fallback_key_env"], "").strip()
    if not token and p.get("default_api_key"):
        token = str(p["default_api_key"]).strip()
    return token


def upstream_headers(token: str = None, panel_key: str = "1") -> dict:
    p = resolve_panel(panel_key)
    api_key = (token if token is not None else get_api_token(p["key"])).strip()
    return {
        p["auth_header"]: api_key,
        "Content-Type": "application/json",
    }


def _extract_nested(obj: dict, path_str: str):
    cur = obj
    for part in (path_str or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _format_ts_or_ms(val) -> str:
    if val is None or val == "":
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        fval = float(val)
        if fval > 1e12:
            fval /= 1000.0
        return datetime.utcfromtimestamp(fval).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OSError):
        return str(val)


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
    subject = f"Account Active: {user['name']} ({user['email']})"
    body = (
        f"A user account under your agent email is now active.\n\n"
        f"User Name: {user['name']}\n"
        f"User Email: {user['email']}\n"
        f"Status: {user['status']}\n"
        f"Created At: {user['created_at']}\n\n"
        f"Login URL: {login_url}\n"
    )
    return send_email_smtp(agent_email, subject, body)


# ---------------- LOGIN & PUBLIC API GUARDS ----------------

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


def public_api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        supplied_key = (
            request.headers.get("X-API-Key")
            or request.headers.get("x-api-key")
            or request.headers.get("authkey")
            or request.headers.get("mauthapi")
            or request.headers.get("mapikey")
            or request.headers.get("MAuth")
            or request.args.get("api_key")
            or ((request.get_json(silent=True) or {}).get("api_key") if request.is_json else "")
            or ""
        ).strip()

        if not supplied_key:
            return jsonify({
                "meta": {"code": 401, "status": "unauthorized"},
                "message": "API key is missing. Pass x-api-key / authkey / mauthapi / mapikey / MAuth header."
            }), 401

        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE api_key = ?", (supplied_key,)).fetchone()
        conn.close()

        if not row:
            return jsonify({
                "meta": {"code": 403, "status": "forbidden"},
                "message": "Invalid API key."
            }), 403

        user = dict(row)
        if user["status"] != "ACTIVE":
            return jsonify({
                "meta": {"code": 403, "status": "forbidden"},
                "message": f"Account status is {user['status']}."
            }), 403

        if not int(user.get("api_enabled") or 0):
            return jsonify({
                "meta": {"code": 403, "status": "forbidden"},
                "message": "API permission is not enabled for this account. Contact your Agent."
            }), 403

        return f(user, *args, **kwargs)
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
        email = (request.form.get("login") or request.form.get("email") or "").strip().lower()
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
            flash("Your account is PENDING approval from your Agent.", "error")
            return redirect(url_for("login"))
        if user["status"] == "REJECTED":
            flash("Your account was REJECTED by your Agent.", "error")
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
        sync_user_to_firestore(email)

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
            "api_enabled": 0,
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
                api_enabled, api_key_created, member_since, last_login, balance,
                lifetime_earning, otp_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_data["email"], user_data["name"], user_data["agent_email"],
            user_data["password_hash"], user_data["status"], user_data["uid"],
            user_data["api_key"], user_data["api_enabled"], user_data["api_key_created"],
            user_data["member_since"], user_data["last_login"], user_data["balance"],
            user_data["lifetime_earning"], user_data["otp_count"], user_data["created_at"]
        ))
        conn.commit()
        conn.close()
        sync_user_to_firestore(email)

        if auto_flag:
            send_agent_mail(user_data)
            session.clear()
            session["user_email"] = email
            flash("Account created automatically! Welcome to Dashboard.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Account submitted! Waiting for your Agent's approval.", "success")
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
            fs_set_doc("password_resets", email, {"email": email, "code": code, "expires_ts": expires_ts}, merge=True)

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
                    sync_user_to_firestore(email)
                    sync_agent_to_firestore(email)
                    fs_delete_doc("password_resets", email)
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
        panel_brand_name=PANEL_BRAND_NAME,
    )


@app.route("/agent/api-permission", methods=["POST"])
@agent_login_required
def agent_api_permission(agent_email):
    user_email = request.form.get("user_email", "").strip().lower()
    mode = request.form.get("mode", "enable").strip().lower()

    if not user_email:
        flash("Please enter a user account email.", "error")
        return redirect(url_for("agent_panel"))

    user = get_user_dict(user_email)
    if not user:
        flash(f"No user account found with email '{user_email}'.", "error")
        return redirect(url_for("agent_panel"))

    if user["agent_email"] != agent_email:
        flash(f"User '{user_email}' is not under your agent email.", "error")
        return redirect(url_for("agent_panel"))

    now_str = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
    conn = get_db()

    if mode == "disable":
        conn.execute("UPDATE users SET api_enabled = 0 WHERE email = ?", (user_email,))
        conn.commit()
        conn.close()
        sync_user_to_firestore(user_email)
        flash(f"API permission disabled for {user_email}.", "success")
    else:
        api_key = user.get("api_key") or f"M{secrets.token_hex(6).upper()}"
        api_created = user.get("api_key_created") or now_str
        conn.execute(
            "UPDATE users SET api_enabled = 1, api_key = ?, api_key_created = ? WHERE email = ?",
            (api_key, api_created, user_email)
        )
        conn.commit()
        conn.close()
        sync_user_to_firestore(user_email)
        flash(f"API permission active for {user_email}! API Key is now visible in user's Profile.", "success")

    return redirect(url_for("agent_panel"))


@app.route("/agent/decide-user", methods=["POST"])
@agent_login_required
def agent_decide_user(agent_email):
    user_email = request.form.get("user_email", "").strip().lower()
    decision = request.form.get("decision", "").strip().lower()
    user = get_user_dict(user_email)

    if not user or user["agent_email"] != agent_email:
        flash("User not found or not under your agent email.", "error")
        return redirect(url_for("agent_panel"))

    if user["status"] != "PENDING":
        flash(f"User {user_email} is already {user['status']}.", "error")
        return redirect(url_for("agent_panel"))

    if decision == "approve":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'ACTIVE' WHERE email = ?", (user_email,))
        conn.commit()
        conn.close()
        sync_user_to_firestore(user_email)
        user["status"] = "ACTIVE"
        send_agent_mail(user)
        flash(f"User {user_email} approved (ACTIVE).", "success")
    elif decision == "reject":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'REJECTED' WHERE email = ?", (user_email,))
        conn.commit()
        conn.close()
        sync_user_to_firestore(user_email)
        flash(f"User {user_email} rejected.", "success")
    else:
        flash("Invalid decision.", "error")

    return redirect(url_for("agent_panel"))


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
    fs_set_doc(
        "agent_service_rates",
        f"{agent_email.lower()}__{service.lower()}",
        {"agent_email": agent_email.lower(), "service": service, "rate": rate},
        merge=True
    )

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
        sync_user_to_firestore(user_email)
        flash(f"User {user_email} has been deactivated.", "success")
    elif action == "activate":
        conn = get_db()
        conn.execute("UPDATE users SET status = 'ACTIVE' WHERE email = ?", (user_email,))
        conn.commit()
        conn.close()
        sync_user_to_firestore(user_email)
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

    panels_status = get_panels_status_map()

    return render_template(
        "admin_panel.html",
        agent_mail_auto_account=get_auto_account_flag(),
        voltx_panel_active=panels_status["1"]["active"],
        panel_2_active=panels_status["2"]["active"],
        panel_3_active=panels_status["3"]["active"],
        panel_4_active=panels_status["4"]["active"],
        panel_5_active=panels_status["5"]["active"],
        panels_status=panels_status,
        panel_brand_name=PANEL_BRAND_NAME,
        country_rates=get_country_rates(),
        service_rates=get_service_rates(),
        otp_rate_limits=get_otp_rate_limits(),
        otp_logs=otp_logs,
        withdrawals=withdrawals,
        users=users_dict,
        agents=agents_list,
        agent_account_counts=agent_account_counts,
        max_accounts_per_agent=MAX_ACCOUNTS_PER_AGENT,
    )


@app.route("/admin/toggle-panel/<panel_key>", methods=["POST"])
@admin_login_required
def toggle_any_panel(panel_key):
    p = resolve_panel(panel_key)
    new_state = not get_panel_active_flag(p["key"])
    set_panel_active_flag(p["key"], new_state)
    state_text = "ON" if new_state else "OFF"
    flash(f"{p['name']} Panel is now {state_text}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/toggle-voltx-panel", methods=["POST"])
@admin_login_required
def toggle_voltx_panel():
    return toggle_any_panel("1")


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
    fs_delete_doc("agents", agent_email)

    flash(f"Agent email '{agent_email}' removed.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/toggle-agent-mail-account", methods=["POST"])
@admin_login_required
def toggle_agent_mail_account():
    new_state = not get_auto_account_flag()
    set_auto_account_flag(new_state)
    state_text = "ON (Auto Account Creation Active)" if new_state else "OFF (Pending Agent Approval Required)"
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
        fs_set_doc("country_rates", country, {"country": country, "rate": rate}, merge=True)
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
        fs_set_doc("service_rates", service, {"service": service, "rate": rate}, merge=True)
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
        fs_set_doc("otp_rate_limits", service, {"service": service, "max_rate": max_rate}, merge=True)
        flash(f"OTP rate limit updated for {service}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/withdraw/<req_id>/<decision>", methods=["POST"])
@admin_login_required
def decide_withdraw(req_id, decision):
    conn = get_db()
    w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (req_id,)).fetchone()
    user_email_to_sync = None
    if w and w["status"] == "PENDING":
        user_email_to_sync = w["user_email"]
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

    if w and w["status"] == "PENDING":
        sync_withdrawal_to_firestore(req_id)
        if user_email_to_sync:
            sync_user_to_firestore(user_email_to_sync)
    return redirect(url_for("admin_panel"))


# ---------------- PROFILE API KEY ACTIONS ----------------

@app.route("/api/profile/api-key/<action>", methods=["POST"])
@user_login_required
def manage_user_api_key(user, action):
    if not int(user.get("api_enabled") or 0):
        return jsonify({
            "ok": False,
            "message": "API permission is not enabled for your account. Ask your Agent to click API Permission."
        }), 403

    now_str = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
    conn = get_db()
    if action == "regenerate":
        new_key = f"M{secrets.token_hex(6).upper()}"
        conn.execute(
            "UPDATE users SET api_key = ?, api_enabled = 1, api_key_created = ? WHERE email = ?",
            (new_key, now_str, user["email"])
        )
        conn.commit()
        conn.close()
        sync_user_to_firestore(user["email"])
        return jsonify({
            "ok": True,
            "api_key": new_key,
            "created_at": now_str,
            "message": "New API key generated successfully."
        })
    elif action == "revoke":
        conn.execute("UPDATE users SET api_key = NULL, api_enabled = 0 WHERE email = ?", (user["email"],))
        conn.commit()
        conn.close()
        sync_user_to_firestore(user["email"])
        return jsonify({
            "ok": True,
            "api_key": None,
            "message": "API key has been revoked."
        })
    conn.close()
    return jsonify({"ok": False, "message": "Invalid action."}), 400


# ---------------- SHARED NUMBER & OTP LOGIC (MULTI-PANEL + MASTER FALLBACK) ----------------

def _create_number_for_single_panel(user_dict: dict, raw_range: str, panel_key: str = "1"):
    p = resolve_panel(panel_key)
    if not get_panel_active_flag(p["key"]):
        return {"ok": False, "message": f"{p['name']} Panel is currently turned OFF by Admin."}

    api_token = get_api_token(p["key"])
    if not api_token:
        return {"ok": False, "message": f"{p['name']} API key is not configured in environment ({p['api_key_env']})."}

    clean_rid = str(raw_range or "").upper().replace("X", "").replace("+", "").strip()
    if not clean_rid and p["style"] not in ("slvirtual", "zebrasms", "zenex"):
        return {"ok": False, "message": "Please enter a valid range."}

    expires_ts = None

    try:
        if p["style"] == "sr1":
            num_ep = (p.get("extra_endpoints") or {}).get("get_number") or "/getnum"
            r = requests.post(
                f"{p['base_url']}{num_ep}",
                headers=upstream_headers(api_token, p["key"]),
                json={"rid": clean_rid},
                timeout=12,
            )
            res_json = r.json()
            if res_json.get("meta", {}).get("code") != 200 or not res_json.get("data"):
                return {"ok": False, "message": res_json.get("message", "Out of stock or invalid range.")}
            data = res_json["data"]
            full_num = str(data.get("full_number", "")).strip()
            no_plus = str(data.get("no_plus_number") or full_num.lstrip("+")).strip()
            nat_num = str(data.get("national_number") or no_plus).strip()
            country = data.get("country", "Default")
            operator = data.get("operator", "")

        elif p["style"] == "yehub":
            num_ep = (p.get("extra_endpoints") or {}).get("get_number") or p.get("number_endpoint") or "/allocate_number"
            r = requests.post(
                f"{p['base_url']}{num_ep}",
                headers=upstream_headers(api_token, p["key"]),
                json={"range_id": str(raw_range or clean_rid).strip()},
                timeout=12,
            )
            res_json = r.json()
            data = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
            if not res_json.get("success") and not data.get("full_number") and not data.get("national_number"):
                return {"ok": False, "message": res_json.get("message", "Out of stock or invalid range.")}
            nat_num = str(data.get("national_number") or data.get("number") or "").strip()
            full_num = str(data.get("full_number") or (f"+{nat_num.lstrip('+')}" if nat_num else "")).strip()
            no_plus = str(data.get("no_plus_number") or full_num.lstrip("+")).strip()
            country = data.get("country", "Default")
            operator = data.get("operator", "")

        elif p["style"] == "slvirtual":
            num_ep = (p.get("extra_endpoints") or {}).get("get_number") or p.get("number_endpoint") or "/rent"
            payload_body = {}
            if clean_rid:
                payload_body["rangePrefix"] = clean_rid
            payload_body["service"] = "ANY"
            r = requests.post(
                f"{p['base_url']}{num_ep}",
                headers=upstream_headers(api_token, p["key"]),
                json=payload_body,
                timeout=12,
            )
            res_json = r.json()
            data = res_json.get("data") if isinstance(res_json.get("data"), dict) else {}
            full_num = str(data.get("phoneNumber") or data.get("full_number") or "").strip()
            if not full_num:
                return {"ok": False, "message": res_json.get("message", "Out of stock or invalid range.")}
            no_plus = full_num.lstrip("+")
            nat_num = no_plus
            country = data.get("country", "Default")
            operator = data.get("tag", "")

        elif p["style"] == "zebrasms":
            num_ep = (p.get("extra_endpoints") or {}).get("get_number") or p.get("number_endpoint") or "/publicapi/getnum"
            req_range = str(raw_range or "").strip()
            if not req_range and clean_rid:
                req_range = f"{clean_rid}XXX"
            payload_body = {"range": req_range} if req_range else {}
            r = requests.post(
                f"{p['base_url']}{num_ep}",
                headers=upstream_headers(api_token, p["key"]),
                json=payload_body,
                timeout=12,
            )
            res_json = r.json()
            meta_code = (res_json.get("meta") or {}).get("code")
            rows = ((res_json.get("data") or {}).get("rows")) or []
            if meta_code != 0 or not rows:
                return {"ok": False, "message": res_json.get("message") or "Out of stock or invalid range."}
            row0 = rows[0] if isinstance(rows[0], dict) else {}
            full_num = str(row0.get("number") or "").strip()
            if not full_num:
                return {"ok": False, "message": res_json.get("message") or "No number returned by ZebraSMS."}
            no_plus = full_num.lstrip("+")
            nat_num = no_plus
            country = str(row0.get("country") or "Default")
            operator = str(row0.get("operator") or "")
            if row0.get("range"):
                raw_range = str(row0.get("range")).strip()
            if row0.get("expires_ms"):
                try:
                    exp_val = float(row0.get("expires_ms"))
                    if exp_val > 1e12:
                        exp_val /= 1000.0
                    if exp_val > time.time():
                        expires_ts = exp_val
                except (ValueError, TypeError):
                    pass

        elif p["style"] == "zenex":
            num_ep = (p.get("extra_endpoints") or {}).get("get_number") or p.get("number_endpoint") or "/v1/getnum"
            req_range = str(raw_range or clean_rid).strip()
            payload_body = {
                "is_national": False,
                "remove_plus": False,
            }
            if req_range:
                payload_body["range"] = req_range

            r = requests.post(
                f"{p['base_url']}{num_ep}",
                headers=upstream_headers(api_token, p["key"]),
                json=payload_body,
                timeout=12,
            )
            try:
                res_json = r.json()
            except ValueError:
                return {"ok": False, "message": f"Zenex invalid JSON (HTTP {r.status_code}): {r.text[:150]}"}

            if not isinstance(res_json, dict):
                return {"ok": False, "message": f"Zenex unexpected response (HTTP {r.status_code})."}

            meta = res_json.get("meta") if isinstance(res_json.get("meta"), dict) else {}
            meta_code = meta.get("code")
            meta_status = str(meta.get("status") or "").lower()
            top_status = str(res_json.get("status") or "").lower()

            data = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
            full_num = str(
                data.get("full_number")
                or data.get("number")
                or data.get("copy")
                or data.get("phone")
                or ""
            ).strip()

            explicit_fail = (
                res_json.get("success") is False
                or (meta_code is not None and meta_code not in (0, 200, "0", "200"))
                or (meta_status and meta_status not in ("success", "ok"))
                or (top_status and top_status not in ("success", "ok"))
            )

            if explicit_fail or not full_num:
                err_msg = (
                    res_json.get("message")
                    or res_json.get("error")
                    or meta.get("message")
                    or f"Zenex getnum failed (HTTP {r.status_code})"
                )
                return {"ok": False, "message": err_msg}

            if not full_num.startswith("+"):
                full_num = f"+{full_num}"
            no_plus = full_num.lstrip("+")
            nat_num = str(data.get("national_number") or no_plus).strip()
            country = str(data.get("country") or "Default")
            operator = str(data.get("operator") or "")

        else:
            num_ep = (p.get("extra_endpoints") or {}).get("get_number") or (p.get("number_endpoint") or "").strip()
            if not num_ep:
                return {"ok": False, "message": f"{p['name']} does not have a number allocation endpoint configured."}
            r = requests.post(
                f"{p['base_url']}{num_ep}",
                headers=upstream_headers(api_token, p["key"]),
                json={"range": str(raw_range or clean_rid).strip()},
                timeout=12,
            )
            res_json = r.json()
            data = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
            full_num = str(data.get("full_number") or data.get("number") or "").strip()
            if not full_num:
                return {"ok": False, "message": res_json.get("message", "Out of stock or invalid range.")}
            no_plus = full_num.lstrip("+")
            nat_num = str(data.get("national_number") or no_plus).strip()
            country = str(data.get("country") or "Default")
            operator = str(data.get("operator") or "")

    except Exception as e:
        return {"ok": False, "message": f"{p['name']} API Error: {e}"}

    now = time.time()
    if expires_ts is None:
        expires_ts = now + NUMBER_TTL_SECONDS
    num_id = secrets.token_hex(6)
    range_str = str(raw_range).strip() or (f"{clean_rid}XXX" if clean_rid else "ANY")
    rate_val = calculate_rate(country, "", user_dict.get("agent_email", ""))

    entry = {
        "id": num_id,
        "panel": p["key"],
        "panel_name": p["name"],
        "range": range_str,
        "full_number": full_num,
        "national_number": nat_num,
        "no_plus_number": no_plus,
        "country": country,
        "operator": operator,
        "country_rate": rate_val,
        "created_at": now,
        "expires_at": expires_ts,
        "otp_message": None,
        "earned_amount": 0.0,
    }

    conn = get_db()
    conn.execute("""
        INSERT INTO user_numbers (
            id, user_email, range_val, full_number, national_number, no_plus_number,
            country, operator, country_rate, created_ts, expires_ts, otp_message, earned_amount, panel_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        num_id, user_dict["email"], range_str, entry["full_number"], entry["national_number"],
        entry["no_plus_number"], country, entry["operator"], rate_val,
        entry["created_at"], entry["expires_at"], None, 0.0, p["key"]
    ))
    conn.commit()
    conn.close()
    sync_user_number_to_firestore(num_id)

    return {"ok": True, "item": entry}


def _create_number_for_user(user_dict: dict, raw_range: str, panel_key: str = "master"):
    mode = str(panel_key or "master").strip().lower()
    if mode in ("", "master", "auto", "all"):
        tried_errors = []
        for pk in MASTER_API_CONFIG["panel_order"]:
            p = PANELS.get(pk)
            if not p or not get_panel_active_flag(pk) or not get_api_token(pk):
                continue
            res = _create_number_for_single_panel(user_dict, raw_range, pk)
            if res.get("ok"):
                res["matched_panel"] = pk
                res["matched_panel_name"] = p["name"]
                return res
            tried_errors.append(f"{p['name']}: {res.get('message')}")
        return {
            "ok": False,
            "message": " | ".join(tried_errors) if tried_errors else "No active panel with available number found for this range."
        }
    return _create_number_for_single_panel(user_dict, raw_range, panel_key)


def _fetch_incoming_otps_for_panel(panel_key: str) -> list:
    p = resolve_panel(panel_key)
    if not get_panel_active_flag(p["key"]):
        return []
    api_token = get_api_token(p["key"])
    if not api_token:
        return []

    otps = []
    try:
        if p["style"] == "sr1":
            otp_ep = (p.get("extra_endpoints") or {}).get("success_otp") or "/success-otp"
            r = requests.get(f"{p['base_url']}{otp_ep}", headers=upstream_headers(api_token, p["key"]), timeout=10)
            raw_list = (r.json().get("data") or {}).get("otps") or []
            for item in raw_list:
                otps.append({
                    "number": str(item.get("number") or ""),
                    "message": str(item.get("message") or ""),
                })
        elif p["style"] == "yehub":
            otp_ep = (p.get("extra_endpoints") or {}).get("success_otp") or "/user_numbers"
            r = requests.get(f"{p['base_url']}{otp_ep}", headers=upstream_headers(api_token, p["key"]), timeout=10)
            raw_list = r.json().get("logs") or []
            for item in raw_list:
                otps.append({
                    "number": str(item.get("number") or ""),
                    "message": str(item.get("full_message") or item.get("message") or ""),
                })
        elif p["style"] == "slvirtual":
            otp_ep = (p.get("extra_endpoints") or {}).get("success_otp") or "/otp-monitor"
            r = requests.get(f"{p['base_url']}{otp_ep}?limit=100", headers=upstream_headers(api_token, p["key"]), timeout=10)
            raw_list = r.json().get("data") or []
            for item in raw_list:
                otps.append({
                    "number": str(item.get("phoneNumber") or item.get("number") or ""),
                    "message": str(item.get("fullSms") or item.get("message") or ""),
                })
        elif p["style"] == "zebrasms":
            otp_ep = (p.get("extra_endpoints") or {}).get("success_otp") or "/publicapi/getupdate"
            r = requests.get(f"{p['base_url']}{otp_ep}", headers=upstream_headers(api_token, p["key"]), timeout=10)
            res_json = r.json()
            if (res_json.get("meta") or {}).get("code") == 0:
                raw_list = ((res_json.get("data") or {}).get("rows")) or []
                for item in raw_list:
                    if isinstance(item, dict):
                        otps.append({
                            "number": str(item.get("number") or ""),
                            "message": str(item.get("message") or ""),
                            "sender": str(item.get("sender") or ""),
                        })
        elif p["style"] == "zenex":
            otp_ep = (p.get("extra_endpoints") or {}).get("success_otp") or p.get("otp_endpoint") or "/v1/numsuccess/info"
            r = requests.get(f"{p['base_url']}{otp_ep}", headers=upstream_headers(api_token, p["key"]), timeout=10)
            res_json = r.json()
            data_obj = res_json.get("data") if isinstance(res_json.get("data"), dict) else {}
            raw_list = data_obj.get("otps") or data_obj.get("rows") or res_json.get("otps") or []
            for item in raw_list:
                if isinstance(item, dict):
                    msg_val = item.get("otp") or item.get("message") or item.get("sms") or ""
                    otps.append({
                        "number": str(item.get("number") or item.get("full_number") or ""),
                        "message": str(msg_val),
                        "sender": str(item.get("service") or item.get("sender") or ""),
                    })
    except Exception:
        return []
    return otps


def _fetch_user_numbers_and_sync_otp(user_dict: dict, panel_key: str = None):
    now = time.time()
    conn = get_db()
    if panel_key:
        p = resolve_panel(panel_key)
        rows = conn.execute(
            "SELECT * FROM user_numbers WHERE user_email = ? AND panel_key = ? ORDER BY created_ts DESC",
            (user_dict["email"], p["key"])
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM user_numbers WHERE user_email = ? ORDER BY created_ts DESC",
            (user_dict["email"],)
        ).fetchall()

    items = [dict(r) for r in rows]
    synced_num_ids = []
    synced_log_ids = []

    active_items = [it for it in items if it["expires_ts"] > now and not it["otp_message"]]
    if active_items:
        otps_by_panel = {}
        for it in active_items:
            pk = str(it.get("panel_key") or "1")
            if pk not in otps_by_panel:
                otps_by_panel[pk] = _fetch_incoming_otps_for_panel(pk)

            for otp in otps_by_panel[pk]:
                num_clean = str(it["no_plus_number"] or "").lstrip("+")
                otp_num = str(otp.get("number") or "").lstrip("+")
                if not num_clean or not otp_num:
                    continue
                if otp_num.endswith(num_clean) or num_clean.endswith(otp_num):
                    msg_text = otp.get("message", "")
                    sender_name = str(otp.get("sender") or "").strip()
                    service_sid = sender_name or detect_service_from_message(msg_text)
                    rate_used = calculate_rate(it["country"], service_sid, user_dict.get("agent_email", ""))
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
                        (rate_used, rate_used, user_dict["email"])
                    )
                    cur_log = conn.execute(
                        """INSERT INTO otp_logs (
                            created_at, created_ts, user_email, agent_email,
                            number_val, country, service, rate, message, panel_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            now_utc, now, user_dict["email"], user_dict["agent_email"],
                            it["full_number"], it["country"], service_sid, rate_used, msg_text, pk
                        )
                    )
                    synced_num_ids.append(it["id"])
                    if cur_log.lastrowid:
                        synced_log_ids.append(cur_log.lastrowid)
                    conn.commit()
                    break

    fresh_user = get_user_dict(user_dict["email"])
    current_balance = float(fresh_user["balance"]) if fresh_user else float(user_dict["balance"])
    conn.close()

    if synced_num_ids or synced_log_ids:
        sync_user_to_firestore(user_dict["email"])
        for nid in synced_num_ids:
            sync_user_number_to_firestore(nid)
        for lid in synced_log_ids:
            sync_otp_log_to_firestore(lid)

    formatted = []
    for it in items:
        remaining = max(0, int(it["expires_ts"] - now))
        pk = str(it.get("panel_key") or "1")
        p_info = resolve_panel(pk)
        formatted.append({
            "id": it["id"],
            "panel": pk,
            "panel_name": p_info["name"],
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

    return formatted, current_balance


# ---------------- BACKGROUND OTP LISTENER ----------------

_BG_OTP_STARTED = False
_BG_OTP_LOCK = threading.Lock()


def _background_otp_listener_loop(interval_seconds: int = 5):
    while True:
        try:
            now = time.time()
            conn = get_db()
            rows = conn.execute(
                """SELECT DISTINCT user_email
                   FROM user_numbers
                   WHERE expires_ts > ? AND (otp_message IS NULL OR otp_message = '')""",
                (now,)
            ).fetchall()
            conn.close()

            for r in rows:
                u_email = (r["user_email"] or "").strip().lower()
                if not u_email:
                    continue
                u_dict = get_user_dict(u_email)
                if u_dict and u_dict.get("status") == "ACTIVE":
                    _fetch_user_numbers_and_sync_otp(u_dict)
        except Exception as e:
            print("Background OTP listener error:", repr(e))

        time.sleep(interval_seconds)


def start_background_otp_listener(interval_seconds: int = 5):
    global _BG_OTP_STARTED
    with _BG_OTP_LOCK:
        if _BG_OTP_STARTED:
            return
        _BG_OTP_STARTED = True
        t = threading.Thread(
            target=_background_otp_listener_loop,
            args=(interval_seconds,),
            name="background-otp-listener",
            daemon=True,
        )
        t.start()


# ---------------- SESSION BRIDGE ENDPOINTS (MULTI-PANEL) ----------------

def _filter_liveaccess_5h(payload):
    now = time.time()
    cutoff = now - LIVEACCESS_TTL_SECONDS
    raw_services = ((payload or {}).get("data") or {}).get("services") or []

    conn = get_db()
    conn.execute("DELETE FROM liveaccess_seen WHERE first_seen_ts < ?", (cutoff,))

    filtered_services = []
    for svc in raw_services:
        sid = str(svc.get("sid") or "SERVICE")
        last_at = svc.get("last_at")

        if last_at:
            try:
                ts_val = float(last_at)
                if ts_val > 1e12:
                    ts_val /= 1000.0
                if (now - ts_val) > LIVEACCESS_TTL_SECONDS:
                    continue
            except (ValueError, TypeError):
                try:
                    dt_val = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                    if (now - dt_val.timestamp()) > LIVEACCESS_TTL_SECONDS:
                        continue
                except Exception:
                    pass

        valid_ranges = []
        for rng in (svc.get("ranges") or []):
            rng_str = str(rng).strip()
            if not rng_str:
                continue
            row = conn.execute(
                "SELECT first_seen_ts FROM liveaccess_seen WHERE sid = ? AND range_val = ?",
                (sid, rng_str)
            ).fetchone()
            if row:
                if float(row["first_seen_ts"]) >= cutoff:
                    valid_ranges.append(rng_str)
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO liveaccess_seen (sid, range_val, first_seen_ts) VALUES (?, ?, ?)",
                    (sid, rng_str, now)
                )
                fs_set_doc(
                    "liveaccess_seen",
                    f"{sid}__{rng_str}".replace("/", "_"),
                    {"sid": sid, "range_val": rng_str, "first_seen_ts": now},
                    merge=True
                )
                valid_ranges.append(rng_str)

        if valid_ranges:
            new_svc = dict(svc)
            new_svc["ranges"] = valid_ranges
            filtered_services.append(new_svc)

    conn.commit()
    conn.close()

    if not isinstance(payload, dict):
        payload = {"meta": {"code": 200, "status": "success"}, "data": {}}
    if not isinstance(payload.get("data"), dict):
        payload["data"] = {}
    payload["data"]["services"] = filtered_services
    return payload


def _normalize_liveaccess_for_panel(p: dict, raw_json: dict):
    if p["style"] == "sr1":
        return _filter_liveaccess_5h(raw_json)

    if p["style"] == "yehub":
        ranges_map = (raw_json or {}).get("ranges") or {}
        services = []
        for sid, rng_list in ranges_map.items():
            clean_ranges = [str(x).split("|")[-1].strip() for x in (rng_list or []) if str(x).strip()]
            services.append({"sid": sid, "ranges": clean_ranges})
        return _filter_liveaccess_5h({"meta": {"code": 200, "status": "success"}, "data": {"services": services}})

    if p["style"] == "slvirtual":
        rows = (raw_json or {}).get("data") or []
        by_svc = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("service") or item.get("country") or "SERVICE").strip()
            by_svc.setdefault(sid, [])

            prefix = str(item.get("prefix") or item.get("rangePrefix") or "").strip()
            tag = str(item.get("tag") or "").strip()
            tags_list = item.get("tags") if isinstance(item.get("tags"), list) else []

            candidates = []
            if prefix:
                candidates.append(f"{prefix}XXX" if not prefix.upper().endswith("XXX") else prefix)
            if tag:
                candidates.append(tag)
            for t in tags_list:
                t_clean = str(t or "").strip()
                if t_clean:
                    candidates.append(t_clean)

            for c in candidates:
                if c and c not in by_svc[sid]:
                    by_svc[sid].append(c)

        services = [{"sid": k, "ranges": v} for k, v in by_svc.items() if v]
        return _filter_liveaccess_5h({"meta": {"code": 200, "status": "success"}, "data": {"services": services}})

    if p["style"] == "zenex":
        mp = p.get("mapping") or {}
        raw_list = _extract_nested(raw_json or {}, mp.get("list_key") or "data.active_ranges") or []
        by_svc = {}
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            sid = str(item.get(mp.get("service") or "service") or "SERVICE").strip()
            rng = str(item.get(mp.get("range") or "range") or "").strip()
            by_svc.setdefault(sid, [])
            if rng and rng not in by_svc[sid]:
                by_svc[sid].append(rng)
        services = [{"sid": k, "ranges": v} for k, v in by_svc.items()]
        return _filter_liveaccess_5h({"meta": {"code": 200, "status": "success"}, "data": {"services": services}})

    if p["style"] == "zebrasms":
        rows = ((raw_json or {}).get("data") or {}).get("rows") or []
        services = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("sender") or "SERVICE").strip()
            rng_list = [str(r).strip() for r in (item.get("ranges") or []) if str(r).strip()]
            if rng_list:
                services.append({"sid": sid, "ranges": rng_list})
        return _filter_liveaccess_5h({"meta": {"code": 200, "status": "success"}, "data": {"services": services}})

    return _filter_liveaccess_5h(raw_json)


def _fetch_single_panel_console_hits(p: dict) -> list:
    token = get_api_token(p["key"])
    if not token:
        return []

    hits = []
    if p["style"] == "sr1":
        ep = p.get("endpoint") or "/console"
        r = requests.get(f"{p['base_url']}{ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        res = r.json()
        list_key = (p.get("mapping") or {}).get("list_key") or "data.hits"
        raw_hits = _extract_nested(res if isinstance(res, dict) else {}, list_key) or []
        for h in raw_hits:
            if isinstance(h, dict):
                item = dict(h)
                item["panel"] = p["key"]
                item["panel_name"] = p["name"]
                if not item.get("sid") and item.get("service"):
                    item["sid"] = item.get("service")
                hits.append(item)

    elif p["style"] == "yehub":
        ep = p.get("endpoint") or "/console_data"
        r = requests.get(f"{p['base_url']}{ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        raw = r.json()
        for row in (raw.get("table") or []):
            if isinstance(row, list) and len(row) >= 4:
                svc = str(row[3] or "").strip()
                ts_val = row[4] if len(row) > 4 else None
                hits.append({
                    "range": row[0],
                    "country": row[1],
                    "message": row[2],
                    "service": svc,
                    "sid": svc,
                    "ts": ts_val,
                    "time": ts_val,
                    "panel": p["key"],
                    "panel_name": p["name"],
                })

    elif p["style"] == "slvirtual":
        ep = p.get("endpoint") or "/live-console"
        r = requests.get(f"{p['base_url']}{ep}?limit=50", headers=upstream_headers(token, p["key"]), timeout=10)
        raw = r.json()
        for item in (raw.get("data") or []):
            if not isinstance(item, dict):
                continue
            svc = str(item.get("service") or "").strip()
            t_val = (
                item.get("time")
                or item.get("timestamp")
                or item.get("createdAt")
                or item.get("created_at")
                or item.get("ts")
                or None
            )
            hits.append({
                "number": item.get("maskedNumber", ""),
                "range": item.get("rangePrefix") or item.get("tag", ""),
                "country": item.get("country", ""),
                "service": svc,
                "sid": svc,
                "time": t_val,
                "ts": t_val,
                "message": item.get("rawSnippet") or item.get("otpCode", ""),
                "panel": p["key"],
                "panel_name": p["name"],
            })

    elif p["style"] == "zenex":
        ep = p.get("endpoint") or "/v1/active-ranges"
        mp = p.get("mapping") or {}
        r = requests.get(f"{p['base_url']}{ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        raw = r.json()
        raw_list = _extract_nested(raw if isinstance(raw, dict) else {}, mp.get("list_key") or "data.active_ranges") or []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            svc = str(item.get(mp.get("service") or "service") or "").strip()
            t_val = item.get(mp.get("time") or "created_at")
            hits.append({
                "range": item.get(mp.get("range") or "range", ""),
                "country": item.get("country", ""),
                "service": svc,
                "sid": svc,
                "time": _format_ts_or_ms(t_val) if t_val else "",
                "ts": t_val,
                "message": item.get("message") or f"Active route ({item.get('tag', 'General')}) - {item.get('hits', 0)} hits",
                "panel": p["key"],
                "panel_name": p["name"],
            })

    elif p["style"] == "zebrasms":
        ep = p.get("endpoint") or "/publicapi/getupdate"
        r = requests.get(f"{p['base_url']}{ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        raw = r.json()
        meta_code = (raw.get("meta") or {}).get("code")
        if meta_code == 0:
            rows = ((raw.get("data") or {}).get("rows")) or []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                svc = str(item.get("sender") or "SMS").strip()
                raw_ts = item.get("at_ms")
                formatted_time = _format_ts_or_ms(raw_ts)
                num_str = str(item.get("number") or "").strip()
                clean_digits = num_str.lstrip("+")
                rng_guess = f"{clean_digits[:6]}XXX" if len(clean_digits) >= 6 else clean_digits
                hits.append({
                    "number": num_str,
                    "range": rng_guess,
                    "country": str(item.get("country") or ""),
                    "operator": str(item.get("operator") or ""),
                    "service": svc,
                    "sid": svc,
                    "time": formatted_time,
                    "ts": raw_ts,
                    "message": str(item.get("message") or ""),
                    "panel": p["key"],
                    "panel_name": p["name"],
                })

    return hits


def _fetch_all_panels_console_hits():
    combined_hits = []
    errors = []
    for pk in MASTER_API_CONFIG["panel_order"]:
        p = PANELS.get(pk)
        if not p or not get_panel_active_flag(pk):
            continue
        try:
            combined_hits.extend(_fetch_single_panel_console_hits(p))
        except Exception as e:
            errors.append(f"{p['name']}: {e}")
    return combined_hits, errors


def _fetch_liveaccess_for_panel(p: dict) -> dict:
    token = get_api_token(p["key"])
    if p["style"] == "sr1":
        la_ep = (p.get("extra_endpoints") or {}).get("live_access") or "/liveaccess"
        r = requests.get(f"{p['base_url']}{la_ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        return _normalize_liveaccess_for_panel(p, r.json())
    if p["style"] == "yehub":
        la_ep = (p.get("extra_endpoints") or {}).get("live_access") or "/live_access_data"
        r = requests.get(f"{p['base_url']}{la_ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        return _normalize_liveaccess_for_panel(p, r.json())
    if p["style"] == "slvirtual":
        la_ep = (p.get("extra_endpoints") or {}).get("live_access") or "/available-countries"
        r = requests.get(f"{p['base_url']}{la_ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        return _normalize_liveaccess_for_panel(p, r.json())
    if p["style"] == "zenex":
        la_ep = (p.get("extra_endpoints") or {}).get("live_access") or p.get("endpoint") or "/v1/active-ranges"
        r = requests.get(f"{p['base_url']}{la_ep}", headers=upstream_headers(token, p["key"]), timeout=10)
        return _normalize_liveaccess_for_panel(p, r.json())
    if p["style"] == "zebrasms":
        la_ep = (p.get("extra_endpoints") or {}).get("live_access") or "/publicapi/liveaccess"
        params = {}
        try:
            sender_q = (request.args.get("sender") or "").strip()
            if sender_q:
                params["sender"] = sender_q
        except RuntimeError:
            pass
        r = requests.get(
            f"{p['base_url']}{la_ep}",
            headers=upstream_headers(token, p["key"]),
            params=params,
            timeout=10,
        )
        return _normalize_liveaccess_for_panel(p, r.json())
    return {"meta": {"code": 200, "status": "success"}, "data": {"services": []}}


@app.route("/api/bridge/console")
@user_login_required
def bridge_console(user):
    explicit_panel = (request.args.get("panel") or request.headers.get("X-Panel") or "").strip()
    if explicit_panel and explicit_panel.lower() not in ("all", "master", "auto"):
        p = resolve_panel(explicit_panel)
        if not get_panel_active_flag(p["key"]):
            return panel_off_json(p["key"], 503)
        try:
            hits = _fetch_single_panel_console_hits(p)
            return jsonify({
                "meta": {"code": 200, "status": "success"},
                "panel": p["key"],
                "panel_name": p["name"],
                "voltx_active": get_panel_active_flag("1"),
                "panel_active": True,
                "data": {"hits": hits},
            })
        except Exception as e:
            return jsonify({
                "meta": {"code": 500, "status": "error"},
                "panel": p["key"],
                "message": str(e),
                "data": {"hits": []},
            })

    any_active = any(get_panel_active_flag(pk) for pk in MASTER_API_CONFIG["panel_order"])
    if not any_active:
        return panel_off_json("1", 503)

    combined_hits, errors = _fetch_all_panels_console_hits()
    return jsonify({
        "meta": {"code": 200, "status": "success"},
        "panel": "all",
        "panel_name": "All Servers",
        "voltx_active": get_panel_active_flag("1"),
        "panel_active": True,
        "errors": errors,
        "data": {"hits": combined_hits},
    })


@app.route("/api/bridge/liveaccess")
@user_login_required
def bridge_liveaccess(user):
    p = resolve_panel()
    if not get_panel_active_flag(p["key"]):
        return panel_off_json(p["key"], 503)
    try:
        res = _fetch_liveaccess_for_panel(p)
        if isinstance(res, dict):
            res["panel"] = p["key"]
            res["voltx_active"] = get_panel_active_flag("1")
            res["panel_active"] = True
            res["panel_name"] = p["name"]
        return jsonify(res)
    except Exception as e:
        return jsonify({"meta": {"code": 500, "status": "error"}, "panel": p["key"], "message": str(e), "data": {"services": []}})


@app.route("/api/bridge/getnum", methods=["POST"])
@user_login_required
def bridge_getnum(user):
    payload = request.get_json(silent=True) or {}
    raw_panel = str(payload.get("panel") or "").strip()
    raw_range = str(payload.get("range", "")).strip()
    if raw_panel and raw_panel.lower() not in ("master", "auto", "all"):
        p = resolve_panel(raw_panel)
        if not get_panel_active_flag(p["key"]):
            return panel_off_json(p["key"], 503)
        return jsonify(_create_number_for_user(user, raw_range, p["key"]))
    return jsonify(_create_number_for_user(user, raw_range, "master"))


@app.route("/api/bridge/my-numbers")
@user_login_required
def bridge_my_numbers(user):
    raw_panel = request.args.get("panel", "").strip()
    p = resolve_panel(raw_panel) if raw_panel else None
    formatted, current_balance = _fetch_user_numbers_and_sync_otp(user, p["key"] if p else None)
    return jsonify({
        "ok": True,
        "numbers": formatted,
        "balance": current_balance,
        "panels": get_panels_status_map(),
        "panel_active": get_panel_active_flag(p["key"] if p else "1"),
        "panel_name": p["name"] if p else PANEL_BRAND_NAME,
    })


# ---------------- PUBLIC / USER & MASTER API v1 ENDPOINTS ----------------

@app.route("/api/v1/master/config", methods=["GET"])
@public_api_key_required
def api_v1_master_config(user):
    return jsonify({
        "meta": {"code": 200, "status": "success"},
        "master_config": MASTER_API_CONFIG,
        "panels": get_panels_status_map(),
    })


@app.route("/api/v1/master/getnum", methods=["POST"])
@public_api_key_required
def api_v1_master_getnum(user):
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    raw_range = str(
        payload.get("range")
        or payload.get("rid")
        or payload.get("range_id")
        or payload.get("rangePrefix")
        or ""
    ).strip()
    res = _create_number_for_user(user, raw_range, "master")
    if not res.get("ok"):
        return jsonify({"meta": {"code": 400, "status": "error"}, "message": res.get("message")}), 400
    return jsonify({
        "meta": {"code": 200, "status": "success"},
        "matched_panel": res.get("matched_panel"),
        "matched_panel_name": res.get("matched_panel_name"),
        "data": res["item"],
    })


@app.route("/api/v1/liveaccess", methods=["GET"])
@public_api_key_required
def api_v1_liveaccess(user):
    p = resolve_panel()
    if not get_panel_active_flag(p["key"]):
        return panel_off_json(p["key"], 503)
    try:
        return jsonify(_fetch_liveaccess_for_panel(p))
    except Exception as e:
        return jsonify({"meta": {"code": 500, "status": "error"}, "message": str(e), "data": {"services": []}}), 500


@app.route("/api/v1/getnum", methods=["POST"])
@public_api_key_required
def api_v1_getnum(user):
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    raw_panel = str(payload.get("panel") or request.args.get("panel") or request.headers.get("X-Panel") or "master").strip()
    raw_range = str(
        payload.get("range")
        or payload.get("rid")
        or payload.get("range_id")
        or payload.get("rangePrefix")
        or ""
    ).strip()

    if raw_panel and raw_panel.lower() not in ("master", "auto", "all"):
        p = resolve_panel(raw_panel)
        if not get_panel_active_flag(p["key"]):
            return panel_off_json(p["key"], 503)
        res = _create_number_for_user(user, raw_range, p["key"])
    else:
        res = _create_number_for_user(user, raw_range, "master")

    if not res.get("ok"):
        return jsonify({"meta": {"code": 400, "status": "error"}, "message": res.get("message")}), 400
    return jsonify({
        "meta": {"code": 200, "status": "success"},
        "matched_panel": res.get("matched_panel") or res["item"].get("panel"),
        "matched_panel_name": res.get("matched_panel_name") or res["item"].get("panel_name"),
        "data": res["item"],
    })


@app.route("/api/v1/success-otp", methods=["GET"])
@public_api_key_required
def api_v1_success_otp(user):
    raw_panel = (request.args.get("panel") or request.headers.get("X-Panel") or "").strip()
    p = resolve_panel(raw_panel) if raw_panel else None
    formatted, current_balance = _fetch_user_numbers_and_sync_otp(user, p["key"] if p else None)

    otps = []
    for item in formatted:
        if item.get("otp_message"):
            otps.append({
                "id": item["id"],
                "panel": item["panel"],
                "panel_name": item["panel_name"],
                "number": item["full_number"],
                "range": item["range"],
                "country": item["country"],
                "message": item["otp_message"],
                "earned_amount": item["earned_amount"],
                "created_at": item["created_at"],
            })

    conn = get_db()
    if p:
        log_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM otp_logs WHERE user_email = ? AND panel_key = ? ORDER BY id DESC LIMIT 100",
            (user["email"], p["key"])
        ).fetchall()]
    else:
        log_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM otp_logs WHERE user_email = ? ORDER BY id DESC LIMIT 100",
            (user["email"],)
        ).fetchall()]
    conn.close()

    return jsonify({
        "meta": {"code": 200, "status": "success"},
        "data": {
            "otps": otps,
            "logs": log_rows,
            "balance": current_balance,
        }
    })


@app.route("/api/v1/my-numbers", methods=["GET"])
@public_api_key_required
def api_v1_my_numbers(user):
    raw_panel = request.args.get("panel", "").strip()
    p = resolve_panel(raw_panel) if raw_panel else None
    formatted, current_balance = _fetch_user_numbers_and_sync_otp(user, p["key"] if p else None)
    return jsonify({
        "meta": {"code": 200, "status": "success"},
        "data": {
            "numbers": formatted,
            "balance": current_balance,
        }
    })


@app.route("/api/v1/console", methods=["GET"])
@public_api_key_required
def api_v1_console(user):
    raw_panel = (request.args.get("panel") or request.headers.get("X-Panel") or "all").strip()
    if raw_panel.lower() in ("all", "master", "auto"):
        hits, errors = _fetch_all_panels_console_hits()
        return jsonify({
            "meta": {"code": 200, "status": "success"},
            "panel": "all",
            "errors": errors,
            "data": {"hits": hits},
        })

    p = resolve_panel(raw_panel)
    if not get_panel_active_flag(p["key"]):
        return panel_off_json(p["key"], 503)
    try:
        hits = _fetch_single_panel_console_hits(p)
        return jsonify({
            "meta": {"code": 200, "status": "success"},
            "panel": p["key"],
            "data": {"hits": hits},
        })
    except Exception as e:
        return jsonify({"meta": {"code": 500, "status": "error"}, "message": str(e), "data": {"hits": []}}), 500


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

    addr_id = secrets.token_hex(5).upper()
    conn = get_db()
    conn.execute("""
        INSERT INTO payment_addresses (id, user_email, gateway, label, address, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        addr_id,
        user["email"],
        gateway,
        label,
        wallet_address,
        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    ))
    conn.commit()
    conn.close()
    sync_payment_address_to_firestore(addr_id)

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

        withdraw_id = secrets.token_hex(5).upper()
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
            withdraw_id,
            user["email"],
            user["name"],
            addr_row["gateway"],
            f"{addr_row['label']} ({addr_row['address']})",
            amount,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        ))
        conn.commit()
        conn.close()
        sync_user_to_firestore(user["email"])
        sync_withdrawal_to_firestore(withdraw_id)

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
        panel_brand_name=PANEL_BRAND_NAME,
    )


# ---------------- PANEL PAGES & PUBLIC API DOCS ----------------

@app.route("/api-docs")
@app.route("/api-docs.html")
def public_api_docs():
    email = session.get("user_email")
    user = get_user_dict(email) if email else None
    return render_template(
        "api_docs.html",
        user=user,
        sidebar_items=SIDEBAR_ITEMS,
        active="api-docs",
        base_path=f"{get_base_url()}/api/v1",
        public_docs=True,
        panel_brand_name=PANEL_BRAND_NAME,
        panels_status=get_panels_status_map(),
        master_config=MASTER_API_CONFIG,
    )


@app.route("/dashboard")
@user_login_required
def dashboard(user):
    return render_template(
        "dashboard.html",
        user=user,
        sidebar_items=SIDEBAR_ITEMS,
        active="dashboard",
        voltx_panel_active=get_panel_active_flag("1"),
        panel_brand_name=PANEL_BRAND_NAME,
        panels_status=get_panels_status_map(),
    )


@app.route("/page/<section_key>")
@user_login_required
def section_page(user, section_key):
    if section_key == "payment":
        return redirect(url_for("payment_page"))
    if section_key == "api-docs":
        return redirect(url_for("public_api_docs"))

    valid = {item["key"]: item for item in SIDEBAR_ITEMS}
    if section_key not in valid:
        return redirect(url_for("dashboard"))

    panel_active = get_panel_active_flag("1")
    panels_status = get_panels_status_map()

    if section_key == "profile":
        return render_template(
            "profile.html",
            user=user,
            sidebar_items=SIDEBAR_ITEMS,
            active="profile",
            voltx_panel_active=panel_active,
            panel_brand_name=PANEL_BRAND_NAME,
            panels_status=panels_status,
        )
    if section_key == "get-number":
        return render_template(
            "get_number.html",
            user=user,
            sidebar_items=SIDEBAR_ITEMS,
            active="get-number",
            voltx_panel_active=panel_active,
            panel_brand_name=PANEL_BRAND_NAME,
            panels_status=panels_status,
        )
    if section_key == "console":
        return render_template(
            "console.html",
            user=user,
            sidebar_items=SIDEBAR_ITEMS,
            active="console",
            voltx_panel_active=panel_active,
            panel_brand_name=PANEL_BRAND_NAME,
            panels_status=panels_status,
        )
    if section_key == "access-list":
        return render_template(
            "access_list.html",
            user=user,
            sidebar_items=SIDEBAR_ITEMS,
            active=section_key,
            section=valid[section_key],
            voltx_panel_active=panel_active,
            panel_brand_name=PANEL_BRAND_NAME,
            panels_status=panels_status,
        )

    return render_template(
        "section.html",
        user=user,
        sidebar_items=SIDEBAR_ITEMS,
        active=section_key,
        section=valid[section_key],
        voltx_panel_active=panel_active,
        panel_brand_name=PANEL_BRAND_NAME,
        panels_status=panels_status,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


init_db()
start_background_otp_listener(5)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
