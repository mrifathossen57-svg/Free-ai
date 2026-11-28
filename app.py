# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import re
import sys
import zipfile
import tempfile
import shutil
from telebot import types

# --- Global button styling (Telegram Bot API 9.4+ / pyTelegramBotAPI with style support) ---
def _choose_button_style(text="", callback_data=""):
    """Choose green for positive actions and blue for navigation/information."""
    key = f"{text} {callback_data}".lower()
    success_words = (
        "buy", "upload", "start", "restart", "add", "approve", "confirm",
        "payment", "pay", "submit", "save", "activate", "success", "renew",
    )
    return "success" if any(word in key for word in success_words) else "primary"


def _enable_default_button_style(button_class):
    original_init = button_class.__init__
    def styled_init(self, *args, **kwargs):
        text_value = kwargs.get("text", args[0] if args else "")
        callback_value = kwargs.get("callback_data", "")
        kwargs.setdefault("style", _choose_button_style(text_value, callback_value))
        original_init(self, *args, **kwargs)
    button_class.__init__ = styled_init


# Apply to both inline buttons and regular reply-keyboard buttons throughout the file.
_enable_default_button_style(types.InlineKeyboardButton)
_enable_default_button_style(types.KeyboardButton)
# --- End global button styling ---
import time
from datetime import datetime, timedelta
# Removed unused telegram.* imports as we are using telebot consistently
# from telegram import Update
# from telegram.ext import Updater, CommandHandler, CallbackContext
import psutil
import sqlite3
import json # Kept in case needed elsewhere, but not used in provided logic
import logging # Kept in case needed elsewhere
import signal # Kept in case needed elsewhere
import threading
import re # Added for regex matching in auto-install
import html
import sys # Added for sys.executable
import atexit
import requests # For polling exceptions

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I'am Marco File Host"

def run_flask():
  # Make sure to run on port provided by environment or default to 8080
  port = int(os.environ.get("PORT", 8080))
  app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True # Allows program to exit even if this thread is running
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__)) # Get script's directory

def _parse_env_text(text):
    """Tiny .env parser (KEY=VALUE per line, '#' comments, optional quotes). No external dependency."""
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        if line.lower().startswith('export '): line = line[7:].strip()
        if '=' not in line: continue
        key, _, val = line.partition('=')
        key = key.strip(); val = val.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key): continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"): val = val[1:-1]
        elif ' #' in val: val = val.split(' #', 1)[0].rstrip()
        result[key] = val
    return result

def _load_host_env(path):
    """Load KEY=VALUE pairs from the .env file next to app.py (real environment variables win)."""
    if not os.path.isfile(path): return
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            for k, v in _parse_env_text(f.read()).items():
                os.environ.setdefault(k, v)
    except Exception as e:
        print(f"⚠️ Could not read {path}: {e}")

_load_host_env(os.path.join(BASE_DIR, '.env'))

TOKEN_PATTERN = re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{30,}\b')

# টোকেনটি সরাসরি এখানে ফ্যালব্যাক হিসেবে বসানো হয়েছে
FALLBACK_TOKEN = "8894920799:AAEDSI4AJ5jKQxkADtCiPUdj982MR1CSk8Y"  # <-- নতুন টোকেন দিন

TOKEN = os.environ.get('BOT_TOKEN', '').strip() or FALLBACK_TOKEN.strip()

if not TOKEN_PATTERN.fullmatch(TOKEN):
    sys.exit("❌ BOT_TOKEN missing/invalid. Create a .env file next to app.py with: BOT_TOKEN=your_token")

def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        sys.exit(f"❌ {name} in .env must be a number.")

OWNER_ID = _env_int('OWNER_ID', 6733373257)
ADMIN_ID = _env_int('ADMIN_ID', 6733373257)
YOUR_USERNAME = os.environ.get('OWNER_USERNAME', '@sellerbuyer69')
UPDATE_CHANNEL = os.environ.get('UPDATE_CHANNEL', '@sellerbuyer69')



# Folder setup - using absolute paths
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')   # legacy bots (shared per-user folder)
INSTANCES_DIR = os.path.join(BASE_DIR, 'bot_instances')   # NEW: one isolated folder per hosted bot
IROTECH_DIR = os.path.join(BASE_DIR, 'inf') # Assuming this name is intentional
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

# File upload limits
FREE_USER_LIMIT = 0  # No uploads until an active paid package is approved
SUBSCRIBED_USER_LIMIT = 15 # Changed from 10 to 15
ADMIN_LIMIT = 999       # Changed from 50 to 999
OWNER_LIMIT = float('inf') # Changed from 999 to infinity

# Security limits
MAX_NAME_LEN = 36                    # keeps Telegram callback_data (<=64 bytes) valid
MAX_ZIP_FILES = 2000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ENV_BYTES = 64 * 1024
UPLOAD_COOLDOWN = 3                  # seconds between uploads for normal users
DB_EXTENSIONS = ('.db', '.sqlite', '.sqlite3', '.db3') # files treated as "database" in Bot Status

# Create necessary directories
for _d in (UPLOAD_BOTS_DIR, INSTANCES_DIR, IROTECH_DIR): os.makedirs(_d, exist_ok=True)
try: # owner-only access on Linux/macOS (ignored on Windows)
    os.chmod(IROTECH_DIR, 0o700); os.chmod(INSTANCES_DIR, 0o700)
except OSError: pass

# Initialize bot
bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {} # Stores info about running scripts {script_key: info_dict}
user_subscriptions = {} # {user_id: {'expiry': datetime_object}}
user_files = {} # {user_id: [(file_name, file_type), ...]}
active_users = set() # Set of all user IDs that have interacted with the bot
admin_ids = {ADMIN_ID, OWNER_ID} # Set of admin IDs
bot_locked = False
# free_mode = False # Removed free_mode

# --- Logging Setup ---
# Configure basic logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class _TokenRedactFilter(logging.Filter):
    """Never let a bot token reach the log output."""
    def filter(self, record):
        try:
            msg = record.getMessage()
            red = TOKEN_PATTERN.sub('<redacted-token>', msg)
            if red != msg:
                record.msg = red; record.args = ()
        except Exception: pass
        return True

for _h in logging.getLogger().handlers: _h.addFilter(_TokenRedactFilter())
logging.getLogger('urllib3').setLevel(logging.WARNING)

# --- Command Button Layouts (ReplyKeyboardMarkup) ---
# Telegram ReplyKeyboard buttons do not support custom colors/styles.
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📤 Upload File", "🛒 Buy Package"],
    ["⚡ Bot Speed", "📂 Check Files"],
    ["📜 Package History", "📊 Statistics"],
    ["📞 Contact Owner"],
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = COMMAND_BUTTONS_LAYOUT_USER_SPEC + [["📢 Broadcast", "👑 Admin Panel"]]

