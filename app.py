import os
import time
import smtplib
import secrets
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

UPSTREAM_BASE_URL = os.environ.get(
    "UPSTREAM_BASE_URL",
    "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
).rstrip("/")
UPSTREAM_API_KEY = os.environ.get("UPSTREAM_API_KEY", "")

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "mdrifatahmmed809@gmail.com").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "54wx52@@")
DEFAULT_AGENT_PASS = os.environ.get("AGENT_PASSWORD", "54wx52@@")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
BASE_URL = os.environ.get("BASE_URL", "")

USERS = {}
AGENTS = {}
APPROVAL_TOKENS = {}
USER_NUMBERS = {}      # email -> list of allocated numbers
OTP_EARNING_LOGS = []  # admin ও user লগের জন্য
WITHDRAWALS = []       # bKash / Nagad withdraw requests

# Admin যেগুলো পরিবর্তন করতে পারবে:
COUNTRY_RATES = {
    "United Kingdom": 0.15,
    "Guinea": 0.10,
    "Ivory Coast": 0.10,
    "Bangladesh": 0.08,
    "Default": 0.05,
}

SERVICE_RATES = {
    "WhatsApp": 0.20,
    "Facebook": 0.12,
    "TikTok": 0.12,
    "Telegram": 0.15,
}

NUMBER_TTL_SECONDS = 15 * 60

SIDEBAR_ITEMS = [
    {"key": "dashboard", "label": "Dashboard", "group": None},
    {"key": "get-number", "label": "Get Number", "group": "Dialer Panel"},
    {"key": "console", "label": "Console", "group": "Dialer Panel"},
    {"key": "summary", "label": "Summary", "group": "Dialer Panel"},
    {"key": "access-list", "label": "Access List", "group": "Dialer Panel"},
    {"key": "sender-range", "label": "Sender / Range", "group": "Dialer Panel"},
    {"key": "profile", "label": "Profile", "group": "Account"},
    {"key": "payment", "label": "Payment", "group": "Account"},
    {"key": "tweaks", "label": "Tweaks", "group": "Extra"},
    {"key": "api-docs", "label": "API Docs", "group": "Extra"},
]


def get_base_url():
    if BASE_URL:
        return BASE_URL.rstrip("/")
    return request.host_url.rstrip("/")


def upstream_headers():
    return {
        "mauthapi": UPSTREAM_API_KEY,
        "Content-Type": "application/json",
    }


def calculate_rate(country: str, service_sid: str = "") -> float:
    """Service Rate থাকলে আগে সেটি নেয়, না থাকলে Country Rate, তা না থাকলে Default Rate"""
    if service_sid:
        for k, v in SERVICE_RATES.items():
            if k.lower() == service_sid.strip().lower():
                return float(v)
    if country:
        for k, v in COUNTRY_RATES.items():
            if k.lower() == country.strip().lower():
                return float(v)
    return float(COUNTRY_RATES.get("Default", 0.05))


def detect_service_from_message(msg_text: str) -> str:
    low = (msg_text or "").lower()
    for s in SERVICE_RATES.keys():
        if s.lower() in low:
            return s
    return "OTP"


def send_agent_mail(user):
    agent_email = user["agent_email"]
    approve_token = secrets.token_urlsafe(24)
    reject_token = secrets.token_urlsafe(24)
    APPROVAL_TOKENS[approve_token] = {"email": user["email"], "action": "APPROVED"}
    APPROVAL_TOKENS[reject_token] = {"email": user["email"], "action": "REJECTED"}

    base = get_base_url()
    login_url = f"{base}/login"
    approve_url = f"{base}/agent/decision/{approve_token}"
    reject_url = f"{base}/agent/decision/{reject_token}"

    subject = f"New Account Request: {user['name']} ({user['email']})"
    body = (
        f"New user account created.\n\n"
        f"User Name: {user['name']}\n"
        f"User Email: {user['email']}\n"
        f"Created At: {user['created_at']}\n\n"
        f"Agent Login URL: {login_url}\n"
        f"Approve Link: {approve_url}\n"
        f"Reject Link: {reject_url}\n"
    )

    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = SMTP_USER
            msg["To"] = agent_email
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, [agent_email], msg.as_string())
        except Exception as e:
            print("SMTP Error:", e)