# --- Database Setup ---
def init_db():
    """Initialize the database with required tables"""
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False) # Allow access from multiple threads
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_profiles
                     (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_seen TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''') # Added admins table
        # Ensure owner and initial admin are in admins table
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
             c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

def load_data():
    """Load data from database into memory"""
    logger.info("Loading data from database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()

        # Load subscriptions
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"⚠️ Invalid expiry date format for user {user_id}: {expiry}. Skipping.")

        # Load user files
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))

        # Load active users
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())

        # Load admins
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall()) # Load admins into the set

        conn.close()
        logger.info(f"Data loaded: {len(active_users)} users, {len(user_subscriptions)} subscriptions, {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

# Initialize DB and Load Data at startup
init_db()
load_data()
# --- End Database Setup ---

# --- Helper Functions ---
def get_user_folder(user_id):
    """Get or create user's folder for storing files"""
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

# --- Per-bot isolated sandbox, .env support, DB export ---
_SAFE_ENV_KEYS = ('PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR',
                  'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL', 'TZ', 'NODE_PATH',
                  'VIRTUAL_ENV', 'LD_LIBRARY_PATH', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE',
                  'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy')
_SKIP_DIRS = {'node_modules', '__pycache__', 'venv', '.venv', '.git', 'site-packages'}
MAX_TG_UPLOAD = 49 * 1024 * 1024
_last_upload = {} # {user_id: timestamp} upload throttle

def sanitize_file_name(name):
    """Strip any path part and unsafe characters from a user-supplied file name."""
    name = os.path.basename((name or '').replace('\\', '/'))
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name).lstrip('.')
    stem, ext = os.path.splitext(name)
    stem = stem[:max(1, MAX_NAME_LEN - len(ext))] or 'bot'
    return stem + ext

def get_instance_dir(user_id, file_name, create=False):
    """Private folder of ONE hosted bot: its script, .env, logs and database all live here."""
    base = os.path.abspath(os.path.join(INSTANCES_DIR, str(int(user_id))))
    path = os.path.abspath(os.path.join(base, re.sub(r'[^A-Za-z0-9._-]', '_', file_name)))
    if os.path.commonpath([base, path]) != base: raise ValueError("Unsafe bot path")
    if create: os.makedirs(path, exist_ok=True)
    return path

def is_legacy_bot(user_id, file_name):
    """Bots hosted before this update live in the shared per-user folder."""
    in_sandbox = os.path.exists(os.path.join(get_instance_dir(user_id, file_name), file_name))
    return (not in_sandbox) and os.path.exists(os.path.join(get_user_folder(user_id), file_name))

def resolve_bot_paths(user_id, file_name):
    """Return (work_dir, script_path) for a hosted bot (sandbox first, legacy folder as fallback)."""
    inst = get_instance_dir(user_id, file_name)
    if os.path.exists(os.path.join(inst, file_name)): return inst, os.path.join(inst, file_name)
    legacy = get_user_folder(user_id)
    if os.path.exists(os.path.join(legacy, file_name)): return legacy, os.path.join(legacy, file_name)
    return inst, os.path.join(inst, file_name)

def unique_bot_name(user_id, file_name):
    """Same script hosted twice (e.g. with another token) => separate instance with its own folder/DB."""
    existing = {f[0] for f in user_files.get(user_id, [])}
    if file_name not in existing and not os.path.isdir(get_instance_dir(user_id, file_name)): return file_name
    stem, ext = os.path.splitext(file_name)
    n = 2
    while True:
        suffix = f"_{n}"
        cand = stem[:max(1, MAX_NAME_LEN - len(ext) - len(suffix))] + suffix + ext
        if cand not in existing and not os.path.isdir(get_instance_dir(user_id, cand)): return cand
        n += 1

def rmtree_force(path, retries=3):
    def _onerror(func, p, exc_info):
        try: os.chmod(p, 0o700); func(p)
        except Exception: pass
    for _ in range(retries):
        shutil.rmtree(path, onerror=_onerror)
        if not os.path.exists(path): return True
        time.sleep(0.7)
    return not os.path.exists(path)

# ---- .env per bot ----
def read_bot_env(env_dir):
    p = os.path.join(env_dir, '.env')
    if not os.path.isfile(p): return {}
    try:
        with open(p, 'r', encoding='utf-8', errors='ignore') as f: return _parse_env_text(f.read())
    except Exception as e:
        logger.error(f"Could not read env file {p}: {e}"); return {}

def write_bot_env(env_dir, env_dict):
    os.makedirs(env_dir, exist_ok=True)
    lines = []
    for k, v in env_dict.items():
        v = str(v).replace('\r', '').replace('\n', '')
        lines.append(f'{k}="{v}"' if (' ' in v or '#' in v) else f'{k}={v}')
    fd = os.open(os.path.join(env_dir, '.env'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write("\n".join(lines) + "\n")

def build_child_env(user_id, file_name):
    """Environment of a hosted script: only harmless system vars + that bot's own .env.
    The host's own secrets (BOT_TOKEN, OWNER_ID...) are NOT passed on."""
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env['PYTHONIOENCODING'] = 'utf-8'; env['PYTHONUNBUFFERED'] = '1'
    try: env.update(read_bot_env(get_instance_dir(user_id, file_name)))
    except Exception as e: logger.error(f"build_child_env error for {user_id}/{file_name}: {e}")
    return env

def script_expects_env(script_path):
    try:
        with open(script_path, 'r', encoding='utf-8', errors='ignore') as f: code = f.read(200000)
    except Exception: return False
    return bool(re.search(r'os\.environ|os\.getenv|dotenv|process\.env', code))

# ---- database export ----
def find_bot_db_files(work_dir):
    found = []
    if not os.path.isdir(work_dir): return found
    root_real = os.path.realpath(work_dir)
    for root, dirs, files in os.walk(work_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not os.path.islink(os.path.join(root, d))]
        for fn in files:
            if not fn.lower().endswith(DB_EXTENSIONS): continue
            p = os.path.join(root, fn)
            if os.path.islink(p): continue # a symlink could point at host files
            try:
                if os.path.commonpath([root_real, os.path.realpath(p)]) != root_real: continue
            except ValueError: continue
            found.append(p)
    return sorted(found)

def _snapshot_db(src, dst):
    """Consistent copy of a (possibly live) SQLite DB; falls back to a plain copy."""
    try:
        s = sqlite3.connect(src, timeout=5)
        try:
            d = sqlite3.connect(dst)
            try: s.backup(d)
            finally: d.close()
        finally: s.close()
    except sqlite3.Error:
        shutil.copy2(src, dst)

def send_bot_databases(chat_id, work_dir, db_files, file_name):
    sent = 0; skipped = 0
    tmp_dir = tempfile.mkdtemp(prefix='dbexport_')
    try:
        for src in db_files[:10]:
            rel = os.path.relpath(src, work_dir).replace(os.sep, '__')
            dst = os.path.join(tmp_dir, rel)
            try:
                _snapshot_db(src, dst)
                if os.path.getsize(dst) > MAX_TG_UPLOAD: skipped += 1; continue
                with open(dst, 'rb') as fh:
                    bot.send_document(chat_id, fh, visible_file_name=rel, caption=f"🗄️ {file_name} → {rel}")
                sent += 1
            except Exception as e:
                logger.error(f"DB export failed for {src}: {e}", exc_info=True); skipped += 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    extra = f" ({skipped} skipped: too large/error)" if skipped else ""
    more = f"\nℹ️ Only the first 10 of {len(db_files)} files were sent." if len(db_files) > 10 else ""
    bot.send_message(chat_id, f"✅ Sent {sent} database file(s) for `{file_name}`{extra}.{more}", parse_mode='Markdown')

def bot_extra_info(user_id, file_name):
    # Database / Env / Token details are intentionally hidden from the user control panel.
    return ""
# --- End sandbox helpers ---

def get_user_file_limit(user_id):
    """Maximum hosted scripts allowed; an active paid package takes precedence."""
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if _user_package_access_revoked(user_id): return 0
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            row = conn.execute("SELECT bot_limit, expiry FROM package_subscriptions WHERE user_id=?", (user_id,)).fetchone()
        if row and datetime.fromisoformat(row[1]) > datetime.now(): return max(0, int(row[0]))
    except Exception as e:
        logger.warning(f"Could not read package limit for {user_id}: {e}")
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    """Get the number of files uploaded by a user"""
    return len(user_files.get(user_id, []))

def is_bot_running(script_owner_id, file_name): # Parameter renamed for clarity
    """Check if a bot script is currently running for a specific user"""
    script_key = f"{script_owner_id}_{file_name}" # Key uses script_owner_id
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                logger.warning(f"Process {script_info['process'].pid} for {script_key} found in memory but not running/zombie. Cleaning up.")
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try:
                        script_info['log_file'].close()
                    except Exception as log_e:
                        logger.error(f"Error closing log file during zombie cleanup {script_key}: {log_e}")
                if script_key in bot_scripts:
                    del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            logger.warning(f"Process for {script_key} not found (NoSuchProcess). Cleaning up.")
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try:
                     script_info['log_file'].close()
                except Exception as log_e:
                     logger.error(f"Error closing log file during cleanup of non-existent process {script_key}: {log_e}")
            if script_key in bot_scripts:
                 del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process status for {script_key}: {e}", exc_info=True)
            return False
    return False


def kill_process_tree(process_info):
    """Kill a process and all its children, ensuring log file is closed."""
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A') 

    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
                logger.info(f"Closed log file for {script_key} (PID: {getattr(process_info.get('process'), 'pid', 'N/A')})")
            except Exception as log_e:
                logger.error(f"Error closing log file during kill for {script_key}: {log_e}")

        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
           pid = process.pid
           if pid: 
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    logger.info(f"Attempting to kill process tree for {script_key} (PID: {pid}, Children: {[c.pid for c in children]})")

                    for child in children:
                        try:
                            child.terminate()
                            logger.info(f"Terminated child process {child.pid} for {script_key}")
                        except psutil.NoSuchProcess:
                            logger.warning(f"Child process {child.pid} for {script_key} already gone.")
                        except Exception as e:
                            logger.error(f"Error terminating child {child.pid} for {script_key}: {e}. Trying kill...")
                            try: child.kill(); logger.info(f"Killed child process {child.pid} for {script_key}")
                            except Exception as e2: logger.error(f"Failed to kill child {child.pid} for {script_key}: {e2}")

                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        logger.warning(f"Child process {p.pid} for {script_key} still alive. Killing.")
                        try: p.kill()
                        except Exception as e: logger.error(f"Failed to kill child {p.pid} for {script_key} after wait: {e}")

                    try:
                        parent.terminate()
                        logger.info(f"Terminated parent process {pid} for {script_key}")
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired:
                            logger.warning(f"Parent process {pid} for {script_key} did not terminate. Killing.")
                            parent.kill()
                            logger.info(f"Killed parent process {pid} for {script_key}")
                    except psutil.NoSuchProcess:
                        logger.warning(f"Parent process {pid} for {script_key} already gone.")
                    except Exception as e:
                        logger.error(f"Error terminating parent {pid} for {script_key}: {e}. Trying kill...")
                        try: parent.kill(); logger.info(f"Killed parent process {pid} for {script_key}")
                        except Exception as e2: logger.error(f"Failed to kill parent {pid} for {script_key}: {e2}")

                except psutil.NoSuchProcess:
                    logger.warning(f"Process {pid or 'N/A'} for {script_key} not found during kill. Already terminated?")
           else: logger.error(f"Process PID is None for {script_key}.")
        elif log_file_closed: logger.warning(f"Process object missing for {script_key}, but log file closed.")
        else: logger.error(f"Process object missing for {script_key}, and no log file. Cannot kill.")
    except Exception as e:
        logger.error(f"❌ Unexpected error killing process tree for PID {pid or 'N/A'} ({script_key}): {e}", exc_info=True)

# --- Automatic Package Installation & Script Running ---

def attempt_install_pip(module_name, message):
    module_name = (module_name or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}', module_name): # blocks "--index-url ..." style injection
        logger.warning(f"Refusing to pip install suspicious module name: {module_name!r}")
        bot.reply_to(message, "❌ Invalid module name. Refusing to install.")
        return False
    top_level = module_name.split('.')[0]
    package_name = TELEGRAM_MODULES.get(module_name.lower(), TELEGRAM_MODULES.get(top_level.lower(), top_level))
    if package_name is None: 
        logger.info(f"Module '{module_name}' is core. Skipping pip install.")
        return False 
    try:
        bot.reply_to(message, f"🐍 Module `{module_name}` not found. Installing `{package_name}`...", parse_mode='Markdown')
        command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', package_name]
        logger.info(f"Running install: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            logger.info(f"Installed {package_name}. Output:\n{result.stdout}")
            bot.reply_to(message, f"✅ Package `{package_name}` (for `{module_name}`) installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install `{package_name}` for `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except Exception as e:
        error_msg = f"❌ Error installing `{package_name}`: {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message, error_msg)
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Node package `{module_name}` not found. Installing locally...", parse_mode='Markdown')
        command = ['npm', 'install', module_name]
        logger.info(f"Running npm install: {' '.join(command)} in {user_folder}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=user_folder, encoding='utf-8', errors='ignore')
        if result.returncode == 0:
            logger.info(f"Installed {module_name}. Output:\n{result.stdout}")
            bot.reply_to(message, f"✅ Node package `{module_name}` installed locally.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install Node package `{module_name}`.\nLog:\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except FileNotFoundError:
         error_msg = "❌ Error: 'npm' not found. Ensure Node.js/npm are installed and in PATH."
         logger.error(error_msg)
         bot.reply_to(message, error_msg)
         return False
    except Exception as e:
        error_msg = f"❌ Error installing Node package `{module_name}`: {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message, error_msg)
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """Run Python script. script_owner_id is used for the script_key. message_obj_for_reply is for sending feedback."""
    max_attempts = 2 
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run Python script: {script_path} (Key: {script_key}) for user {script_owner_id}")

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found at '{script_path}'!")
             logger.error(f"Script not found: {script_path} for user {script_owner_id}")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            logger.info(f"Running Python pre-check: {' '.join(check_command)}")
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, env=build_child_env(script_owner_id, file_name), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"Python Pre-check early. RC: {return_code}. Stderr: {stderr[:200]}...")
                if return_code != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip().strip("'\"")
                        logger.info(f"Detected missing Python module: {module_name}")
                        if attempt_install_pip(module_name, message_obj_for_reply):
                            logger.info(f"Install OK for {module_name}. Retrying run_script...")
                            bot.reply_to(message_obj_for_reply, f"🔄 Install successful. Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Install failed. Cannot run '{file_name}'.")
                            return
                    else:
                         error_summary = stderr[:500]
                         bot.reply_to(message_obj_for_reply, f"❌ Error in script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix the script.", parse_mode='Markdown')
                         return
            except subprocess.TimeoutExpired:
                logger.info("Python Pre-check timed out (>5s), imports likely OK. Killing check process.")
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
                logger.info("Python Check process killed. Proceeding to long run.")
            except FileNotFoundError:
                 logger.error(f"Python interpreter not found: {sys.executable}")
                 bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
                 return
            except Exception as e:
                 logger.error(f"Error in Python pre-check for {script_key}: {e}", exc_info=True)
                 bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in script pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     logger.warning(f"Python Check process {check_proc.pid} still running. Killing.")
                     check_proc.kill(); check_proc.communicate()

        logger.info(f"Starting long-running Python process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
             logger.error(f"Failed to open log file '{log_file_path}' for {script_key}: {e}", exc_info=True)
             bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
             return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                [sys.executable, script_path], cwd=user_folder, env=build_child_env(script_owner_id, file_name), stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            logger.info(f"Started Python process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id, # Chat ID for potential future direct replies from script, defaults to admin/triggering user
                'script_owner_id': script_owner_id, # Actual owner of the script
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'py', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ Python script '{file_name}' started! (PID: {process.pid}) (For User: {script_owner_id})")
        except FileNotFoundError:
             logger.error(f"Python interpreter {sys.executable} not found for long run {script_key}")
             bot.reply_to(message_obj_for_reply, f"❌ Error: Python interpreter '{sys.executable}' not found.")
             if log_file and not log_file.closed: log_file.close()
             if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            error_msg = f"❌ Error starting Python script '{file_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            bot.reply_to(message_obj_for_reply, error_msg)
            if process and process.poll() is None:
                 logger.warning(f"Killing potentially started Python process {process.pid} for {script_key}")
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        error_msg = f"❌ Unexpected error running Python script '{file_name}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message_obj_for_reply, error_msg)
        if script_key in bot_scripts:
             logger.warning(f"Cleaning up {script_key} due to error in run_script.")
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    """Run JS script. script_owner_id is used for the script_key. message_obj_for_reply is for sending feedback."""
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts. Check logs.")
        return

    script_key = f"{script_owner_id}_{file_name}"
    logger.info(f"Attempt {attempt} to run JS script: {script_path} (Key: {script_key}) for user {script_owner_id}")

    try:
        if not os.path.exists(script_path):
             bot.reply_to(message_obj_for_reply, f"❌ Error: Script '{file_name}' not found at '{script_path}'!")
             logger.error(f"JS Script not found: {script_path} for user {script_owner_id}")
             if script_owner_id in user_files:
                 user_files[script_owner_id] = [f for f in user_files.get(script_owner_id, []) if f[0] != file_name]
             remove_user_file_db(script_owner_id, file_name)
             return

        if attempt == 1:
            check_command = ['node', script_path]
            logger.info(f"Running JS pre-check: {' '.join(check_command)}")
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder, env=build_child_env(script_owner_id, file_name), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"JS Pre-check early. RC: {return_code}. Stderr: {stderr[:200]}...")
                if return_code != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1).strip().strip("'\"")
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                             logger.info(f"Detected missing Node module: {module_name}")
                             if attempt_install_npm(module_name, user_folder, message_obj_for_reply):
                                 logger.info(f"NPM Install OK for {module_name}. Retrying run_js_script...")
                                 bot.reply_to(message_obj_for_reply, f"🔄 NPM Install successful. Retrying '{file_name}'...")
                                 time.sleep(2)
                                 threading.Thread(target=run_js_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt + 1)).start()
                                 return
                             else:
                                 bot.reply_to(message_obj_for_reply, f"❌ NPM Install failed. Cannot run '{file_name}'.")
                                 return
                        else: logger.info(f"Skipping npm install for relative/core: {module_name}")
                    error_summary = stderr[:500]
                    bot.reply_to(message_obj_for_reply, f"❌ Error in JS script pre-check for '{file_name}':\n```\n{error_summary}\n```\nFix script or install manually.", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                logger.info("JS Pre-check timed out (>5s), imports likely OK. Killing check process.")
                if check_proc and check_proc.poll() is None: check_proc.kill(); check_proc.communicate()
                logger.info("JS Check process killed. Proceeding to long run.")
            except FileNotFoundError:
                 error_msg = "❌ Error: 'node' not found. Ensure Node.js is installed for JS files."
                 logger.error(error_msg)
                 bot.reply_to(message_obj_for_reply, error_msg)
                 return
            except Exception as e:
                 logger.error(f"Error in JS pre-check for {script_key}: {e}", exc_info=True)
                 bot.reply_to(message_obj_for_reply, f"❌ Unexpected error in JS pre-check for '{file_name}': {e}")
                 return
            finally:
                 if check_proc and check_proc.poll() is None:
                     logger.warning(f"JS Check process {check_proc.pid} still running. Killing.")
                     check_proc.kill(); check_proc.communicate()

        logger.info(f"Starting long-running JS process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None; process = None
        try: log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"Failed to open log file '{log_file_path}' for JS script {script_key}: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file '{log_file_path}': {e}")
            return
        try:
            startupinfo = None; creationflags = 0
            if os.name == 'nt':
                 startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                 startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(
                ['node', script_path], cwd=user_folder, env=build_child_env(script_owner_id, file_name), stdout=log_file, stderr=log_file,
                stdin=subprocess.PIPE, startupinfo=startupinfo, creationflags=creationflags,
                encoding='utf-8', errors='ignore'
            )
            logger.info(f"Started JS process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj_for_reply.chat.id, # Chat ID for potential future direct replies
                'script_owner_id': script_owner_id, # Actual owner of the script
                'start_time': datetime.now(), 'user_folder': user_folder, 'type': 'js', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply, f"✅ JS script '{file_name}' started! (PID: {process.pid}) (For User: {script_owner_id})")
        except FileNotFoundError:
             error_msg = "❌ Error: 'node' not found for long run. Ensure Node.js is installed."
             logger.error(error_msg)
             if log_file and not log_file.closed: log_file.close()
             bot.reply_to(message_obj_for_reply, error_msg)
             if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            error_msg = f"❌ Error starting JS script '{file_name}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            bot.reply_to(message_obj_for_reply, error_msg)
            if process and process.poll() is None:
                 logger.warning(f"Killing potentially started JS process {process.pid} for {script_key}")
                 kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        error_msg = f"❌ Unexpected error running JS script '{file_name}': {str(e)}"
        logger.error(error_msg, exc_info=True)
        bot.reply_to(message_obj_for_reply, error_msg)
        if script_key in bot_scripts:
             logger.warning(f"Cleaning up {script_key} due to error in run_js_script.")
             kill_process_tree(bot_scripts[script_key])
             del bot_scripts[script_key]

# --- Map Telegram import names to actual PyPI package names ---
TELEGRAM_MODULES = {
    # Main Bot Frameworks
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'telethon.sync': 'telethon', # Handle specific imports
    'from telethon.sync import telegramclient': 'telethon', # Example

    # Additional Libraries (add more specific mappings if import name differs)
    'telepot': 'telepot',
    'pytg': 'pytg',
    'tgcrypto': 'tgcrypto',
    'telegram_upload': 'telegram-upload',
    'telegram_send': 'telegram-send',
    'telegram_text': 'telegram-text',

    # MTProto & Low-Level
    'mtproto': 'telegram-mtproto', # Example, check actual package name
    'tl': 'telethon',  # Part of Telethon, install 'telethon'

    # Utilities & Helpers (examples, verify package names)
    'telegram_utils': 'telegram-utils',
    'telegram_logger': 'telegram-logger',
    'telegram_handlers': 'python-telegram-handlers',

    # Database Integrations (examples)
    'telegram_redis': 'telegram-redis',
    'telegram_sqlalchemy': 'telegram-sqlalchemy',

    # Payment & E-commerce (examples)
    'telegram_payment': 'telegram-payment',
    'telegram_shop': 'telegram-shop-sdk',

    # Testing & Debugging (examples)
    'pytest_telegram': 'pytest-telegram',
    'telegram_debug': 'telegram-debug',

    # Scraping & Analytics (examples)
    'telegram_scraper': 'telegram-scraper',
    'telegram_analytics': 'telegram-analytics',

    # NLP & AI (examples)
    'telegram_nlp': 'telegram-nlp-toolkit',
    'telegram_ai': 'telegram-ai', # Assuming this exists

    # Web & API Integration (examples)
    'telegram_api': 'telegram-api-client',
    'telegram_web': 'telegram-web-integration',

    # Gaming & Interactive (examples)
    'telegram_games': 'telegram-games',
    'telegram_quiz': 'telegram-quiz-bot',

    # File & Media Handling (examples)
    'telegram_ffmpeg': 'telegram-ffmpeg',
    'telegram_media': 'telegram-media-utils',

    # Security & Encryption (examples)
    'telegram_2fa': 'telegram-twofa',
    'telegram_crypto': 'telegram-crypto-bot',

    # Localization & i18n (examples)
    'telegram_i18n': 'telegram-i18n',
    'telegram_translate': 'telegram-translate',

    # Common non-telegram examples
    'bs4': 'beautifulsoup4',
    'requests': 'requests',
    'pillow': 'Pillow', # Note the capitalization difference
    'cv2': 'opencv-python', # Common import name for OpenCV
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil',
    'pandas': 'pandas',
    'numpy': 'numpy',
    'flask': 'Flask',
    'django': 'Django',
    'sqlalchemy': 'SQLAlchemy',
    'asyncio': None, # Core module, should not be installed
    'json': None,    # Core module
    'datetime': None,# Core module
    'os': None,      # Core module
    'sys': None,     # Core module
    're': None,      # Core module
    'time': None,    # Core module
    'math': None,    # Core module
    'random': None,  # Core module
    'logging': None, # Core module
    'threading': None,# Core module
    'subprocess':None,# Core module
    'zipfile':None,  # Core module
    'tempfile':None, # Core module
    'shutil':None,   # Core module
    'sqlite3':None,  # Core module
    'psutil': 'psutil',
    'atexit': None   # Core module

}
# --- End Automatic Package Installation & Script Running ---


# --- Database Operations ---
DB_LOCK = threading.Lock() 

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
            logger.info(f"Saved file '{file_name}' ({file_type}) for user {user_id}")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error saving file for user {user_id}, {file_name}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error saving file for {user_id}, {file_name}: {e}", exc_info=True)
        finally: conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
            logger.info(f"Removed file '{file_name}' for user {user_id} from DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing file for {user_id}, {file_name}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error removing file for {user_id}, {file_name}: {e}", exc_info=True)
        finally: conn.close()

def add_active_user(user_id):
    active_users.add(user_id) 
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
            logger.info(f"Added/Confirmed active user {user_id} in DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error adding active user {user_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error adding active user {user_id}: {e}", exc_info=True)
        finally: conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            expiry_str = expiry.isoformat()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', (user_id, expiry_str))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
            logger.info(f"Saved subscription for {user_id}, expiry {expiry_str}")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error saving subscription for {user_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error saving subscription for {user_id}: {e}", exc_info=True)
        finally: conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
            logger.info(f"Removed subscription for {user_id} from DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing subscription for {user_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error removing subscription for {user_id}: {e}", exc_info=True)
        finally: conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id) 
            logger.info(f"Added admin {admin_id} to DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error adding admin {admin_id}: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error adding admin {admin_id}: {e}", exc_info=True)
        finally: conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID:
        logger.warning("Attempted to remove OWNER_ID from admins.")
        return False 
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('SELECT 1 FROM admins WHERE user_id = ?', (admin_id,))
            if c.fetchone():
                c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
                conn.commit()
                removed = c.rowcount > 0 
                if removed: admin_ids.discard(admin_id); logger.info(f"Removed admin {admin_id} from DB")
                else: logger.warning(f"Admin {admin_id} found but delete affected 0 rows.")
            else:
                logger.warning(f"Admin {admin_id} not found in DB.")
                admin_ids.discard(admin_id)
            return removed
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing admin {admin_id}: {e}"); return False
        except Exception as e: logger.error(f"❌ Unexpected error removing admin {admin_id}: {e}", exc_info=True); return False
        finally: conn.close()
# --- End Database Operations ---

# --- Menu creation (Inline and ReplyKeyboards) ---
def cancel_markup(callback_data="cancel_action"):
    """Reusable English Cancel button for interactive menus and input prompts."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data=callback_data))
    return markup

def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
               types.InlineKeyboardButton('🛒 Buy Package', callback_data='buy_package'))
    markup.add(types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
               types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    markup.add(types.InlineKeyboardButton('❌ Cancel', callback_data='cancel_action'))
    if user_id in admin_ids:
        markup.add(types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'))
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row_buttons_text in layout_to_use:
        markup.add(*[types.KeyboardButton(text) for text in row_buttons_text])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True): # Parameter renamed
    markup = types.InlineKeyboardMarkup(row_width=2)
    # Callbacks use script_owner_id
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    # Database and Env / Token buttons removed as requested.
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data='cancel_action'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('❌ Cancel', callback_data='cancel_action'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('❌ Cancel', callback_data='cancel_action'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup
# --- End Menu Creation ---

# --- File Handling ---
def launch_or_prompt_env(file_path, owner_id, work_dir, bot_name, file_type, message):
    """Start a hosted script without showing Env / Token controls."""
    target = run_script if file_type == 'py' else run_js_script
    threading.Thread(target=target, args=(file_path, owner_id, work_dir, bot_name, message)).start()

def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        logger.info(f"Temp dir for zip: {temp_dir}")
        zip_path = os.path.join(temp_dir, 'upload.zip')
        extract_dir = os.path.join(temp_dir, 'content'); os.makedirs(extract_dir)
        with open(zip_path, 'wb') as new_file: new_file.write(downloaded_file_content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            infos = zip_ref.infolist()
            if len(infos) > MAX_ZIP_FILES: raise zipfile.BadZipFile("Too many files in archive")
            if sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED: raise zipfile.BadZipFile("Archive too large when extracted")
            base_abs = os.path.abspath(extract_dir)
            for member in infos:
                target = os.path.abspath(os.path.join(extract_dir, member.filename))
                if os.path.commonpath([base_abs, target]) != base_abs:
                    raise zipfile.BadZipFile(f"Zip has unsafe path: {member.filename}")
                if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                    raise zipfile.BadZipFile(f"Symlinks are not allowed: {member.filename}")
            zip_ref.extractall(extract_dir)
            logger.info(f"Extracted zip to {extract_dir}")

        extracted_items = os.listdir(extract_dir)
        py_files = [f for f in extracted_items if f.endswith('.py')]
        js_files = [f for f in extracted_items if f.endswith('.js')]
        req_file = 'requirements.txt' if 'requirements.txt' in extracted_items else None
        pkg_json = 'package.json' if 'package.json' in extracted_items else None

        if req_file:
            req_path = os.path.join(extract_dir, req_file)
            logger.info(f"requirements.txt found, installing: {req_path}")
            bot.reply_to(message, f"🔄 Installing Python deps from `{req_file}`...")
            try:
                command = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', req_path]
                result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
                logger.info(f"pip install from requirements.txt OK. Output:\n{result.stdout}")
                bot.reply_to(message, f"✅ Python deps from `{req_file}` installed.")
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Python deps from `{req_file}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                logger.error(error_msg)
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 error_msg = f"❌ Unexpected error installing Python deps: {e}"
                 logger.error(error_msg, exc_info=True); bot.reply_to(message, error_msg); return

        if pkg_json:
            logger.info(f"package.json found, npm install in: {extract_dir}")
            bot.reply_to(message, f"🔄 Installing Node deps from `{pkg_json}`...")
            try:
                command = ['npm', 'install']
                result = subprocess.run(command, capture_output=True, text=True, check=True, cwd=extract_dir, encoding='utf-8', errors='ignore')
                logger.info(f"npm install OK. Output:\n{result.stdout}")
                bot.reply_to(message, f"✅ Node deps from `{pkg_json}` installed.")
            except FileNotFoundError:
                bot.reply_to(message, "❌ 'npm' not found. Cannot install Node deps."); return
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Node deps from `{pkg_json}`.\nLog:\n```\n{e.stderr or e.stdout}\n```"
                logger.error(error_msg)
                if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (Log truncated)"
                bot.reply_to(message, error_msg, parse_mode='Markdown'); return
            except Exception as e:
                 error_msg = f"❌ Unexpected error installing Node deps: {e}"
                 logger.error(error_msg, exc_info=True); bot.reply_to(message, error_msg); return

        main_script_name = None; file_type = None
        preferred_py = ['main.py', 'bot.py', 'app.py']; preferred_js = ['index.js', 'main.js', 'bot.js', 'app.js']
        for p in preferred_py:
            if p in py_files: main_script_name = p; file_type = 'py'; break
        if not main_script_name:
             for p in preferred_js:
                 if p in js_files: main_script_name = p; file_type = 'js'; break
        if not main_script_name:
            if py_files: main_script_name = py_files[0]; file_type = 'py'
            elif js_files: main_script_name = js_files[0]; file_type = 'js'
        if not main_script_name:
            bot.reply_to(message, "❌ No `.py` or `.js` script found in archive!"); return

        # Every upload gets its OWN folder (=> its own database, .env, logs). Same name => separate instance.
        bot_name = unique_bot_name(user_id, sanitize_file_name(main_script_name))
        work_dir = get_instance_dir(user_id, bot_name, create=True)
        logger.info(f"Moving extracted files from {extract_dir} to {work_dir}")
        for item_name in os.listdir(extract_dir):
            shutil.move(os.path.join(extract_dir, item_name), os.path.join(work_dir, item_name))
        if bot_name != main_script_name:
            os.replace(os.path.join(work_dir, main_script_name), os.path.join(work_dir, bot_name))
            bot.reply_to(message, f"ℹ️ Hosted as separate instance `{bot_name}` (own folder & database).", parse_mode='Markdown')

        save_user_file(user_id, bot_name, file_type)
        logger.info(f"Saved main script '{bot_name}' ({file_type}) for {user_id} from zip.")
        main_script_path = os.path.join(work_dir, bot_name)
        bot.reply_to(message, f"✅ Files extracted. Main script: `{bot_name}`", parse_mode='Markdown')
        launch_or_prompt_env(main_script_path, user_id, work_dir, bot_name, file_type, message)

    except zipfile.BadZipFile as e:
        logger.error(f"Bad zip file from {user_id}: {e}")
        bot.reply_to(message, f"❌ Error: Invalid/corrupted ZIP. {e}")
    except Exception as e:
        logger.error(f"❌ Error processing zip for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing zip: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir); logger.info(f"Cleaned temp dir: {temp_dir}")
            except Exception as e: logger.error(f"Failed to clean temp dir {temp_dir}: {e}", exc_info=True)

def handle_js_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'js')
        launch_or_prompt_env(file_path, script_owner_id, user_folder, file_name, 'js', message)
    except Exception as e:
        logger.error(f"❌ Error processing JS file {file_name} for {script_owner_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing JS file: {str(e)}")

def handle_py_file(file_path, script_owner_id, user_folder, file_name, message):
    try:
        save_user_file(script_owner_id, file_name, 'py')
        launch_or_prompt_env(file_path, script_owner_id, user_folder, file_name, 'py', message)
    except Exception as e:
        logger.error(f"❌ Error processing Python file {file_name} for {script_owner_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error processing Python file: {str(e)}")
# --- End File Handling ---


# --- Logic Functions (called by commands and text handlers) ---
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username

    logger.info(f"Welcome request from user_id: {user_id}, username: @{user_username}")
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO user_profiles(user_id,username,first_name,last_seen) VALUES(?,?,?,?)",
                         (user_id, user_username or '', user_name or 'User', datetime.now().isoformat()))
    except Exception as profile_error:
        logger.warning("Could not save user profile: %s", profile_error)

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    user_bio = "Could not fetch bio"; photo_file_id = None
    try: user_bio = bot.get_chat(user_id).bio or "No bio"
    except Exception: pass
    try:
        user_profile_photos = bot.get_user_profile_photos(user_id, limit=1)
        if user_profile_photos.photos: photo_file_id = user_profile_photos.photos[0][-1].file_id
    except Exception: pass

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            owner_notification = (f"🎉 New user!\n👤 Name: {user_name}\n✳️ User: @{user_username or 'N/A'}\n"
                                  f"🆔 ID: `{user_id}`\n📝 Bio: {user_bio}")
            bot.send_message(OWNER_ID, owner_notification, parse_mode='Markdown')
            if photo_file_id: bot.send_photo(OWNER_ID, photo_file_id, caption=f"Pic of new user {user_id}")
        except Exception as e: logger.error(f"⚠️ Failed to notify owner about new user {user_id}: {e}")

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    paid_package = active_package_for_user(user_id) if user_id not in admin_ids else None
    if user_id == OWNER_ID: user_status = "👑 Owner"
    elif user_id in admin_ids: user_status = "🛡️ Admin"
    elif paid_package:
        user_status = f"⭐ Premium — {paid_package['name']}"
        try:
            days_left = max(0, (datetime.fromisoformat(paid_package['expiry']) - datetime.now()).days)
            expiry_info = f"\n⏳ প্যাকেজের মেয়াদ বাকি: {days_left} দিন"
        except Exception: pass
    else:
        user_status = "🆓 No Package"

    safe_name = html.escape(str(user_name or 'User'))
    safe_username = html.escape('@' + user_username if user_username else 'Not set')
    access_note = ("✅ আপনার প্যাকেজ সক্রিয়। নিচের 📤 Upload File বাটনে ফাইল দিন।" if (user_id in admin_ids or paid_package)
                   else "🔒 ফাইল আপলোড করতে আগে 🛒 Buy Package থেকে প্যাকেজ কিনুন। পেমেন্ট Admin approve করলে Upload File চালু হবে।")
    welcome_msg_text = (f"〽️ <b>Welcome, {safe_name}!</b>\n\n"
                        f"🆔 <b>Your User ID:</b> <code>{user_id}</code>\n"
                        f"✳️ <b>Username:</b> <code>{safe_username}</code>\n"
                        f"🔰 <b>Status:</b> {html.escape(user_status)}{html.escape(expiry_info)}\n"
                        f"📁 <b>Files Uploaded:</b> {current_files} / {html.escape(limit_str)}\n\n"
                        f"🤖 Python (.py) এবং JavaScript (.js) স্ক্রিপ্ট হোস্ট করুন।\n"
                        f"🗂️ একক ফাইল অথবা .zip archive আপলোড করা যাবে।\n\n"
                        f"{html.escape(access_note)}\n\n"
                        f"👇 <b>নিচের বাটন ব্যবহার করুন।</b>")
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    try:
        if photo_file_id: bot.send_photo(chat_id, photo_file_id)
        bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error sending welcome to {user_id}: {e}", exc_info=True)
        try: bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='HTML')
        except Exception as fallback_e: logger.error(f"Fallback send_message failed for {user_id}: {fallback_e}")

def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    markup.add(types.InlineKeyboardButton('❌ Cancel', callback_data='cancel_action'))
    bot.reply_to(message, "Visit our Updates Channel:", reply_markup=markup)

def _user_package_access_revoked(user_id):
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            return conn.execute("SELECT 1 FROM package_access_revoked WHERE user_id=?", (user_id,)).fetchone() is not None
    except Exception as exc:
        logger.error(f"Could not verify package revocation for {user_id}: {exc}")
        # Fail closed: a DB problem must not accidentally grant upload access.
        return True

def _user_can_upload(user_id):
    """Only admins/owner or users with an active, approved, non-revoked package can upload."""
    if user_id in admin_ids:
        return True
    if _user_package_access_revoked(user_id):
        return False
    try:
        return active_package_for_user(user_id) is not None
    except Exception:
        return False

def _upload_package_required(message):
    bot.reply_to(message, "🔒 ফাইল আপলোড করতে আগে 🛒 Buy Package থেকে প্যাকেজ কিনুন। পেমেন্ট Admin approve করার পর 📤 Upload File চালু হবে।")

def _logic_upload_file(message):
    user_id = message.from_user.id
    if not _user_can_upload(user_id):
        _upload_package_required(message)
        return
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin, cannot accept files.")
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files first.")
        return
    bot.reply_to(message, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.", reply_markup=cancel_markup())

def _logic_check_files(message):
    user_id = message.from_user.id
    # chat_id = message.chat.id # user_id will be used as script_owner_id for buttons
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, "📂 Your files:\n\n(No files uploaded yet)", reply_markup=cancel_markup())
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name) # Use user_id for checking status
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        # Callback data includes user_id as script_owner_id
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    bot.reply_to(message, "📂 Your files:\nClick to manage.", reply_markup=markup, parse_mode='Markdown')

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start_time_ping = time.time()
    wait_msg = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_time_ping) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        # mode = "💰 Free Mode: ON" if free_mode else "💸 Free Mode: OFF" # Removed free_mode
        if user_id == OWNER_ID: user_level = "👑 Owner"
        elif user_id in admin_ids: user_level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "⭐ Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     # f"模式 Mode: {mode}\n" # Removed
                     f"👤 Your Level: {active_package_for_user(user_id)['name'] if active_package_for_user(user_id) else ('🆓 No package' if user_level == '🆓 Free User' else user_level)}")
        bot.edit_message_text(speed_msg, chat_id, wait_msg.message_id)
    except Exception as e:
        logger.error(f"Error during speed test (cmd): {e}", exc_info=True)
        bot.edit_message_text("❌ Error during speed test.", chat_id, wait_msg.message_id)

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    markup.add(types.InlineKeyboardButton('❌ Cancel', callback_data='cancel_action'))
    bot.reply_to(message, "Click to contact Owner:", reply_markup=markup)

# --- Admin Logic Functions ---
def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "💳 Subscription Management\nUse inline buttons from /start or admin command menu.", reply_markup=create_subscription_menu())

def _logic_statistics(message):
    user_id = message.from_user.id
    files = user_files.get(user_id, [])
    running = sum(1 for name, _ in files if is_bot_running(user_id, name))
    limit = get_user_file_limit(user_id)
    package = active_package_for_user(user_id)
    level = package["name"] if package else ("⭐ Legacy Premium" if user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now() else "🆓 No package")
    text = (f"📊 My Status\n\n🔰 {level}\n\n"
            f"🤖 Bots: {len(files)} / {limit if limit != float('inf') else '∞'}   🟢 On: {running}   🔴 Off: {max(0, len(files)-running)}\n")
    if not files: text += "(No bots hosted yet)"
    bot.reply_to(message, text)


def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "📢 Send message to broadcast to all active users.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    logger.warning(f"Bot {status} by Admin {message.from_user.id} via command/button.")
    bot.reply_to(message, f"🔒 Bot has been {status}.")

# def _logic_toggle_free_mode(message): # Removed
#     pass

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👑 Admin Panel\nManage admins. Use inline buttons from /start or admin menu.",
                 reply_markup=create_admin_panel())

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.chat.id
        reply_func = lambda text, **kwargs: bot.reply_to(message_or_call, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply_func = lambda text, **kwargs: bot.send_message(admin_chat_id, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call.message 
    else:
        logger.error("Invalid argument for _logic_run_all_scripts")
        return

    if admin_user_id not in admin_ids:
        reply_func("⚠️ Admin permissions required.")
        return

    reply_func("⏳ Starting process to run all user scripts. This may take a while...")
    logger.info(f"Admin {admin_user_id} initiated 'run all scripts' from chat {admin_chat_id}.")

    started_count = 0; attempted_users = 0; skipped_files = 0; error_files_details = []

    # Use a copy of user_files keys and values to avoid modification issues during iteration
    all_user_files_snapshot = dict(user_files)

    for target_user_id, files_for_user in all_user_files_snapshot.items():
        if not files_for_user: continue
        attempted_users += 1
        logger.info(f"Processing scripts for user {target_user_id}...")
        user_folder = get_user_folder(target_user_id)

        for file_name, file_type in files_for_user:
            # script_owner_id for key context is target_user_id
            if not is_bot_running(target_user_id, file_name):
                user_folder, file_path = resolve_bot_paths(target_user_id, file_name)
                if os.path.exists(file_path):
                    logger.info(f"Admin {admin_user_id} attempting to start '{file_name}' ({file_type}) for user {target_user_id}.")
                    try:
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(file_path, target_user_id, user_folder, file_name, admin_message_obj_for_script_runner)).start()
                            started_count += 1
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(file_path, target_user_id, user_folder, file_name, admin_message_obj_for_script_runner)).start()
                            started_count += 1
                        else:
                            logger.warning(f"Unknown file type '{file_type}' for {file_name} (user {target_user_id}). Skipping.")
                            error_files_details.append(f"`{file_name}` (User {target_user_id}) - Unknown type")
                            skipped_files += 1
                        time.sleep(0.7) # Increased delay slightly
                    except Exception as e:
                        logger.error(f"Error queueing start for '{file_name}' (user {target_user_id}): {e}")
                        error_files_details.append(f"`{file_name}` (User {target_user_id}) - Start error")
                        skipped_files += 1
                else:
                    logger.warning(f"File '{file_name}' for user {target_user_id} not found at '{file_path}'. Skipping.")
                    error_files_details.append(f"`{file_name}` (User {target_user_id}) - File not found")
                    skipped_files += 1
            # else: logger.info(f"Script '{file_name}' for user {target_user_id} already running.")

    summary_msg = (f"✅ All Users' Scripts - Processing Complete:\n\n"
                   f"▶️ Attempted to start: {started_count} scripts.\n"
                   f"👥 Users processed: {attempted_users}.\n")
    if skipped_files > 0:
        summary_msg += f"⚠️ Skipped/Error files: {skipped_files}\n"
        if error_files_details:
             summary_msg += "Details (first 5):\n" + "\n".join([f"  - {err}" for err in error_files_details[:5]])
             if len(error_files_details) > 5: summary_msg += "\n  ... and more (check logs)."

    reply_func(summary_msg, parse_mode='Markdown')
    logger.info(f"Run all scripts finished. Admin: {admin_user_id}. Started: {started_count}. Skipped/Errors: {skipped_files}")


# --- Command Handlers & Text Handlers for ReplyKeyboard ---
@bot.message_handler(commands=['start', 'help'])
def command_send_welcome(message): _logic_send_welcome(message)

@bot.message_handler(commands=['status']) # Kept for direct command
def command_show_status(message): _logic_statistics(message) # Changed to call _logic_statistics


BUTTON_TEXT_TO_LOGIC = {
    "📤 Upload File": _logic_upload_file,
    "🛒 Buy Package": lambda message: show_packages(message.chat.id),
    "⚡ Bot Speed": _logic_bot_speed,
    "📂 Check Files": _logic_check_files,
    "📜 Package History": lambda message: show_package_history(message.chat.id),
    "📊 Statistics": _logic_statistics,
    "📞 Contact Owner": _logic_contact_owner,
    "📢 Updates Channel": _logic_updates_channel,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    # "💰 Free Mode": _logic_toggle_free_mode, # Removed
    "🟢 Running All Code": _logic_run_all_scripts,  # Added
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda message: message.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    logic_func = BUTTON_TEXT_TO_LOGIC.get(message.text)
    if logic_func: logic_func(message)
    else: logger.warning(f"Button text '{message.text}' matched but no logic func.")

@bot.message_handler(commands=['updateschannel'])
def command_updates_channel(message): _logic_updates_channel(message)
@bot.message_handler(commands=['uploadfile'])
def command_upload_file(message): _logic_upload_file(message)
@bot.message_handler(commands=['checkfiles'])
def command_check_files(message): _logic_check_files(message)
@bot.message_handler(commands=['botspeed'])
def command_bot_speed(message): _logic_bot_speed(message)
@bot.message_handler(commands=['contactowner'])
def command_contact_owner(message): _logic_contact_owner(message)
@bot.message_handler(commands=['subscriptions'])
def command_subscriptions(message): _logic_subscriptions_panel(message)
@bot.message_handler(commands=['statistics']) # Alias for /status
def command_statistics(message): _logic_statistics(message)
@bot.message_handler(commands=['broadcast'])
def command_broadcast(message): _logic_broadcast_init(message)
@bot.message_handler(commands=['lockbot']) 
def command_lock_bot(message): _logic_toggle_lock_bot(message)
# @bot.message_handler(commands=['freemode']) # Removed
# def command_free_mode(message): _logic_toggle_free_mode(message)
@bot.message_handler(commands=['adminpanel'])
def command_admin_panel(message): _logic_admin_panel(message)
@bot.message_handler(commands=['runningallcode']) # Added
def command_run_all_code(message): _logic_run_all_scripts(message)


@bot.message_handler(commands=['ping'])
def ping(message):
    start_ping_time = time.time() 
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start_ping_time) * 1000, 2)
    bot.edit_message_text(f"Pong! Latency: {latency} ms", message.chat.id, msg.message_id)


# --- Document (File) Handler ---
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message): # Renamed
    user_id = message.from_user.id
    chat_id = message.chat.id # Used for replies, script context uses user_id
    doc = message.document
    logger.info(f"Doc from {user_id}: {doc.file_name} ({doc.mime_type}), Size: {doc.file_size}")

    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return

    # Strict package gate: users cannot upload files before an approved active package.
    if not _user_can_upload(user_id):
        _upload_package_required(message)
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files via /checkfiles.")
        return

    raw_name = doc.file_name
    if not raw_name: bot.reply_to(message, "⚠️ No file name. Ensure file has a name."); return
    if raw_name.lower() == '.env' or raw_name.lower().endswith('.env'):
        bot.reply_to(message, "⚠️ .env upload is not available from this bot menu. Configure required variables in your hosting settings.")
        return
    file_name = sanitize_file_name(raw_name)
    file_ext = os.path.splitext(file_name)[1].lower()
    now_ts = time.time()
    if user_id not in admin_ids and now_ts - _last_upload.get(user_id, 0) < UPLOAD_COOLDOWN:
        bot.reply_to(message, "⏳ Slow down - wait a few seconds between uploads."); return
    _last_upload[user_id] = now_ts
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Unsupported type! Only `.py`, `.js`, `.zip` allowed.")
        return
    max_file_size = 20 * 1024 * 1024 # 20 MB
    if doc.file_size > max_file_size:
        bot.reply_to(message, f"⚠️ File too large (Max: {max_file_size // 1024 // 1024} MB)."); return

    try:
        try:
            bot.forward_message(OWNER_ID, chat_id, message.message_id)
            bot.send_message(OWNER_ID, f"⬆️ File '{file_name}' from {message.from_user.first_name} (`{user_id}`)", parse_mode='Markdown')
        except Exception as e: logger.error(f"Failed to forward uploaded file to OWNER_ID {OWNER_ID}: {e}")

        download_wait_msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...")
        file_info_tg_doc = bot.get_file(doc.file_id) # Renamed
        downloaded_file_content = bot.download_file(file_info_tg_doc.file_path)
        bot.edit_message_text(f"✅ Downloaded `{file_name}`. Processing...", chat_id, download_wait_msg.message_id)
        logger.info(f"Downloaded {file_name} for user {user_id}")
        if file_ext == '.zip':
            handle_zip_file(downloaded_file_content, file_name, message)
        else:
            # Own private folder per hosted bot => own database / .env / logs
            bot_name = unique_bot_name(user_id, file_name)
            work_dir = get_instance_dir(user_id, bot_name, create=True)
            file_path = os.path.join(work_dir, bot_name)
            with open(file_path, 'wb') as f: f.write(downloaded_file_content)
            logger.info(f"Saved single file to {file_path}")
            if bot_name != file_name:
                bot.reply_to(message, f"ℹ️ `{file_name}` already exists - hosting this one as a separate instance `{bot_name}` (own folder & database).", parse_mode='Markdown')
            if file_ext == '.js': handle_js_file(file_path, user_id, work_dir, bot_name, message)
            elif file_ext == '.py': handle_py_file(file_path, user_id, work_dir, bot_name, message)
    except telebot.apihelper.ApiTelegramException as e:
         logger.error(f"Telegram API Error handling file for {user_id}: {e}", exc_info=True)
         if "file is too big" in str(e).lower():
              bot.reply_to(message, f"❌ Telegram API Error: File too large to download (~20MB limit).")
         else: bot.reply_to(message, f"❌ Telegram API Error: {str(e)}. Try later.")
    except Exception as e:
        logger.error(f"❌ General error handling file for {user_id}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Unexpected error: {str(e)}")
# --- End Document Handler ---


# --- Callback Query Handlers (for Inline Buttons) ---
def handle_generic_cancel(call):
    """Cancel the current next-step input (when present) and close the prompt."""
    try:
        bot.clear_step_handler_by_chat_id(call.message.chat.id)
    except Exception as exc:
        logger.debug(f"No pending step handler to clear: {exc}")
    bot.answer_callback_query(call.id, "Cancelled")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    try:
        bot.send_message(call.message.chat.id, "❌ Cancelled.")
    except Exception:
        pass

# --- Callback Query Handlers (for Inline Buttons) ---
@bot.callback_query_handler(func=lambda call: True) 
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    logger.info(f"Callback: User={user_id}, Data='{data}'")

    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats', 'cancel_action']: # Allow stats
        bot.answer_callback_query(call.id, "⚠️ Bot locked by admin.", show_alert=True)
        return
    try:
        if data == 'upload': upload_callback(call)
        elif data == 'check_files': check_files_callback(call)
        elif data.startswith('file_'): file_control_callback(call)
        elif data.startswith('start_'): start_bot_callback(call)
        elif data.startswith('stop_'): stop_bot_callback(call)
        elif data.startswith('restart_'): restart_bot_callback(call)
        elif data.startswith('delete_'): delete_bot_callback(call)
        elif data.startswith('logs_'): logs_bot_callback(call)
        elif data.startswith('confirmdel_'): confirm_delete_callback(call)
        elif data == 'speed': speed_callback(call)
        elif data == 'buy_package': show_packages_callback(call)
        elif data.startswith('pkg_'): package_selected_callback(call)
        elif data.startswith('paymethod_'): payment_method_selected_callback(call)
        elif data.startswith('payapprove_'): admin_payment_decision(call, True)
        elif data.startswith('paycancel_'): admin_payment_decision(call, False)
        elif data == 'payments_admin': admin_required_callback(call, show_pending_payments)
        elif data == 'packages_admin': admin_required_callback(call, admin_packages_menu)
        elif data == 'package_add': admin_required_callback(call, begin_add_package)
        elif data == 'methods_admin': admin_required_callback(call, payment_methods_admin_menu)
        elif data == 'payment_add_method': admin_required_callback(call, begin_add_payment_method)
        elif data == 'payment_set_number': admin_required_callback(call, begin_set_payment_number)
        elif data == 'payment_delete_method': admin_required_callback(call, begin_delete_payment_method)
        elif data.startswith('pmset_'): admin_required_callback(call, select_payment_number_method)
        elif data.startswith('pmdel_'): admin_required_callback(call, delete_payment_method_callback)
        elif data == 'users_admin': admin_required_callback(call, show_hosted_users)
        elif data.startswith('adminuser_'): admin_required_callback(call, show_user_bots_callback)
        elif data.startswith('pkgdel_'): admin_required_callback(call, delete_package_callback)
        elif data == 'back_to_main': back_to_main_callback(call)
        elif data.startswith('confirm_broadcast_'): handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast': handle_cancel_broadcast(call)
        elif data == 'cancel_action': handle_generic_cancel(call)
        elif data == 'adminpkg_delete_user': admin_required_callback(call, begin_delete_user_package)
        elif data.startswith('userpkgdel_'): admin_required_callback(call, confirm_delete_user_package)
        elif data.startswith('userpkgconfirm_'): admin_required_callback(call, execute_delete_user_package)
        # --- Admin Callbacks ---
        elif data == 'subscription': admin_required_callback(call, subscription_management_callback)
        elif data == 'stats': stats_callback(call) # No admin check here, handled in func
        elif data == 'lock_bot': admin_required_callback(call, lock_bot_callback)
        elif data == 'unlock_bot': admin_required_callback(call, unlock_bot_callback)
        # elif data == 'free_mode': admin_required_callback(call, toggle_free_mode_callback) # Removed
        elif data == 'run_all_scripts': admin_required_callback(call, run_all_scripts_callback) # Added
        elif data == 'broadcast': admin_required_callback(call, broadcast_init_callback) 
        elif data == 'admin_panel': admin_required_callback(call, admin_panel_callback)
        elif data == 'add_admin': owner_required_callback(call, add_admin_init_callback) 
        elif data == 'remove_admin': owner_required_callback(call, remove_admin_init_callback) 
        elif data == 'list_admins': admin_required_callback(call, list_admins_callback)
        elif data == 'add_subscription': admin_required_callback(call, add_subscription_init_callback) 
        elif data == 'remove_subscription': admin_required_callback(call, remove_subscription_init_callback) 
        elif data == 'check_subscription': admin_required_callback(call, check_subscription_init_callback) 
        else:
            bot.answer_callback_query(call.id, "Unknown action.")
            logger.warning(f"Unhandled callback data: {data} from user {user_id}")
    except Exception as e:
        logger.error(f"Error handling callback '{data}' for {user_id}: {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Error processing request.", show_alert=True)
        except Exception as e_ans: logger.error(f"Failed to answer callback after error: {e_ans}")

def admin_required_callback(call, func_to_run):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    func_to_run(call) 

def owner_required_callback(call, func_to_run):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner permissions required.", show_alert=True)
        return
    func_to_run(call)

def upload_callback(call):
    user_id = call.from_user.id
    if not _user_can_upload(user_id):
        bot.answer_callback_query(call.id, "আগে প্যাকেজ কিনে Admin approval নিন।", show_alert=True)
        return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.answer_callback_query(call.id, f"⚠️ File limit ({current_files}/{limit_str}) reached.", show_alert=True)
        return
    bot.answer_callback_query(call.id) 
    bot.send_message(call.message.chat.id, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.")

def check_files_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id 
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.answer_callback_query(call.id, "⚠️ No files uploaded.", show_alert=True)
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data='cancel_action'))
            bot.edit_message_text("📂 Your files:\n\n(No files uploaded)", chat_id, call.message.message_id, reply_markup=markup)
        except Exception as e: logger.error(f"Error editing msg for empty file list: {e}")
        return
    bot.answer_callback_query(call.id) 
    markup = types.InlineKeyboardMarkup(row_width=1) 
    for file_name, file_type in sorted(user_files_list): 
        is_running = is_bot_running(user_id, file_name) # Use user_id for status check
        status_icon = "🟢 Running" if is_running else "🔴 Stopped"
        btn_text = f"{file_name} ({file_type}) - {status_icon}"
        # Callback includes user_id as script_owner_id
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data='cancel_action'))
    try:
        bot.edit_message_text("📂 Your files:\nClick to manage.", chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
         if "message is not modified" in str(e): logger.warning("Msg not modified (files).")
         else: logger.error(f"Error editing msg for file list: {e}")
    except Exception as e: logger.error(f"Unexpected error editing msg for file list: {e}", exc_info=True)

def file_control_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id

        # Allow owner/admin to control any file, or user to control their own
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            logger.warning(f"User {requesting_user_id} tried to access file '{file_name}' of user {script_owner_id} without permission.")
            bot.answer_callback_query(call.id, "⚠️ You can only manage your own files.", show_alert=True)
            check_files_callback(call) # Show their own files
            return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            logger.warning(f"File '{file_name}' not found for user {script_owner_id} during control.")
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            # If admin was viewing, this might be confusing. For now, just show their own.
            check_files_callback(call) 
            return

        bot.answer_callback_query(call.id) 
        is_running = is_bot_running(script_owner_id, file_name)
        status_text = '🟢 Running' if is_running else '🔴 Stopped'
        file_type = next((f[1] for f in user_files_list if f[0] == file_name), '?') 
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}"
                + bot_extra_info(script_owner_id, file_name),
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_running),
                parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified (controls for {file_name})")
             else: raise 
    except (ValueError, IndexError) as ve:
        logger.error(f"Error parsing file control callback: {ve}. Data: '{call.data}'")
        bot.answer_callback_query(call.id, "Error: Invalid action data.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in file_control_callback for data '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "An error occurred.", show_alert=True)

def start_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id # Where the admin/user gets the reply

        logger.info(f"Start request: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")

        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied to start this script.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]
        user_folder, file_path = resolve_bot_paths(script_owner_id, file_name)

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ Error: File `{file_name}` missing! Re-upload.", show_alert=True)
            remove_user_file_db(script_owner_id, file_name); check_files_callback(call); return

        if is_bot_running(script_owner_id, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Script '{file_name}' already running.", show_alert=True)
            try: bot.edit_message_reply_markup(chat_id_for_reply, call.message.message_id, reply_markup=create_control_buttons(script_owner_id, file_name, True))
            except Exception as e: logger.error(f"Error updating buttons (already running): {e}")
            return

        bot.answer_callback_query(call.id, f"⏳ Attempting to start {file_name} for user {script_owner_id}...")

        # Pass call.message as message_obj_for_reply so feedback goes to the person who clicked
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        else:
             bot.send_message(chat_id_for_reply, f"❌ Error: Unknown file type '{file_type}' for '{file_name}'."); return 

        time.sleep(1.5) # Give script time to actually start or fail early
        is_now_running = is_bot_running(script_owner_id, file_name) 
        status_text = '🟢 Running' if is_now_running else '🟡 Starting (or failed, check logs/replies)'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified after starting {file_name}")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing start callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid start command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in start_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error starting script.", show_alert=True)
        try: # Attempt to reset buttons to 'stopped' state on error
            _, script_owner_id_err_str, file_name_err = call.data.split('_', 2)
            script_owner_id_err = int(script_owner_id_err_str)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(script_owner_id_err, file_name_err, False))
        except Exception as e_btn: logger.error(f"Failed to update buttons after start error: {e_btn}")

def stop_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Stop request: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1] 
        script_key = f"{script_owner_id}_{file_name}"

        if not is_bot_running(script_owner_id, file_name): 
            bot.answer_callback_query(call.id, f"⚠️ Script '{file_name}' already stopped.", show_alert=True)
            try:
                 bot.edit_message_text(
                     f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                     chat_id_for_reply, call.message.message_id,
                     reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown')
            except Exception as e: logger.error(f"Error updating buttons (already stopped): {e}")
            return

        bot.answer_callback_query(call.id, f"⏳ Stopping {file_name} for user {script_owner_id}...")
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]; logger.info(f"Removed {script_key} from running after stop.")
        else: logger.warning(f"Script {script_key} running by psutil but not in bot_scripts dict.")

        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, False), parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified after stopping {file_name}")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing stop callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid stop command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in stop_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error stopping script.", show_alert=True)

def restart_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Restart: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        file_type = file_info[1]
        user_folder, file_path = resolve_bot_paths(script_owner_id, file_name); script_key = f"{script_owner_id}_{file_name}"

        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ Error: File `{file_name}` missing! Re-upload.", show_alert=True)
            remove_user_file_db(script_owner_id, file_name)
            if script_key in bot_scripts: del bot_scripts[script_key]
            check_files_callback(call); return

        bot.answer_callback_query(call.id, f"⏳ Restarting {file_name} for user {script_owner_id}...")
        if is_bot_running(script_owner_id, file_name):
            logger.info(f"Restart: Stopping existing {script_key}...")
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(1.5) 

        logger.info(f"Restart: Starting script {script_key}...")
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, user_folder, file_name, call.message)).start()
        else:
             bot.send_message(chat_id_for_reply, f"❌ Unknown type '{file_type}' for '{file_name}'."); return

        time.sleep(1.5) 
        is_now_running = is_bot_running(script_owner_id, file_name) 
        status_text = '🟢 Running' if is_now_running else '🟡 Starting (or failed)'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, file_name, is_now_running), parse_mode='Markdown'
            )
        except telebot.apihelper.ApiTelegramException as e:
             if "message is not modified" in str(e): logger.warning(f"Msg not modified (restart {file_name})")
             else: raise
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing restart callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid restart command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in restart_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error restarting.", show_alert=True)
        try:
            _, script_owner_id_err_str, file_name_err = call.data.split('_', 2)
            script_owner_id_err = int(script_owner_id_err_str)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(script_owner_id_err, file_name_err, False))
        except Exception as e_btn: logger.error(f"Failed to update buttons after restart error: {e_btn}")


def _check_bot_access(call):
    """Shared permission check for per-bot buttons. Returns (owner_id, file_name) or None."""
    _, owner_str, file_name = call.data.split('_', 2)
    owner_id = int(owner_str)
    if not (call.from_user.id == owner_id or call.from_user.id in admin_ids):
        bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return None
    if not any(f[0] == file_name for f in user_files.get(owner_id, [])):
        bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return None
    return owner_id, file_name

def delete_bot_callback(call):
    """Step 1: ask for delete confirmation."""
    try:
        access = _check_bot_access(call)
        if not access: return
        owner_id, file_name = access
        bot.answer_callback_query(call.id)
        if is_legacy_bot(owner_id, file_name):
            scope = "the script and its log (legacy bot: other files in the shared folder are kept)"
        else:
            scope = "the script and its associated files (the whole bot folder)"
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🗑️ Yes, delete permanently", callback_data=f'confirmdel_{owner_id}_{file_name}'))
        markup.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f'file_{owner_id}_{file_name}'))
        bot.edit_message_text(f"⚠️ Delete `{file_name}`?\nThis removes {scope}. It cannot be undone.",
                              call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing delete callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid delete command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in delete_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error deleting.", show_alert=True)

def confirm_delete_callback(call):
    """Step 2: really delete. The whole bot folder (incl. its database) is removed, so a re-host starts clean."""
    try:
        access = _check_bot_access(call)
        if not access: return
        script_owner_id, file_name = access
        chat_id_for_reply = call.message.chat.id
        logger.info(f"Delete confirmed: Requester={call.from_user.id}, Owner={script_owner_id}, File='{file_name}'")

        bot.answer_callback_query(call.id, f"🗑️ Deleting {file_name}...")
        script_key = f"{script_owner_id}_{file_name}"
        if is_bot_running(script_owner_id, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(1.0)
        elif script_key in bot_scripts:
            del bot_scripts[script_key]

        work_dir, file_path = resolve_bot_paths(script_owner_id, file_name)
        legacy = is_legacy_bot(script_owner_id, file_name)
        deleted_disk = []
        if legacy:
            log_path = os.path.join(work_dir, f"{os.path.splitext(file_name)[0]}.log")
            for p in (file_path, log_path):
                if os.path.exists(p):
                    try: os.remove(p); deleted_disk.append(os.path.basename(p))
                    except OSError as e: logger.error(f"Error deleting {p}: {e}")
            env_dir = get_instance_dir(script_owner_id, file_name) # holds only the bot's .env
            if os.path.isdir(env_dir) and rmtree_force(env_dir): deleted_disk.append('.env')
        else:
            if os.path.isdir(work_dir):
                if rmtree_force(work_dir): deleted_disk.append('bot folder + database')
                else: logger.error(f"Could not fully remove {work_dir}")

        remove_user_file_db(script_owner_id, file_name)
        deleted_str = ", ".join(f"`{f}`" for f in deleted_disk) if deleted_disk else "associated files"
        try:
            bot.edit_message_text(f"🗑️ `{file_name}` (User `{script_owner_id}`) deleted: {deleted_str}",
                                  chat_id_for_reply, call.message.message_id, reply_markup=None, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error editing msg after delete: {e}")
            bot.send_message(chat_id_for_reply, f"🗑️ `{file_name}` deleted.", parse_mode='Markdown')
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing confirmdel callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid delete command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in confirm_delete_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error deleting.", show_alert=True)

def db_bot_callback(call):
    """Download the database file(s) of ONE bot."""
    try:
        access = _check_bot_access(call)
        if not access: return
        owner_id, file_name = access
        work_dir, _ = resolve_bot_paths(owner_id, file_name)
        db_files = find_bot_db_files(work_dir)
        if not db_files:
            bot.answer_callback_query(call.id, "ℹ️ No database files found for this bot yet.", show_alert=True); return
        bot.answer_callback_query(call.id, "📥 Preparing database...")
        threading.Thread(target=send_bot_databases, args=(call.message.chat.id, work_dir, db_files, file_name)).start()
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing db callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in db_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error exporting database.", show_alert=True)

def env_bot_callback(call):
    """Env menu of ONE hosted bot: add / remove variables, or upload a .env file."""
    try:
        access = _check_bot_access(call)
        if not access: return
        owner_id, file_name = access
        bot.answer_callback_query(call.id)
        keys = list(read_bot_env(get_instance_dir(owner_id, file_name)).keys())
        current = ", ".join(f"`{k}`" for k in keys) if keys else "(none yet)"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.row(types.InlineKeyboardButton("➕ Add Variable", callback_data=f'envadd_{owner_id}_{file_name}'),
                   types.InlineKeyboardButton("➖ Remove Variable", callback_data=f'envrm_{owner_id}_{file_name}'))
        markup.row(types.InlineKeyboardButton("📄 Upload .env / Paste", callback_data=f'envup_{owner_id}_{file_name}'))
        markup.row(types.InlineKeyboardButton("🔙 Back", callback_data=f'file_{owner_id}_{file_name}'))
        markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data='cancel_action'))
        bot.send_message(call.message.chat.id,
            f"🔑 *Env variables for* `{file_name}`\nKeys: {current}\n\n"
            "Your script reads them with `os.getenv('NAME')` (Python) or `process.env.NAME` (JS).\n"
            "Values are never shown again.", reply_markup=markup, parse_mode='Markdown')
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing env callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in env_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error.", show_alert=True)

def envadd_callback(call):
    access = _check_bot_access(call)
    if not access: return
    owner_id, file_name = access
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "➕ Send the variable *NAME* (example: `BOT_TOKEN`).\nUse Cancel below to abort.", parse_mode='Markdown', reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_env_key, owner_id, file_name)

def process_env_key(message, owner_id, file_name):
    if not (message.from_user.id == owner_id or message.from_user.id in admin_ids): return
    if message.content_type != 'text': bot.reply_to(message, "⚠️ Send the name as text."); return
    key = message.text.strip()
    if key.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key):
        msg = bot.reply_to(message, "⚠️ Invalid name. Use letters, digits, `_` (e.g. `BOT_TOKEN`). Try again or press Cancel.", parse_mode='Markdown', reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_env_key, owner_id, file_name); return
    msg = bot.reply_to(message, f"✏️ Now send the *VALUE* for `{key}`.\n🔒 Your message will be deleted after saving. Use Cancel below to abort.", parse_mode='Markdown', reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_env_value, owner_id, file_name, key)

def process_env_value(message, owner_id, file_name, key):
    if not (message.from_user.id == owner_id or message.from_user.id in admin_ids): return
    if message.content_type != 'text': bot.reply_to(message, "⚠️ Send the value as text."); return
    value = message.text.strip()
    if value.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    if not value or len(value) > 4096:
        msg = bot.reply_to(message, "⚠️ Value is empty or too long. Send again or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_env_value, owner_id, file_name, key); return
    try:
        env_dir = get_instance_dir(owner_id, file_name)
        current = read_bot_env(env_dir)
        if key not in current and len(current) >= 50: bot.reply_to(message, "⚠️ Max 50 variables."); return
        current[key] = value; write_bot_env(env_dir, current)
        try: bot.delete_message(message.chat.id, message.message_id)
        except Exception: pass
        running = is_bot_running(owner_id, file_name)
        note = "🔄 Restart the bot to apply." if running else "▶️ Press Start to run."
        bot.send_message(message.chat.id, f"✅ `{key}` saved.\n{note}", reply_markup=create_control_buttons(owner_id, file_name, running), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error saving env value: {e}", exc_info=True); bot.reply_to(message, "❌ Could not save.")

def envrm_callback(call):
    access = _check_bot_access(call)
    if not access: return
    owner_id, file_name = access
    keys = list(read_bot_env(get_instance_dir(owner_id, file_name)).keys())
    if not keys: bot.answer_callback_query(call.id, "No variables to remove.", show_alert=True); return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "➖ Send the NAME to remove:\n" + ", ".join(f"`{k}`" for k in keys) + "\nUse Cancel below to abort.", parse_mode='Markdown', reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_env_remove, owner_id, file_name)

def process_env_remove(message, owner_id, file_name):
    if not (message.from_user.id == owner_id or message.from_user.id in admin_ids): return
    if message.content_type != 'text': bot.reply_to(message, "⚠️ Send the name as text."); return
    key = message.text.strip()
    if key.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    env_dir = get_instance_dir(owner_id, file_name); current = read_bot_env(env_dir)
    if key not in current: bot.reply_to(message, f"⚠️ `{key}` not found.", parse_mode='Markdown'); return
    del current[key]; write_bot_env(env_dir, current)
    running = is_bot_running(owner_id, file_name)
    bot.reply_to(message, f"✅ `{key}` removed." + (" 🔄 Restart to apply." if running else ""),
                 reply_markup=create_control_buttons(owner_id, file_name, running), parse_mode='Markdown')

def envup_callback(call):
    access = _check_bot_access(call)
    if not access: return
    owner_id, file_name = access
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        "📄 Send a `.env` file, or paste lines like `NAME=value`.\n🔒 Your message is deleted after saving. /cancel to abort.", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_env_input, owner_id, file_name)

def process_env_input(message, owner_id, file_name):
    requester = message.from_user.id
    if not (requester == owner_id or requester in admin_ids): return
    if not any(f[0] == file_name for f in user_files.get(owner_id, [])):
        bot.reply_to(message, "⚠️ That bot no longer exists."); return
    retry = lambda text: (bot.reply_to(message, text),
                          bot.register_next_step_handler(message, process_env_input, owner_id, file_name))
    try:
        raw_text = None
        if message.content_type == 'document':
            doc = message.document; name = (doc.file_name or '').lower()
            if not (name == '.env' or name.endswith('.env') or name.endswith('.txt')):
                retry("⚠️ Please send a `.env` file, KEY=VALUE text, or the bot token. /cancel to abort."); return
            if doc.file_size and doc.file_size > MAX_ENV_BYTES:
                retry("⚠️ File too large for an env file (max 64 KB)."); return
            raw_text = bot.download_file(bot.get_file(doc.file_id).file_path).decode('utf-8', errors='ignore')
        elif message.content_type == 'text':
            if message.text.strip().lower() == '/cancel': bot.reply_to(message, "Env update cancelled."); return
            raw_text = message.text
        else:
            retry("⚠️ Send a `.env` file or text. /cancel to abort."); return

        new_vars = _parse_env_text(raw_text)
        if not new_vars and message.content_type == 'text' and TOKEN_PATTERN.fullmatch(raw_text.strip()):
            new_vars = {'BOT_TOKEN': raw_text.strip()}
        if not new_vars:
            retry("⚠️ No valid KEY=VALUE lines found. Example: `BOT_TOKEN=123456:ABC...`  (/cancel to abort)"); return
        if len(new_vars) > 50 or any(len(v) > 4096 for v in new_vars.values()):
            retry("⚠️ Too many variables or a value is too long."); return

        env_dir = get_instance_dir(owner_id, file_name)
        current = read_bot_env(env_dir); current.update(new_vars)
        write_bot_env(env_dir, current)
        logger.info(f"Env updated for {owner_id}/{file_name}: keys={list(new_vars.keys())}")

        try: bot.delete_message(message.chat.id, message.message_id) # remove the secret from the chat
        except Exception: pass
        running = is_bot_running(owner_id, file_name)
        note = "🔄 Restart the bot to apply." if running else "▶️ Press Start to run with the new settings."
        saved = ", ".join(f"`{k}`" for k in new_vars)
        bot.send_message(message.chat.id, f"✅ Saved {len(new_vars)} variable(s): {saved}\n(your message was deleted)\n{note}",
                         reply_markup=create_control_buttons(owner_id, file_name, running), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error processing env input: {e}", exc_info=True)
        bot.reply_to(message, "❌ Could not save env settings.")

def logs_bot_callback(call):
    try:
        _, script_owner_id_str, file_name = call.data.split('_', 2)
        script_owner_id = int(script_owner_id_str)
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id

        logger.info(f"Logs: Requester={requesting_user_id}, Owner={script_owner_id}, File='{file_name}'")
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return

        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return

        user_folder, _script_path = resolve_bot_paths(script_owner_id, file_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, f"⚠️ No logs for '{file_name}'.", show_alert=True); return

        bot.answer_callback_query(call.id) 
        try:
            log_content = ""; file_size = os.path.getsize(log_path)
            max_log_kb = 100; max_tg_msg = 4096
            if file_size == 0: log_content = "(Log empty)"
            elif file_size > max_log_kb * 1024:
                 with open(log_path, 'rb') as f: f.seek(-max_log_kb * 1024, os.SEEK_END); log_bytes = f.read()
                 log_content = log_bytes.decode('utf-8', errors='ignore')
                 log_content = f"(Last {max_log_kb} KB)\n...\n" + log_content
            else:
                 with open(log_path, 'r', encoding='utf-8', errors='ignore') as f: log_content = f.read()

            if len(log_content) > max_tg_msg:
                log_content = log_content[-max_tg_msg:]
                first_nl = log_content.find('\n')
                if first_nl != -1: log_content = "...\n" + log_content[first_nl+1:]
                else: log_content = "...\n" + log_content 
            if not log_content.strip(): log_content = "(No visible content)"
            log_content = TOKEN_PATTERN.sub('<redacted-token>', log_content) # never show tokens in chat

            bot.send_message(chat_id_for_reply, f"📜 Logs for `{file_name}` (User `{script_owner_id}`):\n```\n{log_content}\n```", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error reading/sending log {log_path}: {e}", exc_info=True)
            bot.send_message(chat_id_for_reply, f"❌ Error reading log for `{file_name}`.")
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing logs callback '{call.data}': {e}")
        bot.answer_callback_query(call.id, "Error: Invalid logs command.", show_alert=True)
    except Exception as e:
        logger.error(f"Error in logs_bot_callback for '{call.data}': {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error fetching logs.", show_alert=True)

def speed_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    start_cb_ping_time = time.time() 
    try:
        bot.edit_message_text("🏃 Testing speed...", chat_id, call.message.message_id)
        bot.send_chat_action(chat_id, 'typing') 
        response_time = round((time.time() - start_cb_ping_time) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        # mode = "💰 Free Mode: ON" if free_mode else "💸 Free Mode: OFF" # Removed
        if user_id == OWNER_ID: user_level = "👑 Owner"
        elif user_id in admin_ids: user_level = "🛡️ Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "⭐ Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n"
                     # f"模式 Mode: {mode}\n" # Removed
                     f"👤 Your Level: {active_package_for_user(user_id)['name'] if active_package_for_user(user_id) else ('🆓 No package' if user_level == '🆓 Free User' else user_level)}")
        bot.answer_callback_query(call.id) 
        bot.edit_message_text(speed_msg, chat_id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
    except Exception as e:
         logger.error(f"Error during speed test (cb): {e}", exc_info=True)
         bot.answer_callback_query(call.id, "Error in speed test.", show_alert=True)
         try: bot.edit_message_text("〽️ Main Menu", chat_id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
         except Exception: pass

def back_to_main_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    if user_id == OWNER_ID: user_status = "👑 Owner"
    elif user_id in admin_ids: user_status = "🛡️ Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"; days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Subscription expires in: {days_left} days"
        else: user_status = "🆓 Free User (Expired Sub)" # Will be cleaned up by welcome if not already
    else: user_status = "🆓 Free User"
    main_menu_text = (f"〽️ Welcome back, {call.from_user.first_name}!\n\n🆔 ID: `{user_id}`\n"
                      f"🔰 Status: {user_status}{expiry_info}\n📁 Files: {current_files} / {limit_str}\n\n"
                      f"👇 Use buttons or type commands.")
    try:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(main_menu_text, chat_id, call.message.message_id,
                              reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
         if "message is not modified" in str(e): logger.warning("Msg not modified (back_to_main).")
         else: logger.error(f"API error on back_to_main: {e}")
    except Exception as e: logger.error(f"Error handling back_to_main: {e}", exc_info=True)

# --- Admin Callback Implementations (for Inline Buttons) ---
def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("💳 Subscription Management\nSelect action:",
                              call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu())
    except Exception as e: logger.error(f"Error showing sub menu: {e}")

def stats_callback(call): # Called by user and admin
    bot.answer_callback_query(call.id)
    # The logic is now inside _logic_statistics which determines what to show based on user_id
    # We need to pass a message-like object to _logic_statistics
    # For callbacks, call.message can be used.
    _logic_statistics(call.message) 
    # To update the inline keyboard after showing stats, we need to edit the message
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception as e:
        logger.error(f"Error updating menu after stats_callback: {e}")


def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    logger.warning(f"Bot locked by Admin {call.from_user.id}")
    bot.answer_callback_query(call.id, "🔒 Bot locked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception as e: logger.error(f"Error updating menu (lock): {e}")

def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    logger.warning(f"Bot unlocked by Admin {call.from_user.id}")
    bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception as e: logger.error(f"Error updating menu (unlock): {e}")

# def toggle_free_mode_callback(call): # Removed
#     pass

def run_all_scripts_callback(call): # Added
    _logic_run_all_scripts(call) # Pass the call object


def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast.\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text and message.text.lower() == '/cancel': bot.reply_to(message, "Broadcast cancelled."); return

    broadcast_content = message.text # Can also handle photos, videos etc. if message.content_type is checked
    if not broadcast_content and not (message.photo or message.video or message.document or message.sticker or message.voice or message.audio): # If no text and no other media
         bot.reply_to(message, "⚠️ Cannot broadcast empty message. Send text or media, or /cancel.")
         msg = bot.send_message(message.chat.id, "📢 Send broadcast message or press Cancel.", reply_markup=cancel_markup())
         bot.register_next_step_handler(msg, process_broadcast_message)
         return

    target_count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Confirm & Send", callback_data=f"confirm_broadcast_{message.message_id}"),
               types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"))

    preview_text = broadcast_content[:1000].strip() if broadcast_content else "(Media message)"
    bot.reply_to(message, f"⚠️ Confirm Broadcast:\n\n```\n{preview_text}\n```\n" 
                          f"To **{target_count}** users. Sure?", reply_markup=markup, parse_mode='Markdown')

def handle_confirm_broadcast(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id not in admin_ids: bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True); return
    try:
        original_message = call.message.reply_to_message
        if not original_message: raise ValueError("Could not retrieve original message.")

        # Check content type and get content
        broadcast_text = None
        broadcast_photo_id = None
        broadcast_video_id = None
        # Add other types as needed: document, sticker, voice, audio

        if original_message.text:
            broadcast_text = original_message.text
        elif original_message.photo:
            broadcast_photo_id = original_message.photo[-1].file_id # Get highest quality
        elif original_message.video:
            broadcast_video_id = original_message.video.file_id
        # Add more elif for other content types
        else:
            raise ValueError("Message has no text or supported media for broadcast.")

        bot.answer_callback_query(call.id, "🚀 Starting broadcast...")
        bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users...",
                              chat_id, call.message.message_id, reply_markup=None)
        # Pass all potential content types to execute_broadcast
        thread = threading.Thread(target=execute_broadcast, args=(
            broadcast_text, broadcast_photo_id, broadcast_video_id, 
            original_message.caption if (broadcast_photo_id or broadcast_video_id) else None, # Pass caption
            chat_id))
        thread.start()
    except ValueError as ve: 
        logger.error(f"Error retrieving msg for broadcast confirm: {ve}")
        bot.edit_message_text(f"❌ Error starting broadcast: {ve}", chat_id, call.message.message_id, reply_markup=None)
    except Exception as e:
        logger.error(f"Error in handle_confirm_broadcast: {e}", exc_info=True)
        bot.edit_message_text("❌ Unexpected error during broadcast confirm.", chat_id, call.message.message_id, reply_markup=None)

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Broadcast cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    # Optionally delete the original message too if call.message.reply_to_message exists
    if call.message.reply_to_message:
        try: bot.delete_message(call.message.chat.id, call.message.reply_to_message.message_id)
        except: pass


def execute_broadcast(broadcast_text, photo_id, video_id, caption, admin_chat_id):
    sent_count = 0; failed_count = 0; blocked_count = 0
    start_exec_time = time.time() 
    users_to_broadcast = list(active_users); total_users = len(users_to_broadcast)
    logger.info(f"Executing broadcast to {total_users} users.")
    batch_size = 25; delay_batches = 1.5

    for i, user_id_bc in enumerate(users_to_broadcast): # Renamed
        try:
            if broadcast_text:
                bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
            elif photo_id:
                bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
            elif video_id:
                bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
            # Add other send methods for other types
            sent_count += 1
        except telebot.apihelper.ApiTelegramException as e:
            err_desc = str(e).lower()
            if any(s in err_desc for s in ["bot was blocked", "user is deactivated", "chat not found", "kicked from", "restricted"]): 
                logger.warning(f"Broadcast failed to {user_id_bc}: User blocked/inactive.")
                blocked_count += 1
            elif "flood control" in err_desc or "too many requests" in err_desc:
                retry_after = 5; match = re.search(r"retry after (\d+)", err_desc)
                if match: retry_after = int(match.group(1)) + 1 
                logger.warning(f"Flood control. Sleeping {retry_after}s...")
                time.sleep(retry_after)
                try: # Retry once
                    if broadcast_text: bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
                    elif photo_id: bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
                    elif video_id: bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
                    sent_count += 1
                except Exception as e_retry: logger.error(f"Broadcast retry failed to {user_id_bc}: {e_retry}"); failed_count +=1
            else: logger.error(f"Broadcast failed to {user_id_bc}: {e}"); failed_count += 1
        except Exception as e: logger.error(f"Unexpected error broadcasting to {user_id_bc}: {e}"); failed_count += 1

        if (i + 1) % batch_size == 0 and i < total_users - 1:
            logger.info(f"Broadcast batch {i//batch_size + 1} sent. Sleeping {delay_batches}s...")
            time.sleep(delay_batches)
        elif i % 5 == 0: time.sleep(0.2) 

    duration = round(time.time() - start_exec_time, 2)
    result_msg = (f"📢 Broadcast Complete!\n\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}\n"
                  f"🚫 Blocked/Inactive: {blocked_count}\n👥 Targets: {total_users}\n⏱️ Duration: {duration}s")
    logger.info(result_msg)
    try: bot.send_message(admin_chat_id, result_msg)
    except Exception as e: logger.error(f"Failed to send broadcast result to admin {admin_chat_id}: {e}")

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👑 Admin Panel\nManage admins (Owner actions may be restricted).",
                              call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
    except Exception as e: logger.error(f"Error showing admin panel: {e}")

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to promote to Admin.\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_add_admin_id)

def process_add_admin_id(message):
    owner_id_check = message.from_user.id 
    if owner_id_check != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Admin promotion cancelled."); return
    try:
        new_admin_id = int(message.text.strip())
        if new_admin_id <= 0: raise ValueError("ID must be positive")
        if new_admin_id == OWNER_ID: bot.reply_to(message, "⚠️ Owner is already Owner."); return
        if new_admin_id in admin_ids: bot.reply_to(message, f"⚠️ User `{new_admin_id}` already Admin."); return
        add_admin_db(new_admin_id) 
        logger.warning(f"Admin {new_admin_id} added by Owner {owner_id_check}.")
        bot.reply_to(message, f"✅ User `{new_admin_id}` promoted to Admin.")
        try: bot.send_message(new_admin_id, "🎉 Congrats! You are now an Admin.")
        except Exception as e: logger.error(f"Failed to notify new admin {new_admin_id}: {e}")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter User ID to promote or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_add_admin_id)
    except Exception as e: logger.error(f"Error processing add admin: {e}", exc_info=True); bot.reply_to(message, "Error.")

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID of Admin to remove.\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_remove_admin_id)

def process_remove_admin_id(message):
    owner_id_check = message.from_user.id
    if owner_id_check != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Admin removal cancelled."); return
    try:
        admin_id_remove = int(message.text.strip()) # Renamed
        if admin_id_remove <= 0: raise ValueError("ID must be positive")
        if admin_id_remove == OWNER_ID: bot.reply_to(message, "⚠️ Owner cannot remove self."); return
        if admin_id_remove not in admin_ids: bot.reply_to(message, f"⚠️ User `{admin_id_remove}` not Admin."); return
        if remove_admin_db(admin_id_remove): 
            logger.warning(f"Admin {admin_id_remove} removed by Owner {owner_id_check}.")
            bot.reply_to(message, f"✅ Admin `{admin_id_remove}` removed.")
            try: bot.send_message(admin_id_remove, "ℹ️ You are no longer an Admin.")
            except Exception as e: logger.error(f"Failed to notify removed admin {admin_id_remove}: {e}")
        else: bot.reply_to(message, f"❌ Failed to remove admin `{admin_id_remove}`. Check logs.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "👑 Enter Admin ID to remove or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_remove_admin_id)
    except Exception as e: logger.error(f"Error processing remove admin: {e}", exc_info=True); bot.reply_to(message, "Error.")

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    try:
        admin_list_str = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(list(admin_ids)))
        if not admin_list_str: admin_list_str = "(No Owner/Admins configured!)"
        bot.edit_message_text(f"👑 Current Admins:\n\n{admin_list_str}", call.message.chat.id,
                              call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
    except Exception as e: logger.error(f"Error listing admins: {e}")

def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID & days (e.g., `12345678 30`).\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_add_subscription_details)

def process_add_subscription_details(message):
    admin_id_check = message.from_user.id 
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub add cancelled."); return
    try:
        parts = message.text.split();
        if len(parts) != 2: raise ValueError("Incorrect format")
        sub_user_id = int(parts[0].strip()); days = int(parts[1].strip())
        if sub_user_id <= 0 or days <= 0: raise ValueError("User ID/days must be positive")

        current_expiry = user_subscriptions.get(sub_user_id, {}).get('expiry')
        start_date_new_sub = datetime.now() # Renamed
        if current_expiry and current_expiry > start_date_new_sub: start_date_new_sub = current_expiry
        new_expiry = start_date_new_sub + timedelta(days=days)
        save_subscription(sub_user_id, new_expiry)

        logger.info(f"Sub for {sub_user_id} by admin {admin_id_check}. Expiry: {new_expiry:%Y-%m-%d}")
        bot.reply_to(message, f"✅ Sub for `{sub_user_id}` by {days} days.\nNew expiry: {new_expiry:%Y-%m-%d}")
        try: bot.send_message(sub_user_id, f"🎉 Sub activated/extended by {days} days! Expires: {new_expiry:%Y-%m-%d}.")
        except Exception as e: logger.error(f"Failed to notify {sub_user_id} of new sub: {e}")
    except ValueError as e:
        bot.reply_to(message, f"⚠️ Invalid: {e}. Format: `ID days` or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID & days, or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_add_subscription_details)
    except Exception as e: logger.error(f"Error processing add sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to remove sub.\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_remove_subscription_id)

def process_remove_subscription_id(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub removal cancelled."); return
    try:
        sub_user_id_remove = int(message.text.strip()) # Renamed
        if sub_user_id_remove <= 0: raise ValueError("ID must be positive")
        if sub_user_id_remove not in user_subscriptions:
            bot.reply_to(message, f"⚠️ User `{sub_user_id_remove}` no active sub in memory."); return
        remove_subscription_db(sub_user_id_remove) 
        logger.warning(f"Sub removed for {sub_user_id_remove} by admin {admin_id_check}.")
        bot.reply_to(message, f"✅ Sub for `{sub_user_id_remove}` removed.")
        try: bot.send_message(sub_user_id_remove, "ℹ️ Your subscription removed by admin.")
        except Exception as e: logger.error(f"Failed to notify {sub_user_id_remove} of sub removal: {e}")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID to remove sub from, or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_remove_subscription_id)
    except Exception as e: logger.error(f"Error processing remove sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to check sub.\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_check_subscription_id)

def process_check_subscription_id(message):
    admin_id_check = message.from_user.id
    if admin_id_check not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Sub check cancelled."); return
    try:
        sub_user_id_check = int(message.text.strip()) # Renamed
        if sub_user_id_check <= 0: raise ValueError("ID must be positive")
        if sub_user_id_check in user_subscriptions:
            expiry_dt = user_subscriptions[sub_user_id_check].get('expiry')
            if expiry_dt:
                if expiry_dt > datetime.now():
                    days_left = (expiry_dt - datetime.now()).days
                    bot.reply_to(message, f"✅ User `{sub_user_id_check}` active sub.\nExpires: {expiry_dt:%Y-%m-%d %H:%M:%S} ({days_left} days left).")
                else:
                    bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` expired sub (On: {expiry_dt:%Y-%m-%d %H:%M:%S}).")
                    remove_subscription_db(sub_user_id_check) # Clean up
            else: bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` in sub list, but expiry missing. Re-add if needed.")
        else: bot.reply_to(message, f"ℹ️ User `{sub_user_id_check}` no active sub record.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID. Send numerical ID or /cancel.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID to check, or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_check_subscription_id)
    except Exception as e: logger.error(f"Error processing check sub: {e}", exc_info=True); bot.reply_to(message, "Error.")

# --- End Callback Query Handlers ---

# ================= PACKAGE SHOP / MANUAL PAYMENTS / ADMIN HOSTING =================
def init_shop_db():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS packages (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price REAL NOT NULL, days INTEGER NOT NULL, bot_limit INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
        conn.execute("CREATE TABLE IF NOT EXISTS payment_methods (method TEXT PRIMARY KEY, number TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS payment_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, package_id INTEGER NOT NULL, method TEXT NOT NULL, payment_ref TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, reviewed_by INTEGER, reviewed_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS package_subscriptions (user_id INTEGER PRIMARY KEY, package_id INTEGER NOT NULL, package_name TEXT NOT NULL, bot_limit INTEGER NOT NULL, expiry TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS package_access_revoked (user_id INTEGER PRIMARY KEY, revoked_at TEXT NOT NULL, revoked_by INTEGER NOT NULL)")
        count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        if count == 0:
            conn.executemany("INSERT INTO packages(name,price,days,bot_limit,active) VALUES(?,?,?,?,1)", [
                ("Basic • 1 Bot", 100, 30, 1),
                ("Basic • 2 Bots", 150, 30, 2),
            ])

init_shop_db()

def active_package_for_user(user_id):
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            row=conn.execute("SELECT package_name, bot_limit, expiry FROM package_subscriptions WHERE user_id=?",(user_id,)).fetchone()
        if row and datetime.fromisoformat(row[2]) > datetime.now(): return {"name":row[0],"bot_limit":row[1],"expiry":row[2]}
    except Exception: pass
    return None

def shop_markup():
    m=types.InlineKeyboardMarkup(row_width=2)
    with sqlite3.connect(DATABASE_PATH) as conn: rows=conn.execute("SELECT id,name,price,days,bot_limit FROM packages WHERE active=1 ORDER BY price").fetchall()
    for pid,name,price,days,lim in rows:
        m.add(types.InlineKeyboardButton(f"{name} • ৳{price:g}",callback_data=f"pkg_{pid}"))
    m.add(types.InlineKeyboardButton("❌ Cancel",callback_data="cancel_action"))
    m.add(types.InlineKeyboardButton("🔙 Main Menu",callback_data="back_to_main"))
    return m

def show_packages(chat_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows=conn.execute("SELECT id,name,price,days,bot_limit FROM packages WHERE active=1 ORDER BY price").fetchall()
        methods=conn.execute("SELECT method,number FROM payment_methods ORDER BY method").fetchall()
    if not rows:
        bot.send_message(chat_id,"🛒 এখনো কোনো প্যাকেজ যোগ করা হয়নি।", reply_markup=cancel_markup())
        return
    lines=["🛒 Available Packages\n"]
    for pid,name,price,days,lim in rows: lines.append(f"📦 {name}\n💰 দাম: ৳{price:g}\n📅 মেয়াদ: {days} দিন\n🤖 বট: {lim} টি\n")
    lines.append("\nপ্যাকেজ কিনতে নিচের বাটনে চাপ দিন। পেমেন্টের পর Transaction ID/রেফারেন্স পাঠাতে হবে; Admin যাচাই করে চালু করবেন।")
    bot.send_message(chat_id,"\n".join(lines),reply_markup=shop_markup())

def show_package_history(chat_id):
    """Show a neatly formatted package summary and all approved purchases."""
    user_id = chat_id  # In a private bot chat, chat_id is the Telegram user ID.
    now = datetime.now()
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            current = conn.execute(
                "SELECT package_name, bot_limit, expiry FROM package_subscriptions WHERE user_id=?",
                (user_id,)
            ).fetchone()
            total_purchases = conn.execute(
                "SELECT COUNT(*) FROM payment_requests WHERE user_id=? AND status='approved'",
                (user_id,)
            ).fetchone()[0]
            latest_payment = conn.execute(
                "SELECT method, payment_ref FROM payment_requests WHERE user_id=? AND status='approved' ORDER BY id DESC LIMIT 1",
                (user_id,)
            ).fetchone()
            history = conn.execute(
                """SELECT COALESCE(p.name, 'প্যাকেজ'), p.days, p.bot_limit,
                          r.created_at, r.method, r.payment_ref
                   FROM payment_requests r
                   LEFT JOIN packages p ON p.id = r.package_id
                   WHERE r.user_id=? AND r.status='approved'
                   ORDER BY r.id DESC""",
                (user_id,)
            ).fetchall()

        def safe(value):
            return str(value if value not in (None, '') else 'তথ্য নেই').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        def fmt_date(value):
            try:
                return datetime.fromisoformat(str(value)).strftime("%d-%m-%Y %I:%M %p")
            except (TypeError, ValueError):
                return safe(value)

        lines = ["✨ <b>আপনার প্যাকেজ ড্যাশবোর্ড</b>", "━━━━━━━━━━━━━━━━━━"]
        if current:
            package_name, bot_limit, expiry_text = current
            lines.append(f"[1] 📦 <b>প্যাকেজ:</b> {safe(package_name)}")
        else:
            lines.append("[1] 📦 <b>প্যাকেজ:</b> বর্তমানে কোনো প্যাকেজ চালু নেই")
            bot_limit, expiry_text = None, None

        lines.append(f"[2] 🧾 <b>মোট প্যাকেজ:</b> {total_purchases} টি")
        lines.append(f"[3] 🤖 <b>কয়টি বট/ফাইল:</b> {bot_limit if bot_limit is not None else '—'} টি")

        if expiry_text:
            try:
                expiry = datetime.fromisoformat(str(expiry_text))
                remaining_seconds = max(0, int((expiry - now).total_seconds()))
                days, rem = divmod(remaining_seconds, 86400)
                hours, rem = divmod(rem, 3600)
                minutes, _ = divmod(rem, 60)
                remaining_label = f"{days} দিন, {hours} ঘণ্টা, {minutes} মিনিট"
                if remaining_seconds == 0:
                    remaining_label += " (মেয়াদ শেষ)"
                lines.append(f"[4] ⏳ <b>মেয়াদ বাকি:</b> {remaining_label}")
                lines.append(f"[5] 📅 <b>মেয়াদ শেষ:</b> {expiry:%d-%m-%Y %I:%M %p}")
            except (TypeError, ValueError):
                lines.extend(["[4] ⏳ <b>মেয়াদ বাকি:</b> তথ্য পাওয়া যায়নি", "[5] 📅 <b>মেয়াদ শেষ:</b> তথ্য পাওয়া যায়নি"])
        else:
            lines.extend(["[4] ⏳ <b>মেয়াদ বাকি:</b> —", "[5] 📅 <b>মেয়াদ শেষ:</b> —"])

        if latest_payment:
            method, payment_ref = latest_payment
            lines.append(f"[6] 💳 <b>পেমেন্টের মাধ্যম:</b> {safe(method)}")
            lines.append(f"[7] 🆔 <b>Transaction ID:</b> {safe(payment_ref)}")
        else:
            lines.extend(["[6] 💳 <b>পেমেন্টের মাধ্যম:</b> —", "[7] 🆔 <b>Transaction ID:</b> —"])

        lines.extend(["━━━━━━━━━━━━━━━━━━", "📜 <b>প্যাকেজ কেনার ইতিহাস</b>"])
        bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

        if not history:
            bot.send_message(chat_id, "এখনো কোনো অনুমোদিত প্যাকেজ কেনার ইতিহাস নেই।")
            return

        chunks = []
        current_chunk = f"📜 <b>অনুমোদিত কেনাকাটা: {total_purchases} টি</b>\n"
        for index, row in enumerate(history, 1):
            name, package_days, package_bots, created_at, method, payment_ref = row
            item = (
                f"\n<b>#{index} • {safe(name)}</b>\n"
                f"📅 মেয়াদ: {package_days if package_days is not None else '—'} দিন\n"
                f"🤖 বট/ফাইল: {package_bots if package_bots is not None else '—'} টি\n"
                f"🕒 কেনার তারিখ: {fmt_date(created_at)}\n"
                f"💳 মাধ্যম: {safe(method)}\n"
                f"🆔 Transaction ID: {safe(payment_ref)}\n"
                "──────────────────\n"
            )
            if len(current_chunk) + len(item) > 3500:
                chunks.append(current_chunk)
                current_chunk = "📜 <b>প্যাকেজ কেনার ইতিহাস (চলমান)</b>\n"
            current_chunk += item
        if current_chunk.strip():
            chunks.append(current_chunk)
        for chunk in chunks:
            bot.send_message(chat_id, chunk, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Package history error for user {user_id}: {e}", exc_info=True)
        bot.send_message(chat_id, "⚠️ প্যাকেজের তথ্য দেখাতে সমস্যা হয়েছে। পরে আবার চেষ্টা করুন।")
def show_packages_callback(call):
    bot.answer_callback_query(call.id)
    show_packages(call.message.chat.id)

def package_selected_callback(call):
    try:
        pid=int(call.data.split('_',1)[1])
        with sqlite3.connect(DATABASE_PATH) as conn: row=conn.execute("SELECT name,price,days,bot_limit FROM packages WHERE id=? AND active=1",(pid,)).fetchone()
        if not row: bot.answer_callback_query(call.id,"প্যাকেজ পাওয়া যায়নি",show_alert=True); return
        name,price,days,lim=row
        with sqlite3.connect(DATABASE_PATH) as conn:
            configured = dict(conn.execute("SELECT method,number FROM payment_methods").fetchall())
        # Show only payment methods enabled by the admin in the database.
        icons = {"bkash": "💗", "nagad": "🟠", "rocket": "🟣", "binance": "🟡"}
        methods = list(configured.items())
        m=types.InlineKeyboardMarkup(row_width=2)
        for method,number in methods:
            icon = icons.get(method.lower(), "💳")
            button_label = f"{icon} {method} • {number}" if number else f"{icon} {method} • সেটআপ বাকি"
            m.add(types.InlineKeyboardButton(button_label[:60],callback_data=f"paymethod_{pid}_{method}"))
        m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
        if not methods:
            bot.answer_callback_query(call.id, "এখনো কোনো পেমেন্ট মাধ্যম যোগ করা হয়নি।", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id,f"📦 {name} | ৳{price:g} | {days} দিন | {lim} বট\\nপেমেন্ট মাধ্যম বাছাই করুন:",reply_markup=m)
    except Exception as e: logger.exception("package selection failed: %s",e)

def payment_method_selected_callback(call):
    try:
        _,pid,method=call.data.split('_',2); pid=int(pid)
        with sqlite3.connect(DATABASE_PATH) as conn:
            row=conn.execute("SELECT name,price FROM packages WHERE id=? AND active=1",(pid,)).fetchone()
            number=conn.execute("SELECT number FROM payment_methods WHERE method=?",(method,)).fetchone()
        if not row:
            bot.answer_callback_query(call.id,"প্যাকেজটি পাওয়া যায়নি।",show_alert=True); return
        if not number or not number[0]:
            bot.answer_callback_query(call.id,f"{method} এখনো সেটআপ করা হয়নি। Admin-কে পেমেন্ট নম্বর/ID যোগ করতে বলুন।",show_alert=True); return
        bot.answer_callback_query(call.id)
        msg=bot.send_message(call.message.chat.id,f"{method} নম্বরে ৳{row[1]:g} পেমেন্ট করুন: {number[0]}\nএরপর Transaction ID/রেফারেন্স লিখে পাঠান।", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg,save_payment_reference,pid,method)
    except Exception as e: logger.exception("payment method selection failed: %s",e)

def save_payment_reference(message,pid,method):
    if not message.text or message.text.strip().lower()=="/cancel": bot.reply_to(message,"পেমেন্ট রিকোয়েস্ট বাতিল করা হয়েছে।"); return
    ref=message.text.strip()[:200]
    with sqlite3.connect(DATABASE_PATH) as conn:
        cur=conn.execute("INSERT INTO payment_requests(user_id,package_id,method,payment_ref,status,created_at) VALUES(?,?,?,?,?,?)",(message.from_user.id,pid,method,ref,'pending',datetime.now().isoformat()))
        rid=cur.lastrowid
        pkg=conn.execute("SELECT name,price,days,bot_limit FROM packages WHERE id=?",(pid,)).fetchone()
    bot.reply_to(message,"✅ পেমেন্ট রিকোয়েস্ট জমা হয়েছে। Admin যাচাই করে Approve করলে প্যাকেজ চালু হবে।")
    if pkg:
        uname = ('@' + message.from_user.username) if message.from_user.username else 'Username নেই'
        text=f"🧾 New payment request #{rid}\n👤 Name: {message.from_user.first_name or 'User'}\n✳️ Username: {uname}\n🆔 User ID: {message.from_user.id}\n📦 Package: {pkg[0]} | ৳{pkg[1]:g} | {pkg[2]} দিন | {pkg[3]} bots\n💳 Method: {method}\n🧾 Reference: {ref}"
        kb=types.InlineKeyboardMarkup(row_width=2).row(types.InlineKeyboardButton("✅ Approve",callback_data=f"payapprove_{rid}"),types.InlineKeyboardButton("❌ Reject",callback_data=f"paycancel_{rid}"))
        kb.add(types.InlineKeyboardButton("Close", callback_data="cancel_action"))
        for aid in admin_ids:
            try: bot.send_message(aid,text,reply_markup=kb)
            except Exception: pass

def show_pending_payments(call):
    bot.answer_callback_query(call.id)
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows=conn.execute("SELECT id,user_id,package_id,method,payment_ref FROM payment_requests WHERE status='pending' ORDER BY id DESC LIMIT 30").fetchall()
    if not rows: bot.send_message(call.message.chat.id,"📭 Pending payment নেই।"); return
    for rid,uid,pid,method,ref in rows:
        with sqlite3.connect(DATABASE_PATH) as conn: pkg=conn.execute("SELECT name,price FROM packages WHERE id=?",(pid,)).fetchone()
        title=pkg[0] if pkg else "Deleted package"; price=pkg[1] if pkg else 0
        kb=types.InlineKeyboardMarkup(row_width=2).row(types.InlineKeyboardButton("✅ Approve",callback_data=f"payapprove_{rid}"),types.InlineKeyboardButton("❌ Reject",callback_data=f"paycancel_{rid}"))
        kb.add(types.InlineKeyboardButton("Close", callback_data="cancel_action"))
        bot.send_message(call.message.chat.id,f"#{rid} | User ID: {uid} | {title} ৳{price:g} | {method} | Ref: {ref}",reply_markup=kb)

def admin_payment_decision(call,approve):
    if call.from_user.id not in admin_ids: bot.answer_callback_query(call.id,"Admin only",show_alert=True); return
    rid=int(call.data.rsplit('_',1)[1])
    with sqlite3.connect(DATABASE_PATH) as conn:
        row=conn.execute("SELECT user_id,package_id,status FROM payment_requests WHERE id=?",(rid,)).fetchone()
        if not row or row[2]!='pending': bot.answer_callback_query(call.id,"Already processed / not found",show_alert=True); return
        uid,pid,_=row
        if approve:
            pkg=conn.execute("SELECT name,days,bot_limit FROM packages WHERE id=? AND active=1",(pid,)).fetchone()
            if not pkg: bot.answer_callback_query(call.id,"Package missing",show_alert=True); return
            name,days,lim=pkg
            old=conn.execute("SELECT expiry FROM package_subscriptions WHERE user_id=?",(uid,)).fetchone()
            start=datetime.now()
            if old:
                try:
                    old_exp=datetime.fromisoformat(old[0])
                    if old_exp>start: start=old_exp
                except Exception: pass
            expiry=start+timedelta(days=days)
            conn.execute("INSERT OR REPLACE INTO package_subscriptions(user_id,package_id,package_name,bot_limit,expiry) VALUES(?,?,?,?,?)",(uid,pid,name,lim,expiry.isoformat()))
            conn.execute("DELETE FROM package_access_revoked WHERE user_id=?", (uid,))
            conn.execute("UPDATE payment_requests SET status='approved',reviewed_by=?,reviewed_at=? WHERE id=?",(call.from_user.id,datetime.now().isoformat(),rid))
        else:
            conn.execute("UPDATE payment_requests SET status='cancelled',reviewed_by=?,reviewed_at=? WHERE id=?",(call.from_user.id,datetime.now().isoformat(),rid))
    bot.answer_callback_query(call.id,"Approved" if approve else "Cancelled")
    try:
        if approve: bot.send_message(uid,f"🎉 Payment approved!\n📦 Package: {name}\n🤖 Bot limit: {lim}\n📅 Expiry: {expiry:%Y-%m-%d %H:%M}")
        else: bot.send_message(uid,"❌ আপনার পেমেন্ট রিকোয়েস্ট Admin বাতিল করেছে। প্রয়োজনে Owner-এর সাথে যোগাযোগ করুন।")
    except Exception: pass
    try: bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
    except Exception: pass

def create_admin_panel():
    m=types.InlineKeyboardMarkup(row_width=2)
    m.row(types.InlineKeyboardButton("📦 Manage Packages",callback_data="packages_admin"),types.InlineKeyboardButton("💳 Pending Payments",callback_data="payments_admin"))
    m.row(types.InlineKeyboardButton("💳 Payment Methods Manage",callback_data="methods_admin"),types.InlineKeyboardButton("👥 Users & Bots",callback_data="users_admin"))
    m.row(types.InlineKeyboardButton("🗑️ Delete User Package",callback_data="adminpkg_delete_user"))
    m.row(types.InlineKeyboardButton("➕ Add Admin",callback_data="add_admin"),types.InlineKeyboardButton("➖ Remove Admin",callback_data="remove_admin"))
    m.add(types.InlineKeyboardButton("📋 List Admins",callback_data="list_admins"))
    m.add(types.InlineKeyboardButton("❌ Cancel",callback_data="cancel_action"),types.InlineKeyboardButton("🔙 Main Menu",callback_data="back_to_main"))
    return m

def admin_packages_menu(call):
    bot.answer_callback_query(call.id)
    with sqlite3.connect(DATABASE_PATH) as conn: rows=conn.execute("SELECT id,name,price,days,bot_limit FROM packages WHERE active=1 ORDER BY id").fetchall()
    m=types.InlineKeyboardMarkup(row_width=1)
    for pid,name,price,days,lim in rows: m.add(types.InlineKeyboardButton(f"🗑 {name} | ৳{price:g} | {days}d | {lim} bots",callback_data=f"pkgdel_{pid}"))
    m.add(types.InlineKeyboardButton("➕ Add Package",callback_data="package_add"))
    m.add(types.InlineKeyboardButton("❌ Cancel",callback_data="cancel_action"))
    bot.send_message(call.message.chat.id,"📦 Packages (tap to deactivate):",reply_markup=m)

def begin_add_package(call):
    bot.answer_callback_query(call.id)
    msg=bot.send_message(call.message.chat.id,"নতুন প্যাকেজ দিন এই ফরম্যাটে:\nName | Price | Days | Bot Limit\nউদাহরণ: Basic | 100 | 30 | 2", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg,save_new_package)

def save_new_package(message):
    if message.from_user.id not in admin_ids: return
    if not message.text or message.text.strip().lower()=="/cancel": return
    try:
        name,price,days,lim=[x.strip() for x in message.text.split('|')]
        price=float(price); days=int(days); lim=int(lim)
        if not name or price<=0 or days<=0 or lim<=0: raise ValueError()
        with sqlite3.connect(DATABASE_PATH) as conn: conn.execute("INSERT INTO packages(name,price,days,bot_limit) VALUES(?,?,?,?)",(name[:60],price,days,lim))
        bot.reply_to(message,"✅ প্যাকেজ যোগ হয়েছে। এখন Buy Package-এ দেখাবে।")
    except Exception: bot.reply_to(message,"❌ ভুল ফরম্যাট। আবার দিন: Name | Price | Days | Bot Limit")

def delete_package_callback(call):
    pid=int(call.data.split('_',1)[1])
    with sqlite3.connect(DATABASE_PATH) as conn: conn.execute("UPDATE packages SET active=0 WHERE id=?",(pid,))
    bot.answer_callback_query(call.id,"Package deactivated")
    try: bot.edit_message_text("প্যাকেজটি বন্ধ করা হয়েছে।",call.message.chat.id,call.message.message_id)
    except Exception: pass

def payment_methods_admin_menu(call):
    bot.answer_callback_query(call.id)
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows = conn.execute("SELECT method,number FROM payment_methods ORDER BY method").fetchall()
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("➕ Add Payment Method", callback_data="payment_add_method"))
    m.add(types.InlineKeyboardButton("🔢 Set / Change Number or Wallet ID", callback_data="payment_set_number"))
    m.add(types.InlineKeyboardButton("🗑 Delete Payment Method", callback_data="payment_delete_method"))
    m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    if rows:
        summary = "\n".join(f"• {name}: {number or 'নম্বর/ID সেট করা হয়নি'}" for name, number in rows)
    else:
        summary = "এখনো কোনো পেমেন্ট মাধ্যম যোগ করা হয়নি।"
    bot.send_message(call.message.chat.id, "💳 Payment Methods Admin\n\n" + summary, reply_markup=m)

def begin_add_payment_method(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "নতুন পেমেন্ট মেথডের নাম লিখুন (যেমন bKash, Nagad, Rocket, Binance)।\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, save_new_payment_method)

def save_new_payment_method(message):
    if message.from_user.id not in admin_ids: return
    if not message.text or message.text.strip().lower() == "/cancel":
        bot.reply_to(message, "বাতিল করা হয়েছে।"); return
    method = message.text.strip()[:30]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .()-]{0,29}", method):
        bot.reply_to(message, "❌ নামটি ঠিক নয়। ইংরেজি অক্ষর/সংখ্যা ও সাধারণ স্পেস ব্যবহার করুন।"); return
    with sqlite3.connect(DATABASE_PATH) as conn:
        exists = conn.execute("SELECT 1 FROM payment_methods WHERE lower(method)=lower(?)", (method,)).fetchone()
        if exists:
            bot.reply_to(message, "এই মেথড আগে থেকেই আছে। Set / Change Number অপশন ব্যবহার করুন।"); return
        conn.execute("INSERT INTO payment_methods(method,number) VALUES(?, '')", (method,))
    bot.reply_to(message, f"✅ {method} যোগ হয়েছে। এখন Payment Methods Manage → Set / Change Number থেকে নম্বর/Wallet ID দিন।")

def begin_set_payment_number(call):
    bot.answer_callback_query(call.id)
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows = conn.execute("SELECT method,number FROM payment_methods ORDER BY method").fetchall()
    if not rows:
        bot.send_message(call.message.chat.id, "আগে ➕ Add Payment Method দিয়ে একটি মেথড যোগ করুন।"); return
    m = types.InlineKeyboardMarkup(row_width=1)
    for method,number in rows:
        m.add(types.InlineKeyboardButton(f"🔢 {method} — {number or 'নম্বর সেট নেই'}"[:60], callback_data=f"pmset_{method}"))
    m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    bot.send_message(call.message.chat.id, "যে পেমেন্ট মেথডের নম্বর/Wallet ID সেট বা পরিবর্তন করবেন সেটি বাছুন:", reply_markup=m)

def select_payment_number_method(call):
    bot.answer_callback_query(call.id)
    method = call.data[len("pmset_"):]
    msg = bot.send_message(call.message.chat.id, f"{method}-এর নতুন মোবাইল নম্বর / Wallet ID লিখুন।\nUse Cancel below to abort.", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, save_payment_number, method)

def save_payment_number(message, method):
    if message.from_user.id not in admin_ids: return
    if not message.text or message.text.strip().lower() == "/cancel":
        bot.reply_to(message, "বাতিল করা হয়েছে।"); return
    number = message.text.strip()[:100]
    if not re.fullmatch(r"[A-Za-z0-9@._:+/ -]{3,100}", number):
        bot.reply_to(message, "❌ নম্বর/ID ফরম্যাট ঠিক নয়। আবার চেষ্টা করুন।"); return
    with sqlite3.connect(DATABASE_PATH) as conn:
        cur = conn.execute("UPDATE payment_methods SET number=? WHERE method=?", (number, method))
        if cur.rowcount == 0:
            bot.reply_to(message, "❌ পেমেন্ট মেথডটি পাওয়া যায়নি।"); return
    bot.reply_to(message, f"✅ {method}-এর নম্বর/Wallet ID আপডেট হয়েছে।")

def begin_delete_payment_method(call):
    bot.answer_callback_query(call.id)
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows = conn.execute("SELECT method,number FROM payment_methods ORDER BY method").fetchall()
    if not rows:
        bot.send_message(call.message.chat.id, "ডিলিট করার মতো কোনো পেমেন্ট মেথড নেই।"); return
    m = types.InlineKeyboardMarkup(row_width=1)
    for method,number in rows:
        m.add(types.InlineKeyboardButton(f"🗑 {method} — {number or 'নম্বর সেট নেই'}"[:60], callback_data=f"pmdel_{method}"))
    m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    bot.send_message(call.message.chat.id, "যে পেমেন্ট মেথড ডিলিট করবেন সেটি বাছুন:", reply_markup=m)

def delete_payment_method_callback(call):
    method = call.data[len("pmdel_"):]
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("DELETE FROM payment_methods WHERE method=?", (method,))
    bot.answer_callback_query(call.id, f"{method} ডিলিট হয়েছে")
    try: bot.edit_message_text(f"🗑 {method} পেমেন্ট মেথড ডিলিট করা হয়েছে।", call.message.chat.id, call.message.message_id)
    except Exception: pass

# Backward-compatible bulk update handler, retained for any older callback references.
def begin_set_payment_methods(call):
    bot.answer_callback_query(call.id)
    msg=bot.send_message(call.message.chat.id,"প্রতি লাইনে একটি করে লিখুন: bKash: 01XXXXXXXXX\nNagad: 01XXXXXXXXX\nRocket: 01XXXXXXXXX\nBinance: WalletID\nএই তালিকা বর্তমান মেথডগুলো প্রতিস্থাপন করবে।", reply_markup=cancel_markup())
    bot.register_next_step_handler(msg,save_payment_methods)

def save_payment_methods(message):
    if message.from_user.id not in admin_ids: return
    if not message.text or message.text.strip().lower()=="/cancel": return
    entries=[]
    canonical = {name.lower(): name for name in ("bKash", "Nagad", "Rocket", "Binance")}
    for line in message.text.splitlines():
        if ':' not in line: continue
        method,number=line.split(':',1); method=method.strip(); number=number.strip()
        method = canonical.get(method.lower(), method)
        if method and re.fullmatch(r'[A-Za-z0-9@._:+/ -]{3,100}',number): entries.append((method[:30],number))
    if not entries: bot.reply_to(message,"❌ কোনো বৈধ নম্বর পাওয়া যায়নি।"); return
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("DELETE FROM payment_methods")
        conn.executemany("INSERT INTO payment_methods(method,number) VALUES(?,?)",entries)
    bot.reply_to(message,"✅ Payment methods updated: "+", ".join(x[0] for x in entries))

def show_hosted_users(call):
    bot.answer_callback_query(call.id)
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows=conn.execute("SELECT u.user_id,COALESCE(p.username,''),COALESCE(p.first_name,'User'),COUNT(f.file_name) FROM active_users u LEFT JOIN user_profiles p ON p.user_id=u.user_id LEFT JOIN user_files f ON f.user_id=u.user_id GROUP BY u.user_id,p.username,p.first_name ORDER BY COALESCE(p.last_seen,'') DESC LIMIT 50").fetchall()
    m=types.InlineKeyboardMarkup(row_width=1)
    for uid,username,first_name,count in rows:
        label = '@'+username if username else first_name
        m.add(types.InlineKeyboardButton(f"👤 {label} | ID {uid} | {count} file(s)",callback_data=f"adminuser_{uid}"))
    m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    bot.send_message(call.message.chat.id,"👥 Users & uploaded files (latest 50):",reply_markup=m)

def show_user_bots_callback(call):
    uid=int(call.data.split('_',1)[1])
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows=conn.execute("SELECT file_name,file_type FROM user_files WHERE user_id=? ORDER BY file_name",(uid,)).fetchall()
        profile=conn.execute("SELECT username,first_name FROM user_profiles WHERE user_id=?",(uid,)).fetchone()
        package=conn.execute("SELECT package_name,expiry FROM package_subscriptions WHERE user_id=?",(uid,)).fetchone()
    username, first_name = profile if profile else ('', 'User')
    display_name = ('@'+username) if username else (first_name or 'User')
    package_label = package[0] if package else 'No active package'
    m=types.InlineKeyboardMarkup(row_width=1)
    for name,typ in rows:
        running=is_bot_running(uid,name)
        m.add(types.InlineKeyboardButton(f"{'🟢' if running else '🔴'} {name} ({typ}) • Manage/Delete",callback_data=f"file_{uid}_{name}"))
    m.add(types.InlineKeyboardButton("🗑️ Delete User Package", callback_data=f"userpkgdel_{uid}"))
    m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    m.add(types.InlineKeyboardButton("🔙 Users",callback_data="users_admin"))
    bot.send_message(call.message.chat.id,
        f"👤 User: {display_name}\n🆔 User ID: <code>{uid}</code>\n📦 Package: {package_label}\n🤖 Bots/Files: {len(rows)}",
        reply_markup=m, parse_mode="HTML")

# --- Admin: revoke a user's package access ---
def begin_delete_user_package(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        "🗑️ Send the user's Telegram ID to delete their package.\n\n❌ Use Cancel below to stop.",
        reply_markup=cancel_markup())
    bot.register_next_step_handler(msg, process_delete_user_package_id)

def process_delete_user_package_id(message):
    if message.from_user.id not in admin_ids:
        return
    if not message.text or message.text.strip().lower() in ('/cancel', 'cancel'):
        bot.reply_to(message, "❌ Cancelled.")
        return
    try:
        uid = int(message.text.strip())
        if uid <= 0:
            raise ValueError
    except ValueError:
        msg = bot.reply_to(message, "⚠️ Invalid User ID. Send a numeric Telegram ID or press Cancel.", reply_markup=cancel_markup())
        bot.register_next_step_handler(msg, process_delete_user_package_id)
        return
    with sqlite3.connect(DATABASE_PATH) as conn:
        profile = conn.execute("SELECT username, first_name FROM user_profiles WHERE user_id=?", (uid,)).fetchone()
        package = conn.execute("SELECT package_name, bot_limit, expiry FROM package_subscriptions WHERE user_id=?", (uid,)).fetchone()
    if not profile and not package:
        bot.reply_to(message, f"⚠️ User {uid} was not found in the bot database.")
        return
    username, first_name = profile if profile else ('', 'User')
    display_name = ('@'+username) if username else (first_name or 'User')
    package_text = package[0] if package else 'No active package'
    expiry_text = package[2] if package else '—'
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(types.InlineKeyboardButton("🗑️ Confirm Delete", callback_data=f"userpkgconfirm_{uid}"),
          types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    bot.send_message(message.chat.id,
        f"⚠️ <b>Confirm package deletion</b>\n\n👤 User: {display_name}\n🆔 User ID: <code>{uid}</code>\n📦 Package: {package_text}\n📅 Expiry: {expiry_text}\n\nThe user will not be able to upload files until a new package payment is approved. Purchase history will be kept.",
        parse_mode="HTML", reply_markup=m)

def confirm_delete_user_package(call):
    uid = int(call.data.split('_', 1)[1])
    with sqlite3.connect(DATABASE_PATH) as conn:
        profile = conn.execute("SELECT username, first_name FROM user_profiles WHERE user_id=?", (uid,)).fetchone()
        package = conn.execute("SELECT package_name FROM package_subscriptions WHERE user_id=?", (uid,)).fetchone()
    username, first_name = profile if profile else ('', 'User')
    display_name = ('@'+username) if username else (first_name or 'User')
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(types.InlineKeyboardButton("🗑️ Confirm Delete", callback_data=f"userpkgconfirm_{uid}"),
          types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_action"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
        f"⚠️ <b>Delete package for this user?</b>\n👤 {display_name}\n🆔 <code>{uid}</code>\n📦 {package[0] if package else 'No active package'}",
        parse_mode="HTML", reply_markup=m)

def execute_delete_user_package(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "Admin permissions required", show_alert=True)
        return
    uid = int(call.data.split('_', 1)[1])
    now_text = datetime.now().isoformat()
    with sqlite3.connect(DATABASE_PATH) as conn:
        profile = conn.execute("SELECT username, first_name FROM user_profiles WHERE user_id=?", (uid,)).fetchone()
        conn.execute("DELETE FROM package_subscriptions WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
        conn.execute("INSERT OR REPLACE INTO package_access_revoked(user_id, revoked_at, revoked_by) VALUES(?,?,?)",
                     (uid, now_text, call.from_user.id))
    user_subscriptions.pop(uid, None)
    bot.answer_callback_query(call.id, "Package deleted; uploads blocked")
    try:
        bot.edit_message_text(f"✅ Package access deleted for User ID {uid}.\nThe user must buy a new package and wait for admin approval before uploading again.",
                              call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    try:
        bot.send_message(uid, "🔒 আপনার প্যাকেজ অ্যাডমিন মুছে দিয়েছেন। এখন ফাইল/বট আপলোড বন্ধ আছে। আবার প্যাকেজ কিনে Admin approval পেলে আপলোড করতে পারবেন।")
    except Exception as exc:
        logger.info(f"Could not notify user {uid} about package deletion: {exc}")
    logger.warning(f"Admin {call.from_user.id} revoked package access for user {uid}")

# Keep admin panel and package menus accessible from the text button.
def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message,"⚠️ Admin permissions required."); return
    bot.reply_to(message,"👑 Admin Panel",reply_markup=create_admin_panel())

# --- Cleanup Function ---
def cleanup():
    logger.warning("Shutdown. Cleaning up processes...")
    script_keys_to_stop = list(bot_scripts.keys()) 
    if not script_keys_to_stop: logger.info("No scripts running. Exiting."); return
    logger.info(f"Stopping {len(script_keys_to_stop)} scripts...")
    for key in script_keys_to_stop:
        if key in bot_scripts: logger.info(f"Stopping: {key}"); kill_process_tree(bot_scripts[key])
        else: logger.info(f"Script {key} already removed.")
    logger.warning("Cleanup finished.")
atexit.register(cleanup)

# --- Main Execution ---
if __name__ == '__main__':
    logger.info("="*40 + "\n🤖 Bot Starting Up...\n" + f"🐍 Python: {sys.version.split()[0]}\n" +
                f"🔧 Base Dir: {BASE_DIR}\n📁 Upload Dir: {UPLOAD_BOTS_DIR}\n" +
                f"📊 Data Dir: {IROTECH_DIR}\n🔑 Owner ID: {OWNER_ID}\n🛡️ Admins: {admin_ids}\n" + "="*40)
    keep_alive()
    logger.info("🚀 Starting polling...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout: logger.warning("Polling ReadTimeout. Restarting in 5s..."); time.sleep(5)
        except requests.exceptions.ConnectionError as ce: logger.error(f"Polling ConnectionError: {ce}. Retrying in 15s..."); time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Unrecoverable polling error: {e}", exc_info=True)
            logger.info("Restarting polling in 30s due to critical error..."); time.sleep(30)
        finally: logger.warning("Polling attempt finished. Will restart if in loop."); time.sleep(1)