def user_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get("user_email")
        if not email or email not in USERS:
            return redirect(url_for("login"))
        user = USERS[email]
        if user["status"] != "APPROVED":
            return render_template("status.html", user=user)
        return f(user, *args, **kwargs)
    return decorated


def agent_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        agent_email = session.get("agent_email")
        if not agent_email:
            return redirect(url_for("login"))
        return f(agent_email, *args, **kwargs)
    return decorated


def admin_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    if session.get("is_admin"):
        return redirect(url_for("admin_panel"))
    if session.get("agent_email"):
        return redirect(url_for("agent_panel"))
    if session.get("user_email") in USERS:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Admin Login
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))

        # Agent Login
        if email in AGENTS:
            if check_password_hash(AGENTS[email], password):
                session.clear()
                session["agent_email"] = email
                return redirect(url_for("agent_panel"))
            flash("Invalid agent email or password.", "error")
            return redirect(url_for("login"))

        is_assigned_agent = any(u["agent_email"] == email for u in USERS.values())
        if is_assigned_agent and password == DEFAULT_AGENT_PASS:
            AGENTS[email] = generate_password_hash(password)
            session.clear()
            session["agent_email"] = email
            return redirect(url_for("agent_panel"))

        # User Login
        user = USERS.get(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        session.clear()
        session["user_email"] = email
        if user["status"] != "APPROVED":
            return render_template("status.html", user=user)
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        agent_email = request.form.get("agent_email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not agent_email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for("register"))
        if email in USERS:
            flash("An account with this email already exists.", "error")
            return redirect(url_for("register"))

        if agent_email not in AGENTS:
            AGENTS[agent_email] = generate_password_hash(DEFAULT_AGENT_PASS)

        user = {
            "name": name,
            "email": email,
            "agent_email": agent_email,
            "password_hash": generate_password_hash(password),
            "status": "PENDING",
            "reject_reason": "",
            "request_sms": f"Account creation request from {name} ({email})",
            "uid": secrets.token_hex(5).upper(),
            "api_key": f"M{secrets.token_hex(8).upper()}",
            "balance": 0.00,
            "lifetime_earning": 0.00,
            "otp_count": 0,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        }
        USERS[email] = user
        USER_NUMBERS[email] = []
        session.clear()
        session["user_email"] = email

        send_agent_mail(user)
        return render_template("status.html", user=user)

    return render_template("register.html")


# ---------------- AGENT PANEL ----------------

@app.route("/agent-panel")
@agent_login_required
def agent_panel(agent_email):
    requests_list = [u for u in USERS.values() if u["agent_email"] == agent_email]
    return render_template("agent_panel.html", agent_email=agent_email, requests_list=requests_list)


@app.route("/agent/action/<email>/<action>", methods=["POST"])
@agent_login_required
def agent_action(agent_email, email, action):
    user = USERS.get(email)
    if user and user["agent_email"] == agent_email:
        if action == "approve":
            user["status"] = "APPROVED"
            user["reject_reason"] = ""
        elif action == "reject":
            reason = request.form.get("reason", "").strip()
            user["status"] = "REJECTED"
            user["reject_reason"] = reason or "Rejected by Agent."
    return redirect(url_for("agent_panel"))


@app.route("/agent/decision/<token>", methods=["GET", "POST"])
def agent_decision(token):
    data = APPROVAL_TOKENS.get(token)
    if not data:
        return "Invalid or expired approval link.", 404
    user = USERS.get(data["email"])
    if not user:
        return "User not found.", 404
    if request.method == "POST":
        user["status"] = data["action"]
        user["reject_reason"] = request.form.get("reason", "").strip() or "Rejected by Agent."
        APPROVAL_TOKENS.pop(token, None)
        return f"<h3>Account ({user['email']}) marked as {data['action']}.</h3>"
    if data["action"] == "APPROVED":
        user["status"] = "APPROVED"
        APPROVAL_TOKENS.pop(token, None)
        return f"<h3>Approved!</h3><p>{user['email']} is now APPROVED.</p>"
    return f"""
    <body style="background:#0b0e0c;color:#fff;font-family:sans-serif;padding:40px;">
      <h2>Reject Account: {user['email']}</h2>
      <form method="POST">
        <input type="text" name="reason" placeholder="Reason" style="padding:10px;width:280px;">
        <button type="submit" style="padding:10px 18px;background:#dc2626;color:#fff;border:none;">Confirm Reject</button>
      </form>
    </body>
    """


# ---------------- ADMIN PANEL (RATES + OTP LOGS + WITHDRAWALS) ----------------

@app.route("/admin")
@admin_login_required
def admin_panel():
    return render_template(
        "admin_panel.html",
        country_rates=COUNTRY_RATES,
        service_rates=SERVICE_RATES,
        otp_logs=OTP_EARNING_LOGS,
        withdrawals=WITHDRAWALS,
        users=USERS,
    )


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
        COUNTRY_RATES[country] = rate
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
        SERVICE_RATES[service] = rate
    return redirect(url_for("admin_panel"))


@app.route("/admin/withdraw/<req_id>/<decision>", methods=["POST"])
@admin_login_required
def decide_withdraw(req_id, decision):
    for w in WITHDRAWALS:
        if w["id"] == req_id and w["status"] == "PENDING":
            user = USERS.get(w["user_email"])
            if decision == "approve":
                w["status"] = "APPROVED"
                w["decided_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            elif decision == "reject":
                w["status"] = "REJECTED"
                w["reason"] = request.form.get("reason", "").strip() or "Rejected by Admin."
                w["decided_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                if user:
                    # Reject হলে ব্যালেন্স ইউজারের একাউন্টে ফেরত যাবে
                    user["balance"] = round(user["balance"] + w["amount"], 4)
            break
    return redirect(url_for("admin_panel"))


# ---------------- BRIDGE & OTP EARNING LOGIC ----------------

@app.route("/api/bridge/console")
@user_login_required
def bridge_console(user):
    try:
        r = requests.get(f"{UPSTREAM_BASE_URL}/console", headers=upstream_headers(), timeout=10)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"meta": {"code": 500, "status": "error"}, "message": str(e), "data": {"hits": []}})


@app.route("/api/bridge/liveaccess")
@user_login_required
def bridge_liveaccess(user):
    try:
        r = requests.get(f"{UPSTREAM_BASE_URL}/liveaccess", headers=upstream_headers(), timeout=10)
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
            f"{UPSTREAM_BASE_URL}/getnum",
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
    entry = {
        "id": secrets.token_hex(6),
        "range": raw_range or f"{clean_rid}XXX",
        "full_number": data.get("full_number", ""),
        "national_number": data.get("national_number", ""),
        "no_plus_number": data.get("no_plus_number", ""),
        "country": country,
        "operator": data.get("operator", ""),
        "country_rate": calculate_rate(country, ""),
        "created_at": now,
        "expires_at": now + NUMBER_TTL_SECONDS,
        "otp_message": None,
        "earned_amount": 0.0,
    }

    USER_NUMBERS.setdefault(user["email"], []).insert(0, entry)
    return jsonify({"ok": True, "item": entry})


@app.route("/api/bridge/my-numbers")
@user_login_required
def bridge_my_numbers(user):
    now = time.time()
    items = USER_NUMBERS.get(user["email"], [])

    active_items = [it for it in items if it["expires_at"] > now and not it["otp_message"]]
    if active_items and UPSTREAM_API_KEY:
        try:
            r = requests.get(f"{UPSTREAM_BASE_URL}/success-otp", headers=upstream_headers(), timeout=10)
            otps = (r.json().get("data") or {}).get("otps") or []
            for it in active_items:
                num_clean = it["no_plus_number"]
                for otp in otps:
                    otp_num = str(otp.get("number", ""))
                    if otp_num.endswith(num_clean) or num_clean.endswith(otp_num):
                        msg_text = otp.get("message", "")
                        service_sid = detect_service_from_message(msg_text)
                        rate_used = calculate_rate(it["country"], service_sid)

                        it["otp_message"] = msg_text
                        it["earned_amount"] = rate_used

                        user["balance"] = round(user["balance"] + rate_used, 4)
                        user["lifetime_earning"] = round(user["lifetime_earning"] + rate_used, 4)
                        user["otp_count"] += 1

                        OTP_EARNING_LOGS.insert(0, {
                            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                            "user_email": user["email"],
                            "number": it["full_number"],
                            "country": it["country"],
                            "service": service_sid,
                            "rate": rate_used,
                            "message": msg_text,
                        })
                        break
        except Exception:
            pass

    formatted = []
    for it in items:
        remaining = max(0, int(it["expires_at"] - now))
        formatted.append({
            **it,
            "remaining_seconds": remaining,
            "expired": remaining == 0,
            "balance": user["balance"],
        })

    return jsonify({"ok": True, "numbers": formatted, "balance": user["balance"]})


# ---------------- USER PAYMENT (bKash / Nagad Withdraw) ----------------

@app.route("/payment", methods=["GET", "POST"])
@user_login_required
def payment_page(user):
    if request.method == "POST":
        method = request.form.get("method", "").strip()  # bKash / Nagad
        wallet_number = request.form.get("wallet_number", "").strip()
        try:
            amount = float(request.form.get("amount", "0"))
        except ValueError:
            flash("Invalid withdraw amount.", "error")
            return redirect(url_for("payment_page"))

        if method not in ("bKash", "Nagad"):
            flash("Please select bKash or Nagad.", "error")
            return redirect(url_for("payment_page"))
        if not wallet_number:
            flash("Please enter your bKash/Nagad number.", "error")
            return redirect(url_for("payment_page"))
        if amount <= 0 or amount > user["balance"]:
            flash("Insufficient balance or invalid amount.", "error")
            return redirect(url_for("payment_page"))

        user["balance"] = round(user["balance"] - amount, 4)
        WITHDRAWALS.insert(0, {
            "id": secrets.token_hex(5).upper(),
            "user_email": user["email"],
            "user_name": user["name"],
            "method": method,
            "wallet_number": wallet_number,
            "amount": amount,
            "status": "PENDING",
            "reason": "",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "decided_at": "",
        })
        flash("Withdraw request submitted! Waiting for Admin approval.", "success")
        return redirect(url_for("payment_page"))

    user_withdraws = [w for w in WITHDRAWALS if w["user_email"] == user["email"]]
    return render_template(
        "payment.html",
        user=user,
        sidebar_items=SIDEBAR_ITEMS,
        active="payment",
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

    if section_key == "get-number":
        return render_template("get_number.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="get-number")
    if section_key == "console":
        return render_template("console.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="console")
    if section_key in ("access-list", "sender-range"):
        return render_template("access_list.html", user=user, sidebar_items=SIDEBAR_ITEMS, active=section_key, section=valid[section_key])
    if section_key == "api-docs":
        return render_template("api_docs.html", user=user, sidebar_items=SIDEBAR_ITEMS, active="api-docs", base_path=f"{get_base_url()}/api/v1")

    return render_template("section.html", user=user, sidebar_items=SIDEBAR_ITEMS, active=section_key, section=valid[section_key])


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
