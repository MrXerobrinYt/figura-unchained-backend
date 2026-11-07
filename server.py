import hashlib
import html
import random
import uuid
import os
import re
import sys
import time
import secrets
import atexit
import sqlite3
import threading
import bcrypt
import jwt
import ipaddress
import asyncio
import logging
import json
import glob
import zlib
import shutil
import tempfile
import zipfile
import base64
from urllib.parse import unquote_plus
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from aiohttp import web, WSMsgType, ClientSession, ClientTimeout
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = [
    "PORT", "DEBUG", "AVATAR_DIR", "MAX_PING_BPS", 
    "MAX_PING_SIZE", "MAX_AVATAR_SIZE", "REQUIRE_AUTH",
    "RATE_LIMIT_REQUESTS", "RATE_LIMIT_WINDOW", "TRUSTED_PROXIES",
    "RATE_LIMIT_CLEANUP_INTERVAL", "WS_MAX_CONNECTIONS_PER_IP",
    "WS_MAX_MESSAGES_PER_SEC", "WS_MAX_SUBS_PER_CLIENT",
    "LOGIN_RATE_LIMIT_REQUESTS", "LOGIN_RATE_LIMIT_WINDOW",
    "MAX_AVATAR_UNPACKED_SIZE", "MIN_ADDON_VERSION",
    "ASSETS_UPDATE_INTERVAL_HOURS", "ASSETS_RATE_LIMIT_REQUESTS",
    "ASSETS_RATE_LIMIT_WINDOW", "MAX_PRIDE_BADGE_ID"
]

missing_vars = [var for var in REQUIRED_VARS if not os.environ.get(var)]

if missing_vars:
    print(f"[FATAL ERROR] Missing required variables in .env: {', '.join(missing_vars)}")
    sys.exit(1)

config_errors = []

try:
    PORT = int(os.environ.get("PORT"))
except ValueError:
    config_errors.append("PORT must be an integer.")

HOST = os.environ.get("IP", "0.0.0.0")
DEBUG_env = os.environ.get("DEBUG").lower()
if DEBUG_env not in ["true", "false"]:
    config_errors.append(f"DEBUG must be 'true' or 'false', got '{DEBUG_env}'")
else:
    DEBUG = DEBUG_env == "true"
    
REQUIRE_AUTH_env = os.environ.get("REQUIRE_AUTH").lower()
if REQUIRE_AUTH_env not in ["true", "false"]:
    config_errors.append(f"REQUIRE_AUTH must be 'true' or 'false', got '{REQUIRE_AUTH_env}'")
else:
    REQUIRE_AUTH = REQUIRE_AUTH_env == "true"

try:
    MAX_PING_BPS = int(os.environ.get("MAX_PING_BPS"))
    MAX_PING_SIZE = int(os.environ.get("MAX_PING_SIZE"))
    MAX_AVATAR_SIZE = int(os.environ.get("MAX_AVATAR_SIZE"))
    RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS"))
    RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW"))
    RATE_LIMIT_CLEANUP_INTERVAL = int(os.environ.get("RATE_LIMIT_CLEANUP_INTERVAL"))
    WS_MAX_CONNECTIONS_PER_IP = int(os.environ.get("WS_MAX_CONNECTIONS_PER_IP"))
    WS_MAX_MESSAGES_PER_SEC = int(os.environ.get("WS_MAX_MESSAGES_PER_SEC"))
    WS_MAX_SUBS_PER_CLIENT = int(os.environ.get("WS_MAX_SUBS_PER_CLIENT"))
    LOGIN_RATE_LIMIT_REQUESTS = int(os.environ.get("LOGIN_RATE_LIMIT_REQUESTS"))
    LOGIN_RATE_LIMIT_WINDOW = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW"))
    MAX_AVATAR_UNPACKED_SIZE = int(os.environ.get("MAX_AVATAR_UNPACKED_SIZE"))
    if MAX_AVATAR_UNPACKED_SIZE < MAX_AVATAR_SIZE:
        config_errors.append("MAX_AVATAR_UNPACKED_SIZE must not be smaller than MAX_AVATAR_SIZE.")
except ValueError:
    config_errors.append("Limits and intervals must be integers.")

AVATAR_DIR = os.environ.get("AVATAR_DIR")

CUSTOM_REAL_IP_HEADER = os.environ.get("CUSTOM_REAL_IP_HEADER")
if CUSTOM_REAL_IP_HEADER:
    CUSTOM_REAL_IP_HEADER = CUSTOM_REAL_IP_HEADER.strip()

trusted_networks = []
for net in os.environ.get("TRUSTED_PROXIES").split(","):
    net = net.strip()
    if not net:
        continue
    try:
        trusted_networks.append(ipaddress.ip_network(net))
    except ValueError:
        config_errors.append(f"Invalid TRUSTED_PROXIES format: {net}")

ADDON_VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)')

def parse_addon_version(raw):
    match = ADDON_VERSION_RE.match(raw or "")
    return tuple(int(part) for part in match.groups()) if match else None

MIN_ADDON_VERSION_RAW = os.environ.get("MIN_ADDON_VERSION").strip()
MIN_ADDON_VERSION = parse_addon_version(MIN_ADDON_VERSION_RAW)
if MIN_ADDON_VERSION is None:
    config_errors.append(f"MIN_ADDON_VERSION must look like '1.9.3', got '{MIN_ADDON_VERSION_RAW}'")

try:
    ASSETS_UPDATE_INTERVAL_HOURS = int(os.environ.get("ASSETS_UPDATE_INTERVAL_HOURS"))
    ASSETS_RATE_LIMIT_REQUESTS = int(os.environ.get("ASSETS_RATE_LIMIT_REQUESTS"))
    ASSETS_RATE_LIMIT_WINDOW = int(os.environ.get("ASSETS_RATE_LIMIT_WINDOW"))
    MAX_PRIDE_BADGE_ID = int(os.environ.get("MAX_PRIDE_BADGE_ID"))
    if ASSETS_UPDATE_INTERVAL_HOURS < 0:
        config_errors.append("ASSETS_UPDATE_INTERVAL_HOURS must be 0 (updates disabled) or a positive number of hours.")
    if ASSETS_RATE_LIMIT_REQUESTS < 1 or ASSETS_RATE_LIMIT_WINDOW < 1:
        config_errors.append("ASSETS_RATE_LIMIT_REQUESTS and ASSETS_RATE_LIMIT_WINDOW must be at least 1.")
    if not 0 <= MAX_PRIDE_BADGE_ID <= 255:
        config_errors.append("MAX_PRIDE_BADGE_ID must be between 0 and 255.")
except ValueError:
    config_errors.append("Assets and badge settings must be integers.")

ASSETS_REPO = os.environ.get("ASSETS_REPO", "").strip() or "FiguraMC/Assets"
ASSETS_REF = os.environ.get("ASSETS_REF", "").strip() or "main"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', ASSETS_REPO):
    config_errors.append(f"ASSETS_REPO must look like 'owner/name', got '{ASSETS_REPO}'")
if not re.match(r'^[A-Za-z0-9_./-]{1,100}$', ASSETS_REF) or '..' in ASSETS_REF:
    config_errors.append(f"ASSETS_REF is not a valid branch, tag or commit: '{ASSETS_REF}'")

if config_errors:
    print("[FATAL ERROR] .env syntax/value errors found:")
    for err in config_errors:
        print(f"  - {err}")
    sys.exit(1)

os.makedirs("data", exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)

log_level = logging.DEBUG if DEBUG else logging.INFO
class OnlineFilter(logging.Filter):
    def filter(self, record):
        record.online = len(globals().get('active_connections') or ())
        return True

log_formatter = logging.Formatter('[%(asctime)s] [%(levelname)-7s][%(online)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(log_formatter)
stdout_handler.addFilter(OnlineFilter())

file_handler = RotatingFileHandler('data/server.log', maxBytes=5*1024*1024, backupCount=3)
file_handler.setFormatter(log_formatter)
file_handler.addFilter(OnlineFilter())

logging.basicConfig(level=log_level, handlers=[stdout_handler, file_handler])

def save_secure_file(path, content):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    with open(os.open(path, flags, mode), "w", encoding="utf-8") as f:
        f.write(content)

db_conn = sqlite3.connect('data/users.db', check_same_thread=False)
db_conn.execute('CREATE TABLE IF NOT EXISTS users (username TEXT, uuid TEXT PRIMARY KEY, password_hash TEXT, last_ip TEXT)')
db_conn.commit()
users_columns = [row[1] for row in db_conn.execute("PRAGMA table_info(users)")]
if users_columns[:1] != ['username']:
    username_source = 'username' if 'username' in users_columns else 'NULL'
    try:
        db_conn.execute('BEGIN')
        db_conn.execute('CREATE TABLE users_new (username TEXT, uuid TEXT PRIMARY KEY, password_hash TEXT, last_ip TEXT)')
        db_conn.execute(f'INSERT INTO users_new (username, uuid, password_hash, last_ip) SELECT {username_source}, uuid, password_hash, last_ip FROM users')
        db_conn.execute('DROP TABLE users')
        db_conn.execute('ALTER TABLE users_new RENAME TO users')
        db_conn.commit()
    except Exception:
        db_conn.rollback()
        raise
db_conn.execute('CREATE TABLE IF NOT EXISTS user_badges (uuid TEXT PRIMARY KEY, badge INTEGER NOT NULL)')
db_conn.commit()
db_lock = threading.Lock()

ADMIN_SESSION_TOKEN = secrets.token_hex(32)
save_secure_file("data/.admin_session", ADMIN_SESSION_TOKEN)

JWT_SECRET_FILE = "data/.jwt_secret"
if not os.path.exists(JWT_SECRET_FILE):
    JWT_SECRET = secrets.token_hex(64)
    save_secure_file(JWT_SECRET_FILE, JWT_SECRET)
else:
    with open(JWT_SECRET_FILE, "r") as f:
        JWT_SECRET = f.read().strip()

def cleanup_admin_session():
    if os.path.exists("data/.admin_session"):
        os.remove("data/.admin_session")

atexit.register(cleanup_admin_session)

UUID_REGEX = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

def is_valid_uuid(val):
    return bool(val and UUID_REGEX.match(val))

def log_info(msg):
    logging.info(msg)

def log_warn(msg):
    logging.warning(msg)

def log_error(msg, exc_info=False):
    logging.error(msg, exc_info=exc_info)

def log_debug(msg):
    if DEBUG:
        logging.debug(msg)

throttle_state = {}

def log_throttled(key, msg, interval=60):
    now = time.time()
    entry = throttle_state.get(key)
    if entry and now - entry[0] < interval:
        entry[1] += 1
        return
    suppressed = entry[1] if entry else 0
    throttle_state[key] = [now, 0]
    if suppressed:
        msg += f" (+{suppressed} similar suppressed)"
    logging.warning(msg)

def fmt_uuid(value):
    try:
        version = uuid.UUID(value).version
    except (ValueError, AttributeError, TypeError):
        return shown(value)
    return f"{value} (v{version if version else '?'})"

def shown(value, limit=40):
    text = str(value)
    return repr(text) if len(text) <= limit else f"<{len(text)} chars>"

def short_uuid(value):
    return str(value).split('-')[0]

def fmt_dur(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"

uuid_to_username = {}
user_session_uuids = {}
user_world_uuids = {}
user_canonical_uuids = {}

def canonical_uuid_for(username):
    digest = hashlib.md5(f"OfflinePlayer:{username}".encode('utf-8')).digest()
    return str(uuid.UUID(bytes=digest, version=3))

def clean_username(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 32:
        return None
    if UUID_REGEX.match(name):
        return None
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return None
    return name

def decode_header_name(raw):
    if not raw:
        return None
    try:
        return clean_username(unquote_plus(raw))
    except Exception:
        return None

def username_for_canonical(canonical):
    name = uuid_to_username.get(canonical)
    if name is not None and user_canonical_uuids.get(name) == canonical:
        return name
    return None

def resolve_canonical(any_uuid):
    name = uuid_to_username.get(any_uuid)
    if name is not None:
        return user_canonical_uuids.get(name, any_uuid)
    return any_uuid

def register_known_user(username):
    canonical = canonical_uuid_for(username)
    squatter = uuid_to_username.get(canonical)
    if squatter is not None and squatter != username:
        if user_session_uuids.get(squatter) == canonical:
            drop_session_alias(squatter)
        if user_world_uuids.get(squatter) == canonical:
            drop_world_alias(squatter)
    user_canonical_uuids[username] = canonical
    uuid_to_username[canonical] = username
    uuid_to_username[username] = username
    return canonical

def drop_session_alias(username):
    session = user_session_uuids.pop(username, None)
    if session is not None and uuid_to_username.get(session) == username:
        del uuid_to_username[session]

def register_session_alias(username, session_uuid):
    if session_uuid == user_canonical_uuids.get(username):
        drop_session_alias(username)
        return True, "same-as-canonical"
    owner = uuid_to_username.get(session_uuid)
    if owner is not None and owner != username:
        return False, f"already-bound-to {owner}"
    if user_session_uuids.get(username) not in (None, session_uuid):
        drop_session_alias(username)
    if user_world_uuids.get(username) == session_uuid:
        drop_world_alias(username)
    user_session_uuids[username] = session_uuid
    uuid_to_username[session_uuid] = username
    return True, "ok"

def drop_world_alias(username):
    world = user_world_uuids.pop(username, None)
    if world is not None and uuid_to_username.get(world) == username:
        del uuid_to_username[world]

def register_world_alias(username, world_uuid):
    if world_uuid in (user_canonical_uuids.get(username), user_session_uuids.get(username)):
        drop_world_alias(username)
        return True, "same-as-known"
    owner = uuid_to_username.get(world_uuid)
    if owner is not None and owner != username:
        return False, f"already-bound-to {owner}"
    if user_world_uuids.get(username) not in (None, world_uuid):
        drop_world_alias(username)
    user_world_uuids[username] = world_uuid
    uuid_to_username[world_uuid] = username
    return True, "ok"

def read_world_uuid(request):
    raw = request.headers.get('client-world-uuid', '').strip().lower()
    return raw if is_valid_uuid(raw) else None

def aliases_of(ident):
    canonical = user_canonical_uuids.get(ident) or (ident if is_valid_uuid(ident) else None)
    result = [canonical] if canonical else []
    session = user_session_uuids.get(ident)
    if session and session not in result:
        result.append(session)
    world = user_world_uuids.get(ident)
    if world and world not in result:
        result.append(world)
    return result

def get_watchers_for_user(ident, include_self=False):
    watchers = {}
    for alias in aliases_of(ident):
        for watcher in subscriptions.get(alias, ()):
            if (include_self or watcher != ident) and watcher not in watchers:
                watchers[watcher] = alias
    return watchers

def display_name(value):
    if not value:
        return "?"
    name = uuid_to_username.get(value)
    if name is not None:
        return name
    return short_uuid(value) if is_valid_uuid(value) else str(value)

def user_tag(canonical):
    name = username_for_canonical(canonical)
    return f"username={name}" if name else f"username=? uuid={fmt_uuid(canonical)}"

def resolve_client_identity(request, canonical):
    username = username_for_canonical(canonical)
    raw_name = request.headers.get('client-username')
    session_uuid = None
    if raw_name:
        claimed = decode_header_name(raw_name)
        if claimed is None:
            log_warn(f"[ID] ignored malformed client-username {shown(raw_name)} (canonical_uuid={canonical})")
        elif canonical_uuid_for(claimed) != canonical:
            log_warn(f"[ID] client-username={claimed!r} does not match canonical_uuid={canonical}; claim ignored")
        else:
            username = claimed
            register_known_user(username)
            raw_session = request.headers.get('client-uuid', '').strip().lower()
            if is_valid_uuid(raw_session):
                session_uuid = raw_session
    return username, session_uuid

VERSION_TOKEN_RE = re.compile(r'^[0-9A-Za-z.+_\-]{1,64}$')

def clean_version(raw):
    raw = (raw or '').strip()
    return raw if VERSION_TOKEN_RE.match(raw) else None

def addon_version_status(version):
    if not version:
        return "missing"
    parsed = parse_addon_version(version)
    if parsed is None:
        return "invalid"
    if parsed < MIN_ADDON_VERSION:
        return "outdated"
    return "ok"

UPDATE_TOAST_PACKET = b'\x03\x02' + "Please update Figura Unchained".encode('utf-8') + b'\x00' + f"Minimum version for your backend is {MIN_ADDON_VERSION_RAW}".encode('utf-8')

async def safe_send(ws, packet):
    try:
        await ws.send_bytes(packet)
        return True
    except Exception as e:
        log_debug(f"[WS] send failed: {e!r}")
        return False

async def notify_avatar_update(ident, reason, include_self=False):
    watchers = get_watchers_for_user(ident, include_self)
    for watcher, alias in watchers.items():
        target_ws = active_connections.get(watcher)
        if target_ws is not None and await safe_send(target_ws, b'\x02' + uuid.UUID(alias).bytes):
            log_debug(f"[EVENT-OUT] {reason} {display_name(ident)} -> {display_name(watcher)}")
    return len(watchers) - (1 if include_self and ident in watchers else 0)

def get_file_hash_sync(filepath):
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def db_is_registered_sync(client_uuid):
    with db_lock:
        cursor = db_conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE uuid = ?", (client_uuid,))
        return cursor.fetchone() is not None

def db_register_sync(client_uuid, username, client_hash, real_ip):
    if db_is_registered_sync(client_uuid):
        return False
    hashed_pw = bcrypt.hashpw(client_hash.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    with db_lock:
        try:
            db_conn.execute("INSERT INTO users (uuid, username, password_hash, last_ip) VALUES (?, ?, ?, ?)", (client_uuid, username, hashed_pw, real_ip))
            db_conn.commit()
        except sqlite3.IntegrityError:
            db_conn.rollback()
            return False
    return True

def db_login_sync(client_uuid, username, client_hash, real_ip):
    with db_lock:
        row = db_conn.execute("SELECT password_hash FROM users WHERE uuid = ?", (client_uuid,)).fetchone()
    if not row:
        return False
    if bcrypt.checkpw(client_hash.encode('utf-8'), row[0].encode('utf-8')):
        with db_lock:
            db_conn.execute("UPDATE users SET last_ip = ?, username = COALESCE(?, username) WHERE uuid = ?", (real_ip, username, client_uuid))
            db_conn.commit()
        return True
    return False

def load_user_aliases():
    with db_lock:
        rows = db_conn.execute("SELECT uuid, username FROM users WHERE username IS NOT NULL").fetchall()
    loaded = 0
    for stored_uuid, username in rows:
        if clean_username(username) is None or canonical_uuid_for(username) != stored_uuid:
            log_warn(f"[SYS] users table: username={username!r} does not match uuid={stored_uuid}, alias skipped")
            continue
        register_known_user(username)
        loaded += 1
    return loaded

def auth_status(request, client_uuid):
    if not REQUIRE_AUTH:
        return True, "auth-disabled"
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    else:
        raw_token = request.headers.get("token", "")
        if ":" in raw_token:
            token = raw_token.split(":", 1)[1]
    if not token:
        return False, "no-token"
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return False, "token-expired"
    except jwt.InvalidTokenError:
        return False, "token-invalid"
    if payload.get("uuid") != client_uuid:
        return False, "token-uuid-mismatch"
    return True, "ok"

def check_auth(request, client_uuid):
    return auth_status(request, client_uuid)[0]

def get_real_ip(request):
    try:
        client_ip = ipaddress.ip_address(request.remote)
    except ValueError:
        return request.remote

    is_trusted = any(client_ip in net for net in trusted_networks)
    
    if is_trusted:
        if CUSTOM_REAL_IP_HEADER:
            custom_ip = request.headers.get(CUSTOM_REAL_IP_HEADER)
            if custom_ip:
                return custom_ip.split(',')[0].strip()
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(',')[0].strip()
        xreal = request.headers.get("X-Real-IP")
        if xreal:
            return xreal.strip()
            
    return request.remote

active_connections = {}
connection_info = {}
active_auth = {}
subscriptions = {}
users_with_avatars = set()
rate_limits = {}
hash_cache = {}
ip_connections = {}
auth_rate_limits = {}
AUTH_PATHS = ('/api/auth/register', '/api/auth/login')
WS_PATHS = ('/ws', '/api/ws', '/api//ws')
VERSION_EXEMPT_PATHS = ('/api', '/api/', '/api/version', '/api/limits', '/api/motd')
VANILLA_AUTH_PATHS = ('/api/auth/id', '/api//auth/id', '/api/auth/verify', '/api//auth/verify')
ASSET_REQUEST_RE = re.compile(r'^(?:/api/?)?/assets/(v\d+)(?:/(.*))?$')
assets_rate_limits = {}
user_badges = {}
assets_snapshot = None
seen_http_clients = set()

def init_identity_state():
    loaded = load_user_aliases()
    for path in glob.glob(os.path.join(AVATAR_DIR, "*.nbt")):
        users_with_avatars.add(os.path.basename(path)[:-4].lower())
    return loaded

known_users_at_start = init_identity_state()

def note_http_client(request, real_ip):
    raw_version = request.headers.get('addon-version')
    raw_token = request.headers.get('token')
    if raw_version is None and raw_token is None:
        return
    token_uuid = (raw_token or '').split(':')[0].lower()
    version = clean_version(raw_version)
    key = (token_uuid or real_ip, version)
    if key in seen_http_clients:
        return
    if len(seen_http_clients) > 10000:
        seen_http_clients.clear()
    seen_http_clients.add(key)
    who = display_name(token_uuid) if is_valid_uuid(token_uuid) else f"ip={real_ip}"
    status = addon_version_status(version)
    if status == "ok":
        log_info(f"[HTTP] client {who} addon-version={version}")
    else:
        log_warn(f"[HTTP] client {who} addon-version={version or 'none'} status={status} (minimum {MIN_ADDON_VERSION_RAW})")

ASSETS_ROOT = os.path.join("data", "assets")
ASSETS_CURRENT_FILE = os.path.join(ASSETS_ROOT, "CURRENT")
ASSETS_SHA_RE = re.compile(r'^[0-9a-f]{40}$')
ASSETS_VERSION_RE = re.compile(r'^v\d+$')
AVATAR_ID_RE = re.compile(r'^[0-9a-fA-F\-]{36}$')
ASSETS_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._-]*$')
ASSETS_MAX_ZIP_BYTES = 64 * 1024 * 1024
ASSETS_MAX_FILE_BYTES = 16 * 1024 * 1024
ASSETS_MAX_TOTAL_BYTES = 128 * 1024 * 1024
ASSETS_MAX_ENTRIES = 20000
ASSETS_MAX_FILES = 5000
ASSETS_RETRY_SECONDS = 900
os.makedirs(ASSETS_ROOT, exist_ok=True)

def is_asset_request(path):
    match = ASSET_REQUEST_RE.match(path)
    if not match:
        return False
    rest = match.group(2)
    return not (rest and AVATAR_ID_RE.match(rest))

def is_safe_asset_relpath(rel):
    return bool(rel) and all(ASSETS_SEGMENT_RE.match(part) for part in rel.split('/'))

def hash_asset_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()

def build_assets_indexes(directory):
    indexes = {}
    skipped = []
    for version in sorted(os.listdir(directory)):
        version_dir = os.path.join(directory, version)
        if not ASSETS_VERSION_RE.match(version) or os.path.islink(version_dir) or not os.path.isdir(version_dir):
            continue
        index = {}
        for root, dirs, files in os.walk(version_dir):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, version_dir).replace(os.sep, '/')
                if os.path.islink(full) or not is_safe_asset_relpath(rel):
                    skipped.append(f"{version}/{rel}")
                    continue
                index[rel] = hash_asset_file(full)
        indexes[version] = index
    return indexes, skipped

def make_assets_snapshot(commit, directory, indexes):
    return {
        "commit": commit,
        "directory": directory,
        "indexes": indexes,
        "index_json": {version: json.dumps(index, separators=(',', ':')).encode('utf-8') for version, index in indexes.items()},
    }

def extract_assets_zip(zip_path, target_dir):
    skipped = []
    files = 0
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > ASSETS_MAX_ENTRIES:
            raise ValueError("archive has too many entries")
        roots = {info.filename.split('/', 1)[0] for info in infos}
        if len(roots) != 1:
            raise ValueError("unexpected archive layout")
        target_real = os.path.realpath(target_dir)
        for info in infos:
            name = info.filename
            if '\\' in name or name.startswith('/'):
                skipped.append(name)
                continue
            parts = name.split('/')[1:]
            if not parts or not parts[-1] or info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            is_license = parts == ['LICENSE']
            is_asset = len(parts) >= 2 and ASSETS_VERSION_RE.match(parts[0]) is not None
            if not (is_license or is_asset):
                continue
            if not is_safe_asset_relpath('/'.join(parts)):
                skipped.append('/'.join(parts))
                continue
            if info.file_size > ASSETS_MAX_FILE_BYTES:
                raise ValueError(f"{name} is too large")
            files += 1
            total += info.file_size
            if files > ASSETS_MAX_FILES or total > ASSETS_MAX_TOTAL_BYTES:
                raise ValueError("archive is too large")
            destination = os.path.join(target_dir, *parts)
            if os.path.commonpath([target_real, os.path.realpath(destination)]) != target_real:
                raise ValueError(f"{name} points outside of the target directory")
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with zf.open(info) as source, open(destination, 'xb') as target:
                shutil.copyfileobj(source, target, 65536)
    return skipped

def prune_assets_dir(keep):
    for name in os.listdir(ASSETS_ROOT):
        path = os.path.join(ASSETS_ROOT, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        if name.startswith('.staging-') or (ASSETS_SHA_RE.match(name) and name not in keep):
            shutil.rmtree(path, ignore_errors=True)

def publish_assets_snapshot(commit, staging_dir):
    if not ASSETS_SHA_RE.match(commit):
        raise ValueError("invalid commit id")
    indexes, _ = build_assets_indexes(staging_dir)
    if not any(indexes.values()):
        raise ValueError("snapshot contains no asset files")
    final_dir = os.path.join(ASSETS_ROOT, commit)
    if os.path.isdir(final_dir):
        shutil.rmtree(final_dir)
    os.replace(staging_dir, final_dir)
    temporary = ASSETS_CURRENT_FILE + ".tmp"
    with open(temporary, 'w', encoding='utf-8') as f:
        f.write(commit)
    os.replace(temporary, ASSETS_CURRENT_FILE)
    return make_assets_snapshot(commit, final_dir, indexes)

def load_current_assets_snapshot():
    try:
        with open(ASSETS_CURRENT_FILE, encoding='utf-8') as f:
            commit = f.read().strip()
    except OSError:
        return None
    if not ASSETS_SHA_RE.match(commit):
        return None
    directory = os.path.join(ASSETS_ROOT, commit)
    if not os.path.isdir(directory):
        return None
    indexes, _ = build_assets_indexes(directory)
    if not any(indexes.values()):
        return None
    return make_assets_snapshot(commit, directory, indexes)

def assets_request_headers(accept, with_token=False):
    headers = {"Accept": accept, "User-Agent": "figura-unchained-backend"}
    if with_token and GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

async def fetch_latest_assets_commit(session):
    url = f"https://api.github.com/repos/{ASSETS_REPO}/commits/{ASSETS_REF}"
    async with session.get(url, headers=assets_request_headers("application/vnd.github.sha", True)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub API answered {resp.status}")
        data = b''
        async for chunk in resp.content.iter_chunked(1024):
            data += chunk
            if len(data) > 1024:
                raise RuntimeError("unexpected GitHub API response")
    commit = data.decode('ascii', errors='replace').strip().lower()
    if not ASSETS_SHA_RE.match(commit):
        raise RuntimeError("GitHub API returned an invalid commit id")
    return commit

async def download_assets_archive(session, commit, destination):
    url = f"https://codeload.github.com/{ASSETS_REPO}/zip/{commit}"
    async with session.get(url, headers=assets_request_headers("application/zip")) as resp:
        if resp.status != 200:
            raise RuntimeError(f"archive download answered {resp.status}")
        received = 0
        with open(destination, 'wb') as out:
            async for chunk in resp.content.iter_chunked(65536):
                received += len(chunk)
                if received > ASSETS_MAX_ZIP_BYTES:
                    raise RuntimeError("archive is too large")
                await asyncio.to_thread(out.write, chunk)

async def sync_assets_once():
    global assets_snapshot
    async with ClientSession(timeout=ClientTimeout(total=300, connect=15)) as session:
        commit = await fetch_latest_assets_commit(session)
        previous = assets_snapshot
        if previous is not None and previous["commit"] == commit:
            log_debug(f"[ASSETS] already up to date commit={commit[:7]}")
            return
        staging = await asyncio.to_thread(tempfile.mkdtemp, prefix=".staging-", dir=ASSETS_ROOT)
        try:
            archive_path = os.path.join(staging, "download.zip")
            content_dir = os.path.join(staging, "content")
            os.mkdir(content_dir)
            await download_assets_archive(session, commit, archive_path)
            skipped = await asyncio.to_thread(extract_assets_zip, archive_path, content_dir)
            snapshot = await asyncio.to_thread(publish_assets_snapshot, commit, content_dir)
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, True)
    assets_snapshot = snapshot
    keep = {commit}
    if previous is not None:
        keep.add(previous["commit"])
    await asyncio.to_thread(prune_assets_dir, keep)
    counts = ", ".join(f"{version}={len(index)}" for version, index in snapshot["indexes"].items())
    log_info(f"[ASSETS] updated to commit={commit[:7]} files: {counts}" + (f" | skipped (unsupported names): {', '.join(skipped[:5])}" if skipped else ""))

async def assets_sync_loop(app):
    global assets_snapshot
    await asyncio.to_thread(prune_assets_dir, set())
    assets_snapshot = await asyncio.to_thread(load_current_assets_snapshot)
    if assets_snapshot is not None:
        counts = ", ".join(f"{version}={len(index)}" for version, index in assets_snapshot["indexes"].items())
        log_info(f"[ASSETS] loaded commit={assets_snapshot['commit'][:7]} files: {counts}")
    if ASSETS_UPDATE_INTERVAL_HOURS <= 0:
        log_info("[ASSETS] automatic updates are disabled (ASSETS_UPDATE_INTERVAL_HOURS=0)")
        return
    interval = ASSETS_UPDATE_INTERVAL_HOURS * 3600
    while True:
        delay = interval
        try:
            await sync_assets_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            delay = min(interval, ASSETS_RETRY_SECONDS)
            log_warn(f"[ASSETS] update failed, keeping the current files: {e!r}")
        await asyncio.sleep(delay)

def load_badges():
    with db_lock:
        rows = db_conn.execute("SELECT uuid, badge FROM user_badges").fetchall()
    for stored_uuid, badge in rows:
        if is_valid_uuid(stored_uuid) and isinstance(badge, int) and 0 <= badge <= MAX_PRIDE_BADGE_ID:
            user_badges[stored_uuid] = badge

def db_set_badge_sync(client_uuid, badge):
    with db_lock:
        if badge is None:
            db_conn.execute("DELETE FROM user_badges WHERE uuid = ?", (client_uuid,))
        else:
            db_conn.execute("INSERT OR REPLACE INTO user_badges (uuid, badge) VALUES (?, ?)", (client_uuid, badge))
        db_conn.commit()

def parse_badge_body(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False, None
    if value is None:
        return True, None
    if type(value) is int and 0 <= value <= MAX_PRIDE_BADGE_ID:
        return True, value
    return False, None

def pride_badges_for(target_uuid):
    badge = user_badges.get(target_uuid)
    if badge is None or not 0 <= badge <= MAX_PRIDE_BADGE_ID:
        return []
    return [0] * badge + [1]

load_badges()

# ======================== Сайт фамы (деревянная доска) ========================
FAMA_PASSWORD = os.environ.get("FAMA_PASSWORD", "").strip()
FAMA_DATA_PATH = os.path.join("data", "fam.json")
FAMA_SEED_PATH = "fam.json"

FAMA_DEFAULT_THREADS = [
    {"id": "married", "label": "Женаты", "color": "#e05252"},
    {"id": "kin", "label": "Кровь / родство", "color": "#c9973f"},
    {"id": "sibling", "label": "Братья / сёстры", "color": "#5b8def"},
    {"id": "friend", "label": "Друзья", "color": "#4caf6d"},
    {"id": "rival", "label": "Вражда", "color": "#9b59d0"},
]

fam_state = {}

def fam_clean_text(value, limit):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value.strip()[:limit]

def fam_clamp_pos(value, low, high, fallback):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return round(min(high, max(low, value)), 1)

def fam_slug(label, existing):
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "thread"
    slug = base
    n = 2
    while slug in existing:
        slug = base + "_" + str(n)
        n += 1
    return slug

def fam_clean_member(raw):
    if not isinstance(raw, dict):
        raise ValueError("member must be an object")
    name = re.sub(r"\s+", " ", fam_clean_text(raw.get("name"), 48))
    if not name:
        raise ValueError("every member needs a name")
    return {
        "name": name,
        "role": fam_clean_text(raw.get("role"), 64),
        "note": fam_clean_text(raw.get("note"), 500),
        "x": fam_clamp_pos(raw.get("x"), 2, 96, round(10 + random.random() * 78, 1)),
        "y": fam_clamp_pos(raw.get("y"), 4, 88, round(8 + random.random() * 74, 1)),
    }

def fam_sanitize(data):
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    members_raw = data.get("members") or []
    if not isinstance(members_raw, list):
        raise ValueError("members must be a list")
    if len(members_raw) > 200:
        raise ValueError("too many members (max 200)")
    members = [fam_clean_member(m) for m in members_raw]
    names = {m["name"] for m in members}

    types = []
    type_ids = set()
    for raw_type in (data.get("threads") or []):
        if not isinstance(raw_type, dict) or len(types) >= 16:
            continue
        label = fam_clean_text(raw_type.get("label"), 32)
        if not label:
            continue
        tid = fam_clean_text(raw_type.get("id"), 24)
        if not re.match(r"^[a-z0-9_]{1,24}$", tid or ""):
            tid = fam_slug(label, type_ids)
        elif tid in type_ids:
            tid = fam_slug(tid, type_ids)
        color = raw_type.get("color")
        if not isinstance(color, str) or not re.match(r"^#[0-9a-fA-F]{3,8}$", color):
            color = "#c9973f"
        type_ids.add(tid)
        types.append({"id": tid, "label": label, "color": color})
    if not types:
        for default_type in FAMA_DEFAULT_THREADS:
            types.append(dict(default_type))
            type_ids.add(default_type["id"])

    relations = []
    seen_pairs = set()
    for raw_rel in (data.get("relations") or []):
        if not isinstance(raw_rel, dict) or len(relations) >= 400:
            continue
        rel_from = fam_clean_text(raw_rel.get("from"), 48)
        rel_to = fam_clean_text(raw_rel.get("to"), 48)
        rel_type = fam_clean_text(raw_rel.get("type"), 24)
        if rel_from in names and rel_to in names and rel_from != rel_to and rel_type in type_ids:
            key = (rel_from, rel_to, rel_type)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            relations.append({"from": rel_from, "to": rel_to, "type": rel_type})

    return {
        "title": fam_clean_text(data.get("title"), 60) or "Фама",
        "tagline": fam_clean_text(data.get("tagline"), 120),
        "description": fam_clean_text(data.get("description"), 4000),
        "threads": types,
        "members": members,
        "relations": relations,
    }

def load_fam_state():
    global fam_state
    for path in (FAMA_DATA_PATH, FAMA_SEED_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                data = fam_sanitize(json.load(f))
            fam_state = data
            log_info(f"[FAMA] loaded from {path}: members={len(data['members'])}, threads={len(data['threads'])}")
            return
        except (OSError, ValueError) as e:
            log_warn(f"[FAMA] cannot use {path}: {e!r}")
    fam_state = {
        "title": "Фама",
        "tagline": "",
        "description": "",
        "threads": [dict(default_type) for default_type in FAMA_DEFAULT_THREADS],
        "members": [],
        "relations": [],
    }

load_fam_state()

@web.middleware
async def security_middleware(request, handler):
    real_ip = get_real_ip(request)
    request['real_ip'] = real_ip
    
    current_time = time.time()
    asset_request = is_asset_request(request.path)

    if asset_request:
        asset_entry = assets_rate_limits.get(real_ip)
        if asset_entry is None or current_time > asset_entry["reset"]:
            asset_entry = assets_rate_limits[real_ip] = {"count": 0, "reset": current_time + ASSETS_RATE_LIMIT_WINDOW}
        asset_entry["count"] += 1
        if asset_entry["count"] > ASSETS_RATE_LIMIT_REQUESTS:
            log_throttled(f"assetrate:{real_ip}", f"[RATE] 429 ip={real_ip} exceeded {ASSETS_RATE_LIMIT_REQUESTS} asset requests per {ASSETS_RATE_LIMIT_WINDOW}s (last: {request.method} {request.path!r})")
            return web.Response(status=429, headers={"Retry-After": str(max(1, int(asset_entry["reset"] - current_time) + 1))})
    else:
        if real_ip not in rate_limits:
            rate_limits[real_ip] = {"count": 0, "reset": current_time + RATE_LIMIT_WINDOW}

        if current_time > rate_limits[real_ip]["reset"]:
            rate_limits[real_ip] = {"count": 1, "reset": current_time + RATE_LIMIT_WINDOW}
        else:
            rate_limits[real_ip]["count"] += 1
            if rate_limits[real_ip]["count"] > RATE_LIMIT_REQUESTS:
                log_throttled(f"rate:{real_ip}", f"[RATE] 429 ip={real_ip} exceeded {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s (last: {request.method} {request.path!r})")
                return web.json_response({"error": "Too Many Requests"}, status=429)

    if LOGIN_RATE_LIMIT_REQUESTS > 0 and request.method == "POST" and request.path in AUTH_PATHS:
        entry = auth_rate_limits.get(real_ip)
        if entry is None or current_time > entry["reset"]:
            entry = auth_rate_limits[real_ip] = {"count": 0, "reset": current_time + LOGIN_RATE_LIMIT_WINDOW}
        entry["count"] += 1
        if entry["count"] > LOGIN_RATE_LIMIT_REQUESTS:
            log_throttled(f"authrate:{real_ip}", f"[RATE] 429 ip={real_ip} exceeded {LOGIN_RATE_LIMIT_REQUESTS} auth attempts per {LOGIN_RATE_LIMIT_WINDOW}s ({request.path})")
            return web.json_response({"error": "Too Many Requests"}, status=429)

    ignore_logs = ['/api/version', '/api/limits', '/api/motd', '/api/', '/api', '/api/auth/register', '/api/auth/login']
    if request.path not in ignore_logs and not asset_request:
        log_debug(f"[HTTP] {request.method} {request.path!r} ip={real_ip}")

    note_http_client(request, real_ip)

    if not asset_request and (request.path.startswith('/api/') or request.path.startswith('/assets/')) and request.path not in WS_PATHS and request.path not in VERSION_EXEMPT_PATHS:
        http_version = clean_version(request.headers.get('addon-version'))
        http_status = addon_version_status(http_version)
        if http_status != "ok":
            log_throttled(f"oldhttp:{real_ip}", f"[HTTP] REJECT 426 ip={real_ip} {request.method} {request.path!r} addon-version={http_version or 'none'} status={http_status} (minimum {MIN_ADDON_VERSION_RAW})")
            if http_status != "outdated" and request.path in VANILLA_AUTH_PATHS:
                return web.Response(status=426, text=f"Please download Figura Unchained. Minimum version for your backend is {MIN_ADDON_VERSION_RAW}", content_type="text/plain")
            return web.json_response({"error": f"Please update Figura Unchained. Minimum version for your backend is {MIN_ADDON_VERSION_RAW}"}, status=426)

    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logging.exception(f"[HTTP] Unhandled error in {request.method} {request.path!r} ip={real_ip}")
        if request.path in WS_PATHS:
            raise
        raise web.HTTPInternalServerError()

async def read_auth_body(request):
    try:
        body = await request.json()
    except Exception:
        return None, None, None, "body-is-not-json"
    if not isinstance(body, dict):
        return None, None, None, "body-is-not-an-object"
    client_uuid = body.get("uuid")
    client_hash = body.get("hash")
    raw_username = body.get("username")
    if not isinstance(client_uuid, str) or not isinstance(client_hash, str):
        return None, None, None, "uuid-or-hash-missing"
    if not is_valid_uuid(client_uuid):
        return None, None, None, f"invalid-uuid-format {shown(client_uuid)}"
    if not client_hash:
        return None, None, None, "empty-hash"
    if len(client_hash.encode('utf-8')) > 72:
        return None, None, None, "hash-too-long"
    client_uuid = client_uuid.lower()
    username = None
    if raw_username is not None:
        username = clean_username(raw_username)
        if username is None:
            return None, None, None, f"invalid-username {shown(raw_username)}"
        if canonical_uuid_for(username) != client_uuid:
            return None, None, None, f"uuid-username-mismatch username={username!r} uuid={client_uuid}"
    return client_uuid, username, client_hash, None

async def handle_api_check(request):
    return web.json_response({"status": "ok"})

async def handle_version(request):
    return web.json_response({"release": "0.1.5", "prerelease": "0.1.5", "unchained_api": 1})

async def handle_limits(request):
    return web.json_response({"rate": {"upload": MAX_PING_BPS, "download": MAX_PING_BPS}, "limits": {"maxAvatarSize": MAX_AVATAR_SIZE}})

async def handle_motd(request):
    return web.json_response({"text": "Figura Unchained Backend", "color": "gold"})

def auth_tag(client_uuid, username):
    name = username or username_for_canonical(client_uuid)
    return f"username={name or '?'} canonical_uuid={client_uuid}"

def request_version_text(request):
    version = clean_version(request.headers.get('addon-version'))
    return f"addon-version={version}" if version else "addon-version=none"

async def handle_register(request):
    ip = request['real_ip']
    client_uuid, username, client_hash, reason = await read_auth_body(request)
    if client_uuid is None:
        log_warn(f"[AUTH] register REJECT ip={ip} reason=bad-request: {reason}")
        return web.json_response({"error": "Invalid data format"}, status=400)

    success = await asyncio.to_thread(db_register_sync, client_uuid, username, client_hash, ip)
    if not success:
        log_info(f"[AUTH] register FAIL {auth_tag(client_uuid, username)} ip={ip} reason=already-registered")
        return web.json_response({"error": "Already registered. Please login."}, status=409)

    if username:
        register_known_user(username)
    token = jwt.encode({"uuid": client_uuid, "exp": datetime.now(timezone.utc) + timedelta(days=30)}, JWT_SECRET, algorithm="HS256")
    log_info(f"[AUTH] register OK {auth_tag(client_uuid, username)} ip={ip} {request_version_text(request)}")
    return web.json_response({
        "status": "success",
        "token": token,
        "title": "Malicious or NSFW content is prohibited",
        "message": "Author IPs are logged by backend's owner"
    })

async def handle_login(request):
    ip = request['real_ip']
    client_uuid, username, client_hash, reason = await read_auth_body(request)
    if client_uuid is None:
        log_warn(f"[AUTH] login REJECT ip={ip} reason=bad-request: {reason}")
        return web.json_response({"error": "Invalid data format"}, status=400)

    success = await asyncio.to_thread(db_login_sync, client_uuid, username, client_hash, ip)
    if not success:
        registered = await asyncio.to_thread(db_is_registered_sync, client_uuid)
        why = "wrong-password" if registered else "uuid-not-registered"
        log_info(f"[AUTH] login FAIL {auth_tag(client_uuid, username)} ip={ip} reason={why}")
        return web.json_response({"error": "Invalid password or UUID"}, status=401)

    if username:
        register_known_user(username)
    token = jwt.encode({"uuid": client_uuid, "exp": datetime.now(timezone.utc) + timedelta(days=30)}, JWT_SECRET, algorithm="HS256")
    log_info(f"[AUTH] login OK {auth_tag(client_uuid, username)} ip={ip} {request_version_text(request)}")
    return web.json_response({"status": "success", "token": token})

async def handle_user_profile(request):
    requested = os.path.basename(request.match_info['uuid']).lower()
    target_uuid = resolve_canonical(requested)
    file_path = os.path.join(AVATAR_DIR, f"{target_uuid}.nbt")
    equipped = []

    if os.path.exists(file_path):
        if target_uuid not in hash_cache:
            hash_cache[target_uuid] = await asyncio.to_thread(get_file_hash_sync, file_path)

        equipped.append({"owner": requested, "id": "avatar", "hash": hash_cache[target_uuid]})
        log_debug(f"[PROFILER] Serving profile {display_name(requested)} (requested={requested} canonical_uuid={target_uuid})")

    return web.json_response({"equipped": equipped, "equippedBadges": {"pride": pride_badges_for(target_uuid), "special": []}}, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

async def download_avatar(request):
    requested = os.path.basename(request.match_info['uuid']).lower()
    target_uuid = resolve_canonical(requested)
    file_path = os.path.join(AVATAR_DIR, f"{target_uuid}.nbt")
    if os.path.exists(file_path):
        log_debug(f"[DOWNLOAD] Serving avatar of {display_name(requested)} ({target_uuid}.nbt)")
        return web.FileResponse(file_path, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    return web.json_response({"error": "Not found"}, status=404, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

class GzipValidator:
    def __init__(self, limit):
        self.limit = limit
        self.total = 0
        self.decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
        self.members = 0

    def feed(self, data):
        buf = data
        while buf:
            if self.decomp.eof:
                self.decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
            out = self.decomp.decompress(buf, 262144)
            self.total += len(out)
            if self.total > self.limit:
                return False
            if self.decomp.eof:
                self.members += 1
                buf = self.decomp.unused_data
            else:
                buf = self.decomp.unconsumed_tail
        return True

    def complete(self):
        return self.decomp.eof and self.members > 0 and self.total > 0

async def iter_upload_body(first_chunk, content):
    yield first_chunk
    async for chunk in content.iter_chunked(65536):
        yield chunk

def reject_upload(client_uuid, ip, status, reason, message):
    log_info(f"[UPLOAD] REJECT {status} {user_tag(client_uuid)} ip={ip} reason={reason}")
    return web.json_response({"error": message}, status=status)

async def upload_avatar(request):
    ip = request['real_ip']
    raw_token = request.headers.get('token', '')
    client_uuid = (raw_token.split(':')[0] if ':' in raw_token else raw_token).lower()
    
    if not is_valid_uuid(client_uuid):
        log_warn(f"[UPLOAD] REJECT 400 ip={ip} reason=invalid-token-uuid value={shown(client_uuid)}")
        return web.json_response({"error": "Invalid token"}, status=400)

    username, _ = resolve_client_identity(request, client_uuid)
    ident = username or client_uuid

    authed, auth_reason = auth_status(request, client_uuid)
    if not authed:
        log_info(f"[UPLOAD] REJECT 401 {user_tag(client_uuid)} ip={ip} reason={auth_reason}")
        return web.json_response({"error": "Unauthorized. Please login."}, status=401)
        
    if request.content_length and request.content_length > MAX_AVATAR_SIZE:
        return reject_upload(client_uuid, ip, 413, f"too-large declared={request.content_length} limit={MAX_AVATAR_SIZE}", "File exceeds MAX_AVATAR_SIZE")

    file_path = os.path.join(AVATAR_DIR, f"{client_uuid}.nbt")
    loop = asyncio.get_running_loop()
    
    try:
        first_chunk_data = await request.content.read(65536)
    except Exception as e:
        return reject_upload(client_uuid, ip, 400, f"read-error {e!r}", "Read error")

    if not first_chunk_data:
        return reject_upload(client_uuid, ip, 400, "empty-body", "Empty file")

    if len(first_chunk_data) < 2 or first_chunk_data[:2] != b'\x1f\x8b':
        return reject_upload(client_uuid, ip, 400, "not-gzip", "Invalid format. Only compressed NBT allowed.")
    
    if len(first_chunk_data) > MAX_AVATAR_SIZE:
        return reject_upload(client_uuid, ip, 413, f"too-large size={len(first_chunk_data)} limit={MAX_AVATAR_SIZE}", "File exceeds MAX_AVATAR_SIZE")
    
    tmp_path = f"{file_path}.{secrets.token_hex(6)}.tmp"
    validator = GzipValidator(MAX_AVATAR_UNPACKED_SIZE)
    bytes_written = 0
    error = None

    try:
        with open(tmp_path, 'wb') as f:
            async for chunk in iter_upload_body(first_chunk_data, request.content):
                bytes_written += len(chunk)
                if bytes_written > MAX_AVATAR_SIZE:
                    error = reject_upload(client_uuid, ip, 413, f"too-large stream>{MAX_AVATAR_SIZE}", "File exceeds MAX_AVATAR_SIZE")
                    break
                try:
                    within_limit = await loop.run_in_executor(None, validator.feed, chunk)
                except zlib.error as e:
                    error = reject_upload(client_uuid, ip, 400, f"gzip-corrupt {e}", "Invalid format. Only compressed NBT allowed.")
                    break
                if not within_limit:
                    error = reject_upload(client_uuid, ip, 413, f"unpacked-too-large limit={MAX_AVATAR_UNPACKED_SIZE}", "File exceeds MAX_AVATAR_UNPACKED_SIZE")
                    break
                await loop.run_in_executor(None, f.write, chunk)

        if error is None and not validator.complete():
            error = reject_upload(client_uuid, ip, 400, "gzip-incomplete", "Invalid format. Only compressed NBT allowed.")

        if error is not None:
            return error

        os.replace(tmp_path, file_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    hash_cache[client_uuid] = await asyncio.to_thread(get_file_hash_sync, file_path)
    users_with_avatars.add(client_uuid)
    watchers = await notify_avatar_update(ident, "Skin update")
    log_info(f"[UPLOAD] OK {user_tag(client_uuid)} bytes={bytes_written} watchers={watchers} ip={ip}")

    return web.json_response({"status": "success"})

async def equip_avatar(request):
    return web.json_response({"status": "success"})

async def delete_avatar(request):
    ip = request['real_ip']
    raw_token = request.headers.get('token', '')
    client_uuid = (raw_token.split(':')[0] if ':' in raw_token else raw_token).lower()
    
    if not is_valid_uuid(client_uuid):
        log_warn(f"[DELETE] REJECT 400 ip={ip} reason=invalid-token-uuid value={shown(client_uuid)}")
        return web.json_response({"error": "Invalid token"}, status=400)

    username, _ = resolve_client_identity(request, client_uuid)
    ident = username or client_uuid

    authed, auth_reason = auth_status(request, client_uuid)
    if not authed:
        log_info(f"[DELETE] REJECT 401 {user_tag(client_uuid)} ip={ip} reason={auth_reason}")
        return web.json_response({"error": "Unauthorized. Please login."}, status=401)

    file_path = os.path.join(AVATAR_DIR, f"{client_uuid}.nbt")
    if os.path.exists(file_path):
        await asyncio.to_thread(os.remove, file_path)
        users_with_avatars.discard(client_uuid)
        hash_cache.pop(client_uuid, None)
        watchers = await notify_avatar_update(ident, "Skin deletion")
        log_info(f"[DELETE] OK {user_tag(client_uuid)} watchers={watchers} ip={ip}")
    else:
        log_info(f"[DELETE] {user_tag(client_uuid)} ip={ip} nothing to delete (no avatar stored)")
                        
    return web.json_response({"status": "deleted"})

async def websocket_handler(request):
    real_ip = request['real_ip']
    if ip_connections.get(real_ip, 0) >= WS_MAX_CONNECTIONS_PER_IP:
        log_throttled(f"wsip:{real_ip}", f"[WS] REJECT 429 ip={real_ip} reason=too-many-connections limit={WS_MAX_CONNECTIONS_PER_IP}")
        return web.Response(status=429)

    ip_connections[real_ip] = ip_connections.get(real_ip, 0) + 1
    
    try:
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)
        
        raw_token = request.headers.get('token', '')
        client_uuid = (raw_token.split(':')[0] if ':' in raw_token else raw_token).lower()
        
        if not is_valid_uuid(client_uuid):
            reason = "no-token-header" if not raw_token else "malformed-uuid"
            log_warn(f"[WS] REJECT ip={real_ip} reason={reason} value={shown(client_uuid)}")
            return ws
            
        addon_version = clean_version(request.headers.get('addon-version'))
        version_status = addon_version_status(addon_version)
        if version_status != "ok":
            log_warn(f"[WS] REJECT {user_tag(client_uuid)} ip={real_ip} addon-version={addon_version or 'none'} status={version_status} (minimum {MIN_ADDON_VERSION_RAW}): closing connection")
            await safe_send(ws, UPDATE_TOAST_PACKET)
            await ws.close(code=4100, message=MIN_ADDON_VERSION_RAW.encode('utf-8'))
            return ws

        is_authenticated, auth_reason = auth_status(request, client_uuid)

        username, session_uuid = resolve_client_identity(request, client_uuid)
        ident = username or client_uuid
        tag = user_tag(client_uuid)

        if REQUIRE_AUTH and not is_authenticated and active_auth.get(ident) and ident in active_connections:
            log_warn(f"[WS] REJECT {tag} ip={real_ip} reason=authenticated-session-online (an unauthenticated connection cannot replace it)")
            return ws

        session_note = ""
        registered_session = None
        if username and session_uuid and session_uuid != client_uuid:
            if is_authenticated:
                ok, why = register_session_alias(username, session_uuid)
                if ok:
                    registered_session = session_uuid
                    session_note = f" (session_uuid={session_uuid})"
                else:
                    session_note = f" (session_uuid={session_uuid} REJECTED: {why})"
            else:
                session_note = f" (session_uuid={session_uuid} ignored: not authenticated)"

        world_uuid = read_world_uuid(request)
        registered_world = None
        if username and world_uuid and is_authenticated:
            ok, why = register_world_alias(username, world_uuid)
            if ok and why == "ok":
                registered_world = world_uuid
                session_note += f" (world_uuid={world_uuid})"
            elif not ok:
                session_note += f" (world_uuid={world_uuid} REJECTED: {why})"

        file_path = os.path.join(AVATAR_DIR, f"{client_uuid}.nbt")
        has_avatar = os.path.exists(file_path)
        if has_avatar:
            users_with_avatars.add(client_uuid)
        else:
            users_with_avatars.discard(client_uuid)

        is_registered = None
        if REQUIRE_AUTH and not is_authenticated:
            is_registered = await asyncio.to_thread(db_is_registered_sync, client_uuid)

        previous = active_connections.get(ident)
        if previous is not None and previous is not ws:
            log_warn(f"[WS] duplicate session {tag} ip={real_ip}: an older connection of this player is still registered and is being replaced")

        active_connections[ident] = ws
        active_auth[ident] = bool(is_authenticated)
        connected_at = time.time()

        if not REQUIRE_AUTH:
            auth_text = "disabled"
        elif is_authenticated:
            auth_text = "yes"
        else:
            auth_text = f"NO reason={auth_reason} registered={'yes' if is_registered else 'no'}"
        version_text = f"v{addon_version}" if addon_version else "no-addon-version"
        connection_info[ident] = {
            "username": username or "?",
            "canonical_uuid": client_uuid,
            "aliases": session_note.strip(),
            "version": version_text,
            "ip": real_ip,
            "auth": "disabled" if not REQUIRE_AUTH else ("yes" if is_authenticated else "NO"),
            "reason": auth_reason if REQUIRE_AUTH and not is_authenticated else None,
            "registered": ("yes" if is_registered else "no") if REQUIRE_AUTH and not is_authenticated else None,
            "avatar": has_avatar,
            "connected_at": connected_at,
        }
        log_info(f"[WS] + connect username={username or '?'} canonical_uuid={client_uuid}{session_note}  {version_text} ip={real_ip} auth={auth_text} avatar={'yes' if has_avatar else 'no'} online={len(active_connections)}")
        
        ping_bytes_sec = 0
        ping_reset_time = time.time()
        msg_count = 0
        msg_reset_time = time.time()
        client_subs = 0
        auth_msg = ""
        pings_in = 0
        pings_relayed = 0
        drops = {}

        def note_drop(kind, reason, detail=""):
            drops[reason] = drops.get(reason, 0) + 1
            if drops[reason] == 1:
                log_warn(f"[WS] dropped {kind} {tag} ip={real_ip} reason={reason} {detail} (further drops this session are only counted)")
        
        try:
            await ws.send_bytes(b'\x00')
            
            if REQUIRE_AUTH:
                if not is_authenticated:
                    if is_registered:
                        auth_msg = "Type /figura-unchained login <password>"
                    else:
                        auth_msg = "Type /figura-unchained register <password>"
                    toast_packet = b'\x03\x02' + "Auth Required".encode('utf-8') + b'\x00' + auth_msg.encode('utf-8')
                    await ws.send_bytes(toast_packet)
                else:
                    toast_packet = b'\x03\x00' + "Authenticated".encode('utf-8') + b'\x00' + "Connected successfully.".encode('utf-8')
                    await ws.send_bytes(toast_packet)

            for alias in (registered_session, registered_world):
                if alias and has_avatar:
                    for watcher in list(subscriptions.get(alias, ())):
                        watcher_ws = active_connections.get(watcher)
                        if watcher != ident and watcher_ws is not None:
                            await safe_send(watcher_ws, b'\x02' + uuid.UUID(alias).bytes)
            
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    data = msg.data
                    if not data:
                        continue
                    cmd = data[0]
                    
                    current_time = time.time()
                    if current_time - msg_reset_time > 1.0:
                        msg_count = 0
                        msg_reset_time = current_time
                    msg_count += 1
                    
                    if msg_count > WS_MAX_MESSAGES_PER_SEC:
                        note_drop("message", "message-rate-limit", f"limit={WS_MAX_MESSAGES_PER_SEC}/s")
                        await ws.send_bytes(b'\x05\x01')
                        continue
                    
                    if cmd == 0:
                        log_debug(f"[WS-TOKEN] Ignored token packet from {display_name(ident)}")
                        
                    elif cmd == 1:
                        pings_in += 1
                        if not is_authenticated:
                            note_drop("ping", "not-authenticated", "(player has to /figura-unchained login or register)")
                            toast_packet = b'\x03\x02' + "Auth Required".encode('utf-8') + b'\x00' + auth_msg.encode('utf-8')
                            await ws.send_bytes(toast_packet)
                            continue

                        if client_uuid not in users_with_avatars:
                            note_drop("ping", "no-avatar-uploaded", "(pings are relayed only for players that have an uploaded avatar)")
                            continue
                            
                        if len(data) > MAX_PING_SIZE:
                            note_drop("ping", "ping-too-large", f"size={len(data)} limit={MAX_PING_SIZE}")
                            await ws.send_bytes(b'\x05\x00') 
                            continue
                            
                        if current_time - ping_reset_time > 1.0:
                            ping_bytes_sec = 0
                            ping_reset_time = current_time
                            
                        ping_bytes_sec += len(data)
                        if ping_bytes_sec > MAX_PING_BPS:
                            note_drop("ping", "ping-rate-limit", f"limit={MAX_PING_BPS} B/s")
                            await ws.send_bytes(b'\x05\x01')
                            continue

                        recipients = []
                        for watcher, target_uuid in get_watchers_for_user(ident).items():
                            watcher_ws = active_connections.get(watcher)
                            if watcher_ws is None:
                                continue
                            s2c_packet = data[0:1] + uuid.UUID(target_uuid).bytes + data[1:]
                            if await safe_send(watcher_ws, s2c_packet):
                                recipients.append(display_name(watcher))
                        pings_relayed += 1
                        log_debug(f"[WS-PING] {display_name(ident)} size={len(data)} -> {', '.join(recipients) if recipients else 'nobody'}")
                    
                    elif cmd == 2 and len(data) >= 17:
                        if client_subs >= WS_MAX_SUBS_PER_CLIENT:
                            note_drop("subscribe", "subscription-limit", f"limit={WS_MAX_SUBS_PER_CLIENT}")
                            continue
                        target_uuid = str(uuid.UUID(bytes=data[1:17]))
                        watchers_of_target = subscriptions.setdefault(target_uuid, set())
                        if ident not in watchers_of_target:
                            watchers_of_target.add(ident)
                            client_subs += 1
                        log_debug(f"[WS-SUB] {display_name(ident)} -> {display_name(target_uuid)}")

                        if resolve_canonical(target_uuid) in users_with_avatars:
                            await ws.send_bytes(b'\x02' + uuid.UUID(target_uuid).bytes)
                    
                    elif cmd == 3 and len(data) >= 17:
                        target_uuid = str(uuid.UUID(bytes=data[1:17]))
                        if ident in subscriptions.get(target_uuid, ()):
                            subscriptions[target_uuid].remove(ident)
                            client_subs -= 1
                            if not subscriptions[target_uuid]:
                                del subscriptions[target_uuid]
                            log_debug(f"[WS-UNSUB] {display_name(ident)} -/-> {display_name(target_uuid)}")
                    
                elif msg.type == WSMsgType.ERROR:
                    log_warn(f"[WS] error {tag} ip={real_ip}: {ws.exception()!r}")
        except ConnectionError as e:
            log_info(f"[WS] connection lost {tag} ip={real_ip}: {e!r}")
        except Exception:
            logging.exception(f"[WS] Unhandled error in session {tag} ip={real_ip}")
        finally:
            current = active_connections.get(ident)
            replaced = current is not None and current is not ws
            if current is ws:
                del active_connections[ident]
                active_auth.pop(ident, None)
                connection_info.pop(ident, None)
            
            if not replaced:
                for target in list(subscriptions.keys()):
                    if ident in subscriptions[target]:
                        subscriptions[target].remove(ident)
                        if not subscriptions[target]:
                            del subscriptions[target]
                if registered_session and user_session_uuids.get(username) == registered_session:
                    drop_session_alias(username)
                if registered_world and user_world_uuids.get(username) == registered_world:
                    drop_world_alias(username)
                        
            drops_text = ", ".join(f"{k}={v}" for k, v in drops.items()) or "none"
            log_info(f"[WS] - disconnect {tag} ip={real_ip} time={fmt_dur(time.time() - connected_at)} code={ws.close_code} pings={pings_in} relayed={pings_relayed} drops={drops_text} subs={client_subs} online={len(active_connections)}")
            if replaced:
                log_info(f"[WS] {tag}: closed connection had already been replaced by a newer one; the newer session was left untouched")
    finally:
        ip_connections[real_ip] -= 1
        if ip_connections[real_ip] <= 0:
            del ip_connections[real_ip]

    return ws

async def set_badge(request):
    ip = request['real_ip']
    raw_token = request.headers.get('token', '')
    client_uuid = (raw_token.split(':')[0] if ':' in raw_token else raw_token).lower()

    if not is_valid_uuid(client_uuid):
        log_warn(f"[BADGE] REJECT 400 ip={ip} reason=invalid-token-uuid value={shown(client_uuid)}")
        return web.json_response({"error": "Invalid token"}, status=400)

    username, _ = resolve_client_identity(request, client_uuid)
    ident = username or client_uuid

    authed, auth_reason = auth_status(request, client_uuid)
    if not authed:
        log_info(f"[BADGE] REJECT 401 {user_tag(client_uuid)} ip={ip} reason={auth_reason}")
        return web.json_response({"error": "Unauthorized. Please login."}, status=401)

    if request.content_length is None or request.content_length > 16:
        log_info(f"[BADGE] REJECT 400 {user_tag(client_uuid)} ip={ip} reason=bad-body-size length={request.content_length}")
        return web.json_response({"error": "Invalid badge"}, status=400)

    ok, badge = parse_badge_body(await request.read())
    if not ok:
        log_info(f"[BADGE] REJECT 400 {user_tag(client_uuid)} ip={ip} reason=invalid-badge-id (allowed: null or 0..{MAX_PRIDE_BADGE_ID})")
        return web.json_response({"error": "Invalid badge"}, status=400)

    if user_badges.get(client_uuid) == badge:
        return web.json_response({"status": "success"})

    await asyncio.to_thread(db_set_badge_sync, client_uuid, badge)
    if badge is None:
        user_badges.pop(client_uuid, None)
    else:
        user_badges[client_uuid] = badge

    watchers = await notify_avatar_update(ident, "Badge update", include_self=True)
    action = "CLEAR" if badge is None else f"SET badge={badge}"
    log_info(f"[BADGE] {action} {user_tag(client_uuid)} watchers={watchers} ip={ip}")
    return web.json_response({"status": "success"})

ASSET_HEADERS = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}

async def assets_index(request):
    snapshot = assets_snapshot
    if snapshot is None:
        return web.Response(status=503, text="Assets are not available yet", headers={"Retry-After": "60"})
    body = snapshot["index_json"].get(request.match_info['version'])
    if body is None:
        return web.Response(status=404, text="Unknown assets version")
    log_debug(f"[ASSETS] index {request.match_info['version']} ip={request['real_ip']}")
    return web.Response(body=body, content_type="application/json", headers=ASSET_HEADERS)

async def assets_file(request):
    snapshot = assets_snapshot
    if snapshot is None:
        return web.Response(status=503, text="Assets are not available yet", headers={"Retry-After": "60"})
    version = request.match_info['version']
    relative = request.match_info['path']
    index = snapshot["indexes"].get(version)
    if index is None or relative not in index:
        return web.Response(status=404, text="Not found")
    full = os.path.join(snapshot["directory"], version, *relative.split('/'))
    if not os.path.isfile(full):
        return web.Response(status=404, text="Not found")
    return web.FileResponse(full, headers=ASSET_HEADERS)

async def cleanup_rate_limits(app):
    while True:
        await asyncio.sleep(RATE_LIMIT_CLEANUP_INTERVAL)
        now = time.time()
        expired = [ip for ip, data in rate_limits.items() if now > data["reset"]]
        for ip in expired:
            del rate_limits[ip]
        for ip in [ip for ip, d in auth_rate_limits.items() if now > d["reset"]]:
            del auth_rate_limits[ip]
        for ip in [ip for ip, d in assets_rate_limits.items() if now > d["reset"]]:
            del assets_rate_limits[ip]
        for key in [k for k, v in throttle_state.items() if now - v[0] > 600]:
            del throttle_state[key]

ONLINE_RESULT_ID_RE = re.compile(r'^[0-9a-f]{32}$')

def build_online_snapshot():
    now = time.time()
    players = []
    for info in sorted(connection_info.values(), key=lambda i: i["connected_at"]):
        players.append({
            "username": info["username"],
            "canonical_uuid": info["canonical_uuid"],
            "aliases": info["aliases"],
            "version": info["version"],
            "ip": info["ip"],
            "auth": info["auth"],
            "reason": info["reason"],
            "registered": info["registered"],
            "avatar": "yes" if info["avatar"] else "no",
            "connected_at": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info["connected_at"])),
            "duration": fmt_dur(now - info["connected_at"]),
        })
    return {"online": len(players), "players": players}

def write_admin_result(result_id, payload):
    if not isinstance(result_id, str) or not ONLINE_RESULT_ID_RE.match(result_id):
        logging.warning("[ADMIN] online request without a valid id")
        return
    now = time.time()
    for old in glob.glob("data/.admin_result_*.json"):
        try:
            if now - os.path.getmtime(old) > 60:
                os.remove(old)
        except OSError:
            pass
    final_path = f"data/.admin_result_{result_id}.json"
    tmp_path = final_path + ".tmp"
    save_secure_file(tmp_path, json.dumps(payload))
    os.replace(tmp_path, final_path)

def process_admin_commands():
    commands = []
    for file_path in glob.glob("data/.admin_command_*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            commands.append(data)
        except Exception as e:
            logging.warning(f"[ADMIN] Unreadable command file {file_path}: {e!r}")
        finally:
            try:
                os.remove(file_path)
            except OSError:
                pass
    return commands

async def admin_command_poller(app):
    while True:
        await asyncio.sleep(1)
        commands = await asyncio.to_thread(process_admin_commands)
        
        for data in commands:
            if data.get("token") != ADMIN_SESSION_TOKEN:
                logging.warning("[ADMIN] Invalid session token in command file.")
                continue
                
            b_type = data.get("type")
            if b_type == "online":
                snapshot = build_online_snapshot()
                await asyncio.to_thread(write_admin_result, data.get("id"), snapshot)
                continue
            packet = None
            
            if b_type == "chat":
                msg = data.get("message", "")
                if msg:
                    packet = b'\x04' + msg.encode('utf-8')
                    
            elif b_type == "toast":
                t_type = int(data.get("toast_type", 0))
                title = data.get("title", "")
                desc = data.get("desc", "")
                if title or desc:
                    packet = b'\x03' + bytes([t_type]) + title.encode('utf-8') + b'\0' + desc.encode('utf-8')
            
            if not packet:
                logging.warning(f"[ADMIN] Command {b_type!r} ignored (unknown type or empty content)")

            if packet:
                count = 0
                for ws in list(active_connections.values()):
                    try:
                        await ws.send_bytes(packet)
                        count += 1
                    except Exception as e:
                        logging.warning(f"[ADMIN] Failed to send to a client: {e!r}")
                logging.info(f"[ADMIN] Broadcasted {b_type} to {count} users.")

def log_task_exit(task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logging.error(f"[SYS] Background task {task.get_name()} crashed: {exc!r}", exc_info=exc)

async def start_background_tasks(app):
    app['cleanup_task'] = asyncio.create_task(cleanup_rate_limits(app), name="cleanup_rate_limits")
    app['admin_poller_task'] = asyncio.create_task(admin_command_poller(app), name="admin_command_poller")
    app['cleanup_task'].add_done_callback(log_task_exit)
    app['admin_poller_task'].add_done_callback(log_task_exit)
    app['assets_task'] = asyncio.create_task(assets_sync_loop(app), name="assets_sync")
    app['assets_task'].add_done_callback(log_task_exit)

async def cleanup_background_tasks(app):
    app['cleanup_task'].cancel()
    app['admin_poller_task'].cancel()
    try:
        await app['cleanup_task']
    except asyncio.CancelledError:
        pass
    try:
        await app['admin_poller_task']
    except asyncio.CancelledError:
        pass
    app['assets_task'].cancel()
    try:
        await app['assets_task']
    except asyncio.CancelledError:
        pass

# ======================== Страницы фамы ========================
FAMA_JSON_PLACEHOLDER = '"__FAMA_DATA_PLACEHOLDER__"'

# ======================== Страницы фамы ========================
# ======================== Страницы фамы ========================
# ======================== Страницы фамы ========================
def fam_json_safe(data):
    return json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')

# -------- настройки сайта (панель) --------
FAMA_SETTINGS_PATH = os.path.join("data", "settings.json")
PING_URL_PATH = "PING_URL.txt"
PANEL_DEFAULT_REPO = "MrXerobrinYt/figura-unchained-backend"
PANEL_REPO_RE = re.compile(r'^[A-Za-z0-9._\-]+/[A-Za-z0-9._\-]+$')
PANEL_TOKEN_RE = re.compile(r'^(ghp_|github_pat_)?[A-Za-z0-9_]{20,180}$')
PANEL_URL_RE = re.compile(r'^[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?$')

settings_state = {}

def load_settings_state():
    global settings_state
    try:
        with open(FAMA_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings_state = {
                "host_display": fam_clean_text(data.get("host_display") or "", 253),
                "fama_password": fam_clean_text(data.get("fama_password") or "", 128),
                "ping_url": fam_clean_text(data.get("ping_url") or "", 253),
                "ping_repo": fam_clean_text(data.get("ping_repo") or "", 120),
            }
            return
    except (OSError, ValueError):
        pass
    settings_state = {"host_display": "", "fama_password": "", "ping_url": "", "ping_repo": ""}

def save_settings_state():
    os.makedirs(os.path.dirname(FAMA_SETTINGS_PATH), exist_ok=True)
    payload = {k: settings_state.get(k) or "" for k in ("host_display", "fama_password", "ping_url", "ping_repo")}
    save_secure_file(FAMA_SETTINGS_PATH, json.dumps(payload, ensure_ascii=False, indent=2))

def fama_effective_password():
    return settings_state.get("fama_password") or FAMA_PASSWORD

load_settings_state()


FAMA_PAGE_STYLES = """
:root{--bg:#0b0d13;--panel:rgba(17,19,27,.86);--fg:#e9e6df;--muted:#8f929e;--gold:#d6b35c;--line:#2b2f3d;--accent:#c98a3f}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;overflow:hidden;background:radial-gradient(1100px 700px at 70% 18%,#1a1626 0%,transparent 60%),radial-gradient(900px 600px at 18% 82%,#0e1720 0%,transparent 55%),var(--bg);color:var(--fg);font-family:Georgia,'Times New Roman',system-ui,serif}
#board{position:fixed;inset:0;overflow:hidden;background-image:repeating-linear-gradient(0deg,rgba(255,255,255,.022) 0 1px,transparent 1px 64px),repeating-linear-gradient(90deg,rgba(255,255,255,.022) 0 1px,transparent 1px 64px),repeating-linear-gradient(0deg,rgba(0,0,0,.25) 0 1px,transparent 1px 320px),repeating-linear-gradient(90deg,rgba(0,0,0,.25) 0 1px,transparent 1px 320px);box-shadow:inset 0 0 180px rgba(0,0,0,.75),inset 0 0 60px rgba(0,0,0,.45)}
#threads{position:absolute;inset:0;width:100%;height:100%;z-index:1;pointer-events:none}
.thread-glow{fill:none;opacity:.22;stroke-linecap:round}
.thread-main{fill:none;opacity:.95;stroke-linecap:round}
.knot{opacity:.95}
#threads path.cold,#threads circle.cold{opacity:.1 !important}
#threads path.thread-main.hot{opacity:1}
#threads path.thread-glow.hot{opacity:.5}
#threads circle.hot{opacity:1}
.profile{top:60px;right:14px;width:min(360px,92vw);max-height:70vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 18px 16px;box-shadow:0 16px 40px rgba(0,0,0,.6);font-family:system-ui,sans-serif}
.profile .ph{display:flex;gap:12px;align-items:center;position:relative}
.profile .ph img{width:52px;height:52px;image-rendering:pixelated;border-radius:4px;background:#c9b88f;flex:0 0 auto}
.profile .ava-tile{width:52px;height:52px;line-height:52px;font-size:24px;flex:0 0 auto}
.profile h3{margin:0;color:var(--gold);font-size:19px;word-break:break-word}
.profile .role{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.07em}
.profile .note{white-space:pre-line;font-size:13.5px;line-height:1.6;color:var(--fg);margin:10px 0}
.profile .rel{display:flex;gap:8px;align-items:center;font-size:13px;padding:6px 0;border-top:1px dashed var(--line);color:var(--fg)}
.profile .rel .sw{width:22px;height:3px;border-radius:2px;flex:0 0 auto}
.profile .close{position:absolute;top:0;right:0;background:none;border:1px solid var(--line);border-radius:8px;color:var(--muted);width:28px;height:28px;cursor:pointer;font-size:14px}
.profile .close:hover{color:var(--fg)}
.profile .none{font-size:12.5px;color:var(--muted);margin:8px 0 0}
.photo{position:absolute;width:152px;z-index:2;transform:rotate(var(--tilt,0deg));transform-origin:50% -30px;background:linear-gradient(180deg,#eee4cd,#d8c9a3 70%,#cbb98f);color:#2c210e;border-radius:3px;padding:9px 10px 11px;box-shadow:0 12px 24px rgba(0,0,0,.65),inset 0 0 0 1px rgba(255,255,255,.28);touch-action:none;cursor:grab;user-select:none;-webkit-user-select:none}
.photo.dragging{cursor:grabbing;z-index:3;box-shadow:0 22px 40px rgba(0,0,0,.8),inset 0 0 0 1px rgba(255,255,255,.28)}
.photo::before{content:'';position:absolute;top:-11px;left:50%;transform:translateX(-50%) rotate(var(--tape,-2deg));width:64px;height:20px;background:rgba(214,179,92,.4);border:1px solid rgba(255,255,255,.22);box-shadow:0 2px 5px rgba(0,0,0,.35)}
.photo img{display:block;width:48px;height:48px;margin:0 auto 6px;image-rendering:pixelated;border-radius:3px;background:#c9b88f;box-shadow:0 1px 3px rgba(0,0,0,.4)}
.ava-tile{display:block;width:48px;height:48px;margin:0 auto 6px;border-radius:3px;background:linear-gradient(180deg,#a8793f,#7c5222);color:#f6ecd2;font-size:22px;line-height:48px;text-align:center;font-family:system-ui,sans-serif;text-transform:uppercase}
.mrow .ava-tile{width:28px;height:28px;line-height:28px;font-size:14px;font-weight:600}
.photo b{display:block;font-size:13px;letter-spacing:.03em;color:#3a2a12;text-transform:uppercase;word-break:break-word;text-align:center}
.photo .role{display:block;color:#7a5f33;font-size:9.5px;margin:2px 0 4px;text-transform:uppercase;letter-spacing:.09em;text-align:center}
.photo .note{margin:6px 0 0;white-space:pre-line;font-size:10.5px;line-height:1.4;color:#2c210e;max-height:7.6em;overflow:hidden;text-align:center}
.hud{position:fixed;z-index:5;font-family:system-ui,sans-serif}
.hud button{background:var(--panel);color:var(--gold);border:1px solid var(--line);border-radius:10px;padding:9px 15px;cursor:pointer;font-size:13px}
.hud button.on{background:var(--gold);color:#12131a;border-color:var(--gold)}
.title{top:12px;left:50%;transform:translateX(-50%);text-align:center;pointer-events:none;max-width:70vw}
.title h1{margin:0;font-size:30px;letter-spacing:.14em;color:var(--gold);text-shadow:0 2px 16px rgba(0,0,0,.85)}
.title .tagline{font-style:italic;color:var(--muted);font-size:13px;margin-top:3px;text-shadow:0 1px 8px rgba(0,0,0,.8)}
.btns{top:14px;right:14px;display:flex;gap:8px}
.panel-box{top:60px;right:14px;width:min(380px,92vw);background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 16px 40px rgba(0,0,0,.6)}
.panel-box .desc{white-space:pre-line;line-height:1.62;font-size:14px;color:var(--fg)}
.panel-box .meta{margin-top:12px;font-size:12px;color:var(--muted);line-height:1.7}
.panel-box .meta a{color:var(--accent)}
.panel-box .meta code{background:#14161f;border:1px solid var(--line);border-radius:6px;padding:1px 7px;color:var(--fg)}
.members{top:60px;right:14px;width:min(340px,92vw);max-height:62vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:10px 16px;box-shadow:0 16px 40px rgba(0,0,0,.6)}
.mrow{padding:9px 0;border-bottom:1px dashed var(--line);display:flex;gap:10px;align-items:flex-start}
.mrow:last-child{border-bottom:0}
.mrow img{width:28px;height:28px;image-rendering:pixelated;border-radius:4px;flex:0 0 auto;margin-top:2px}
.mrow .who{min-width:0}
.mrow b{color:var(--gold);font-size:14.5px}
.mrow .role{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.07em;margin-left:6px}
.mrow .note{font-size:12.5px;margin:3px 0 0;white-space:pre-line;color:var(--fg)}
.legend{bottom:14px;left:14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;max-width:52vw;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-size:12.5px;color:var(--muted)}
.legend .sw{display:inline-block;width:26px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.hint{bottom:14px;right:14px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 14px;font-size:11.5px;color:var(--muted);text-align:right;line-height:1.7}
.hint a{color:var(--accent);text-decoration:none}
.hint a:hover{text-decoration:underline}
.hint code{background:#14161f;border:1px solid var(--line);border-radius:6px;padding:1px 6px;color:var(--fg)}
#btnSavePos{background:#2a3d2c;color:#8fd6a0;border-color:#3c5a40}
#btnSavePos:hover{background:#35503a}
@media(max-width:640px){.title h1{font-size:20px}.photo{width:118px}.photo b{font-size:11px}.legend{max-width:56vw;font-size:11px;padding:8px 10px}.hint{display:none}}
"""

FAMA_PAGE_JS = """
var fam = __FAMA_DATA__;

function el(tag, cls, text){ var e = document.createElement(tag); if(cls) e.className = cls; if(text != null) e.textContent = text; return e; }

document.getElementById('fam_title').textContent = fam.title || 'Фама';
var tagEl = document.getElementById('tagline');
tagEl.textContent = fam.tagline || '';
if(!fam.tagline) tagEl.hidden = true;

var infoDesc = el('p', 'desc'); infoDesc.textContent = fam.description || '';
document.getElementById('infoText').appendChild(infoDesc);
document.getElementById('infoMeta').innerHTML = '';
document.getElementById('infoMeta').textContent = '';
document.getElementById('infoMeta').appendChild(document.createTextNode('обновлено: ' + (fam.updated || '—')));

var typeInfo = {};
(fam.threads || []).forEach(function(t){ typeInfo[t.id] = t; });

var board = document.getElementById('board');
var cardEls = {};
var byName = {};
(fam.members || []).forEach(function(m, i){ byName[m.name] = m; });

function redraw(){ drawThreads(); }
function makeAvatar(name){
  var img = new Image();
  img.src = 'https://mc-heads.net/avatar/' + encodeURIComponent(name) + '/64';
  img.onerror = function(){
    if(img.dataset.fallback === '1'){
      var tile = el('span', 'ava-tile');
      tile.textContent = (name || '?').charAt(0).toUpperCase();
      if(img.parentNode){ img.parentNode.replaceChild(tile, img); drawThreads(); }
    } else {
      img.dataset.fallback = '1';
      img.src = 'https://minotar.net/avatar/' + encodeURIComponent(name) + '/64';
    }
  };
  img.onload = function(){ drawThreads(); };
  return img;
}
(fam.members || []).forEach(function(m, i){
  var d = el('div', 'photo');
  d.style.left = (typeof m.x === 'number' ? m.x : 50) + '%';
  d.style.top = (typeof m.y === 'number' ? m.y : 50) + '%';
  d.style.setProperty('--tilt', (((i % 5) - 2) * 0.9) + 'deg');
  d.style.setProperty('--tape', (((i % 3) - 1) * 1.6) + 'deg');
  if(m.name){ d.appendChild(makeAvatar(m.name)); }
  d.appendChild(el('b', null, m.name));
  if(m.role){ d.appendChild(el('span', 'role', m.role)); }
  if(m.note){ d.appendChild(el('p', 'note', m.note)); }
  d.dataset.name = m.name;
  board.appendChild(d);
  cardEls[m.name] = d;
});
window.addEventListener('load', redraw);

var svg = document.getElementById('threads');
var SVGNS = 'http://www.w3.org/2000/svg';
function drawThreads(){
  var W = window.innerWidth, H = window.innerHeight;
  if(!W || !H) return;
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  (fam.relations || []).forEach(function(rel){
    var a = cardEls[rel.from], b = cardEls[rel.to], t = typeInfo[rel.type];
    if(!a || !b) return;
    var color = (t && t.color) || '#c9973f';
    var ax = a.offsetLeft + a.offsetWidth / 2, ay = a.offsetTop - 2;
    var bx = b.offsetLeft + b.offsetWidth / 2, by = b.offsetTop - 2;
    var dx = bx - ax, dy = by - ay;
    var sag = Math.min(60, Math.sqrt(dx*dx + dy*dy) * 0.12);
    var mx = (ax + bx) / 2, my = (ay + by) / 2 + sag;
    var dPath = 'M ' + ax.toFixed(1) + ' ' + ay.toFixed(1) + ' Q ' + mx.toFixed(1) + ' ' + my.toFixed(1) + ' ' + bx.toFixed(1) + ' ' + by.toFixed(1);
    var glow = document.createElementNS(SVGNS, 'path');
    glow.setAttribute('d', dPath);
    glow.setAttribute('class', 'thread-glow');
    glow.setAttribute('stroke', color);
    glow.setAttribute('stroke-width', 7);
    glow.setAttribute('data-a', rel.from);
    glow.setAttribute('data-b', rel.to);
    svg.appendChild(glow);
    var main = document.createElementNS(SVGNS, 'path');
    main.setAttribute('d', dPath);
    main.setAttribute('class', 'thread-main');
    main.setAttribute('stroke', color);
    main.setAttribute('stroke-width', 2.2);
    main.setAttribute('data-a', rel.from);
    main.setAttribute('data-b', rel.to);
    svg.appendChild(main);
    [ [ax, ay], [bx, by] ].forEach(function(pt){
      var knot = document.createElementNS(SVGNS, 'circle');
      knot.setAttribute('cx', pt[0].toFixed(1));
      knot.setAttribute('cy', pt[1].toFixed(1));
      knot.setAttribute('r', 3);
      knot.setAttribute('class', 'knot');
      knot.setAttribute('fill', color);
      knot.setAttribute('data-a', rel.from);
      knot.setAttribute('data-b', rel.to);
      svg.appendChild(knot);
    });
  });
}
drawThreads();
requestAnimationFrame(drawThreads);
window.addEventListener('resize', drawThreads);

/* ---------- drag & drop ---------- */
var dirty = false;
var btnSavePos = document.getElementById('btnSavePos');
function markDirty(){
  if(dirty) return;
  dirty = true;
  btnSavePos.hidden = false;
}
var drag = null;
function clamp(v, lo, hi){ return Math.min(hi, Math.max(lo, v)); }
board.addEventListener('pointerdown', function(e){
  var card = e.target.closest ? e.target.closest('.photo') : null;
  if(!card || !card.dataset.name) return;
  if(e.pointerType === 'mouse' && e.button !== 0) return;
  e.preventDefault();
  var br = board.getBoundingClientRect();
  var r = card.getBoundingClientRect();
  drag = { card: card, br: br, offX: (e.clientX - br.left) - (r.left - br.left), offY: (e.clientY - br.top) - (r.top - br.top), moved: false, sx: e.clientX, sy: e.clientY };
  card.classList.add('dragging');
  try { card.setPointerCapture(e.pointerId); } catch(err) {}
});
window.addEventListener('pointermove', function(e){
  if(!drag) return;
  if(e.clientX !== drag.sx || e.clientY !== drag.sy){ drag.moved = true; }
  var br = drag.br;
  var x = clamp((e.clientX - br.left - drag.offX) / br.width * 100, 2, 96);
  var y = clamp((e.clientY - br.top - drag.offY) / br.height * 100, 4, 88);
  drag.card.style.left = x.toFixed(2) + '%';
  drag.card.style.top = y.toFixed(2) + '%';
  drawThreads();
});
window.addEventListener('pointerup', function(e){
  if(!drag) return;
  drag.card.classList.remove('dragging');
  var m = byName[drag.card.dataset.name];
  if(m){
    m.x = Math.round(parseFloat(drag.card.style.left) * 2) / 2;
    m.y = Math.round(parseFloat(drag.card.style.top) * 2) / 2;
  }
  var wasClick = !drag.moved;
  var name = drag.card.dataset.name;
  drag = null;
  if(wasClick){ openProfile(name); }
  else { markDirty(); }
});

/* ---------- hover highlight ---------- */
function setHover(name){
  var elems = svg.querySelectorAll('path[data-a], circle[data-a]');
  Array.prototype.forEach.call(elems, function(p){
    var hot = !name || p.getAttribute('data-a') === name || p.getAttribute('data-b') === name;
    p.classList.toggle('hot', hot && !!name);
    p.classList.toggle('cold', !!name && !hot);
  });
}
board.addEventListener('pointerover', function(e){
  var c = e.target.closest ? e.target.closest('.photo') : null;
  if(c && c.dataset.name) setHover(c.dataset.name);
});
board.addEventListener('pointerout', function(e){
  var c = e.target.closest ? e.target.closest('.photo') : null;
  if(!c) return;
  var to = e.relatedTarget;
  if(to && to.closest && to.closest('.photo') === c) return;
  setHover(null);
});

/* ---------- member profile ---------- */
var profilePanel = document.getElementById('profilePanel');
function openProfile(name){
  var m = byName[name];
  if(!m) return;
  profilePanel.innerHTML = '';
  var head = el('div', 'ph');
  head.appendChild(makeAvatar(name));
  var ht = el('div');
  ht.appendChild(el('h3', null, name));
  if(m.role) ht.appendChild(el('span', 'role', m.role));
  head.appendChild(ht);
  var close = el('button', 'close', '×'); close.type = 'button';
  close.addEventListener('click', function(){ profilePanel.hidden = true; });
  head.appendChild(close);
  profilePanel.appendChild(head);
  if(m.note){ profilePanel.appendChild(el('p', 'note', m.note)); }
  var relsOf = (fam.relations || []).filter(function(r){ return r.from === name || r.to === name; });
  if(relsOf.length){
    relsOf.forEach(function(r){
      var other = r.from === name ? r.to : r.from;
      var t = typeInfo[r.type];
      var row = el('div', 'rel');
      var sw = el('span', 'sw'); sw.style.background = (t && t.color) || '#c9973f';
      row.appendChild(sw);
      row.appendChild(document.createTextNode(((t && t.label) || r.type) + ': ' + other));
      profilePanel.appendChild(row);
    });
  } else {
    profilePanel.appendChild(el('p', 'none', 'Связей пока нет.'));
  }
  profilePanel.hidden = false;
}

btnSavePos.addEventListener('click', function(){
  var pass = null;
  try { pass = localStorage.getItem('famaPass'); } catch(err) {}
  if(!pass){ pass = prompt('Пароль редактора (FAMA_PASSWORD):'); if(pass === null) return; }
  btnSavePos.textContent = 'Сохранение...';
  var data = {
    title: fam.title, tagline: fam.tagline, description: fam.description,
    members: fam.members, threads: fam.threads, relations: fam.relations
  };
  fetch('/fama/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pass, data: data }) })
    .then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return { status: r.status, json: j }; }); })
    .then(function(r){
      if(r.status === 200){
        try { localStorage.setItem('famaPass', pass); } catch(err) {}
        btnSavePos.textContent = 'Сохранено ✓';
        setTimeout(function(){ btnSavePos.hidden = true; btnSavePos.textContent = 'Сохранить позиции'; dirty = false; }, 1600);
      } else if(r.status === 403){
        btnSavePos.textContent = 'Неверный пароль';
        try { localStorage.removeItem('famaPass'); } catch(err) {}
        setTimeout(function(){ btnSavePos.textContent = 'Сохранить позиции'; }, 1600);
      } else {
        btnSavePos.textContent = 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status);
        setTimeout(function(){ btnSavePos.textContent = 'Сохранить позиции'; }, 2400);
      }
    })
    .catch(function(){ btnSavePos.textContent = 'Сеть недоступна'; setTimeout(function(){ btnSavePos.textContent = 'Сохранить позиции'; }, 2000); });
});

var legend = document.getElementById('legend');
(fam.threads || []).forEach(function(t){
  var item = el('span');
  var sw = el('span', 'sw'); sw.style.background = t.color;
  item.appendChild(sw);
  item.appendChild(document.createTextNode(t.label));
  legend.appendChild(item);
});

var membersPanel = document.getElementById('membersPanel');
(fam.members || []).forEach(function(m){
  var row = el('div', 'mrow');
  if(m.name){ row.appendChild(makeAvatar(m.name)); }
  var who = el('span', 'who');
  who.appendChild(el('b', null, m.name));
  if(m.role){ who.appendChild(el('span', 'role', m.role)); }
  if(m.note){ who.appendChild(el('p', 'note', m.note)); }
  row.appendChild(who);
  membersPanel.appendChild(row);
});

var btnInfo = document.getElementById('btnInfo');
var btnMembers = document.getElementById('btnMembers');
var infoPanel = document.getElementById('infoPanel');
function toggle(which){
  var show = which === 'info';
  infoPanel.hidden = (show ? !infoPanel.hidden : true);
  membersPanel.hidden = (show ? true : !membersPanel.hidden);
  btnInfo.classList.toggle('on', !infoPanel.hidden);
  btnMembers.classList.toggle('on', !membersPanel.hidden);
}
btnInfo.addEventListener('click', function(){ toggle('info'); });
btnMembers.addEventListener('click', function(){ toggle('members'); });
"""

FAMA_PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>__TITLE__ · Доска фамы</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230b0d13'/%3E%3Cpath d='M8 8 Q16 17 24 24' stroke='%23c9973f' stroke-width='2' fill='none'/%3E%3Ccircle cx='8' cy='8' r='4' fill='%23d6b35c'/%3E%3Ccircle cx='24' cy='24' r='4' fill='%23d6b35c'/%3E%3C/svg%3E">
<meta property="og:title" content="__TITLE__ · Доска фамы">
<meta property="og:description" content="__OGDESC__">
<meta property="og:type" content="website">
<meta property="og:image" content="https://__HOST__/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__TITLE__ · Доска фамы">
<meta name="twitter:description" content="__OGDESC__">
<meta name="twitter:image" content="https://__HOST__/og.png">
<style>__STYLES__</style>
</head>
<body>
<div id="board"><svg id="threads"></svg></div>

<div class="hud title">
<h1 id="fam_title"></h1>
<p class="tagline" id="tagline"></p>
</div>

<div class="hud btns">
<button id="btnSavePos" type="button" hidden>Сохранить позиции</button>
<button id="btnMembers" type="button">Участники</button>
<button id="btnInfo" type="button">О фаме</button>
</div>

<div class="hud panel-box" id="infoPanel" hidden>
<div id="infoText"></div>
<div class="meta" id="infoMeta"></div>
<div class="meta">Наш адрес для Figura: <code>__HOST__</code><br><a href="/fama/edit">редактировать доску</a> · <a href="/info">что это за адрес?</a></div>
</div>

<div class="hud members" id="membersPanel" hidden></div>

<div class="hud profile" id="profilePanel" hidden></div>

<div class="hud legend" id="legend"></div>

<div class="hud hint">
Figura Cloud IP: <code>__HOST__</code><br>
карточки можно перетаскивать и нажимать · <a href="/fama/edit">редактировать</a> · <a href="/status">статус</a>
</div>

<script>__JS__</script>
</body>
</html>
"""

FAMA_PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: https://mc-heads.net; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

FAMA_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?$')

async def handle_root(request):
    host_source = settings_state.get("host_display") or request.host
    host = host_source if FAMA_HOST_RE.match(host_source or '') else 'your-server-address'
    og_desc = fam_state.get('tagline') or fam_state.get('description') or 'Доска нашей семьи'
    og_desc = ' '.join(og_desc.split())[:200]
    page = (FAMA_PAGE_TEMPLATE
            .replace('__TITLE__', html.escape(fam_state.get('title') or 'Фама'))
            .replace('__OGDESC__', html.escape(og_desc))
            .replace('__STYLES__', FAMA_PAGE_STYLES)
            .replace('__JS__', FAMA_PAGE_JS)
            .replace('__FAMA_DATA__', fam_json_safe(fam_state))
            .replace('__HOST__', html.escape(host)))
    return web.Response(text=page, content_type='text/html', charset='utf-8', headers={
        "Content-Security-Policy": FAMA_PAGE_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    })

async def handle_fama_data(request):
    return web.json_response(fam_state)

async def handle_fama_save(request):
    if not FAMA_PASSWORD and not settings_state.get("fama_password"):
        return web.json_response({"error": "Editor disabled: set FAMA_PASSWORD in .env or use panel"}, status=503)
    ip = request['real_ip']
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid data"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Invalid data"}, status=400)
    password = body.get("password")
    if not isinstance(password, str) or not secrets.compare_digest(password, fama_effective_password()):
        log_info(f"[FAMA] save REJECT ip={ip} reason=wrong-password")
        return web.json_response({"error": "Wrong password"}, status=403)
    try:
        data = fam_sanitize(body.get("data"))
    except ValueError as e:
        return web.json_response({"error": f"Invalid fam data: {e}"}, status=400)
    data["updated"] = time.strftime('%Y-%m-%d %H:%M')
    fam_state.clear()
    fam_state.update(data)
    try:
        await asyncio.to_thread(save_secure_file, FAMA_DATA_PATH, json.dumps(fam_state, ensure_ascii=False, indent=2))
    except OSError as e:
        log_warn(f"[FAMA] disk save failed: {e!r} (page still serves the new data from memory)")
    log_info(f"[FAMA] save OK members={len(fam_state['members'])} relations={len(fam_state['relations'])} ip={ip}")
    return web.json_response({"status": "success"})

FAMA_EDIT_SCRIPT = """
var fam = __FAMA_DATA__;
var P = document.getElementById('pass');
try { P.value = localStorage.getItem('famaPass') || ''; } catch(e) {}
var M = document.getElementById('members');
var R = document.getElementById('rels');
var T = document.getElementById('types');
var pvBoard = document.getElementById('pvBoard');
var pvSvg = document.getElementById('pvThreads');

var members = (fam.members || []).map(function(m){ return { name: m.name || '', role: m.role || '', note: m.note || '', x: (typeof m.x === 'number' ? m.x : 50), y: (typeof m.y === 'number' ? m.y : 50) }; });
var types = (fam.threads || []).map(function(t){ return { id: t.id, label: t.label || '', color: t.color || '#c9973f' }; });
var rels = (fam.relations || []).map(function(r){ return { from: r.from || '', to: r.to || '', type: r.type || '' }; });

function el(tag, cls, text){ var e = document.createElement(tag); if(cls) e.className = cls; if(text != null) e.textContent = text; return e; }
function opt(label, value){ var o = document.createElement('option'); o.value = value; o.textContent = label; return o; }
function slug(s){ return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, ''); }
function uniqueId(base){
  base = slug(base) || 'thread';
  var id = base, n = 2;
  while(types.some(function(t){ return t.id === id; })){ id = base + '_' + n; n = n + 1; }
  return id;
}
function memberNames(){ return members.filter(function(m){ return m.name; }).map(function(m){ return m.name; }); }
function typeById(id){ for(var i = 0; i < types.length; i++){ if(types[i].id === id) return types[i]; } return null; }

/* ---------- draft ---------- */
var DRAFT_KEY = 'famaDraft';
var draftTimer = null;
function currentData(){
  return {
    title: document.getElementById('title').value,
    tagline: document.getElementById('tagline').value,
    description: document.getElementById('description').value,
    members: members, threads: types, relations: rels
  };
}
function scheduleDraft(){
  if(window.__draftFrozen) return;
  if(draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(function(){
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ ts: Date.now(), data: currentData() })); } catch(e) {}
  }, 400);
}
function clearDraft(){ try { localStorage.removeItem(DRAFT_KEY); } catch(e) {} }
function draftMatches(){
  try {
    var d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    return d && JSON.stringify(d.data) === JSON.stringify(currentData());
  } catch(e){ return true; }
}

document.getElementById('btnRestore').addEventListener('click', function(){
  try {
    var d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    if(d && d.data){ applyLoaded(d.data); document.getElementById('draftbar').hidden = true; }
  } catch(e){}
});
document.getElementById('btnDiscard').addEventListener('click', function(){
  clearDraft(); document.getElementById('draftbar').hidden = true;
});

function applyLoaded(f){
  members = (f.members || []).map(function(m){ return { name: m.name || '', role: m.role || '', note: m.note || '', x: (typeof m.x === 'number' ? m.x : 50), y: (typeof m.y === 'number' ? m.y : 50) }; });
  types = (f.threads || []).map(function(t){ return { id: t.id, label: t.label || '', color: t.color || '#c9973f' }; });
  rels = (f.relations || []).map(function(r){ return { from: r.from || '', to: r.to || '', type: r.type || '' }; });
  document.getElementById('title').value = f.title || '';
  document.getElementById('tagline').value = f.tagline || '';
  document.getElementById('description').value = f.description || '';
  renderMembers(); renderTypes(); renderRels(); changed();
}

function bind(input, obj, key, num){
  input.addEventListener('input', function(){
    obj[key] = num ? Number(input.value) : input.value;
    if(key === 'name'){ renderRels(); }
    changed();
  });
}

/* ---------- preview ---------- */
var pvChips = {};
function changed(){ buildPreview(); scheduleDraft(); }
function buildPreview(){
  Array.prototype.slice.call(pvBoard.querySelectorAll('.pvchip')).forEach(function(c){ c.remove(); });
  pvChips = {};
  members.forEach(function(m, i){
    if(!m.name) return;
    var chip = el('div', 'pvchip');
    chip.style.left = (typeof m.x === 'number' ? m.x : 50) + '%';
    chip.style.top = (typeof m.y === 'number' ? m.y : 50) + '%';
    chip.style.setProperty('--tilt', (((i % 3) - 1) * 1.2) + 'deg');
    chip.dataset.i = i;
    chip.appendChild(el('b', null, m.name));
    if(m.role) chip.appendChild(el('span', 'role', m.role));
    pvBoard.appendChild(chip);
    pvChips[m.name] = chip;
  });
  buildPreviewThreads();
}
function buildPreviewThreads(){
  var W = pvBoard.clientWidth, H = pvBoard.clientHeight;
  if(!W || !H) return;
  pvSvg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  while(pvSvg.firstChild) pvSvg.removeChild(pvSvg.firstChild);
  rels.forEach(function(r){
    var a = pvChips[r.from], b = pvChips[r.to], t = typeById(r.type);
    if(!a || !b) return;
    var ax = a.offsetLeft + a.offsetWidth / 2, ay = a.offsetTop + HAP_TOP;
    var bx = b.offsetLeft + b.offsetWidth / 2, by = b.offsetTop + HAP_TOP;
    var dx = bx - ax, dy = by - ay;
    var sag = Math.min(46, Math.sqrt(dx*dx + dy*dy) * 0.14);
    var mx = (ax + bx) / 2, my = (ay + by) / 2 + sag;
    var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', 'M ' + ax + ' ' + ay + ' Q ' + mx + ' ' + my + ' ' + bx + ' ' + by);
    p.setAttribute('class', 'pvthread');
    p.setAttribute('stroke', (t && t.color) || '#c9973f');
    p.setAttribute('stroke-width', 2);
    pvSvg.appendChild(p);
  });
}
var HAP_TOP = 10;

var pvDrag = null;
function clamp(v, lo, hi){ return Math.min(hi, Math.max(lo, v)); }
pvBoard.addEventListener('pointerdown', function(e){
  var chip = e.target.closest ? e.target.closest('.pvchip') : null;
  if(!chip) return;
  if(e.pointerType === 'mouse' && e.button !== 0) return;
  e.preventDefault();
  var br = pvBoard.getBoundingClientRect();
  var r = chip.getBoundingClientRect();
  pvDrag = { chip: chip, br: br, offX: (e.clientX - br.left) - (r.left - br.left), offY: (e.clientY - br.top) - (r.top - br.top) };
  chip.classList.add('dragging');
  try { chip.setPointerCapture(e.pointerId); } catch(err) {}
});
window.addEventListener('pointermove', function(e){
  if(!pvDrag) return;
  var br = pvDrag.br;
  var x = clamp((e.clientX - br.left - pvDrag.offX) / br.width * 100, 2, 96);
  var y = clamp((e.clientY - br.top - pvDrag.offY) / br.height * 100, 4, 88);
  pvDrag.chip.style.left = x.toFixed(2) + '%';
  pvDrag.chip.style.top = y.toFixed(2) + '%';
  buildPreviewThreads();
});
window.addEventListener('pointerup', function(e){
  if(!pvDrag) return;
  pvDrag.chip.classList.remove('dragging');
  var idx = parseInt(pvDrag.chip.dataset.i, 10);
  if(!isNaN(idx) && members[idx]){
    members[idx].x = Math.round(parseFloat(pvDrag.chip.style.left) * 2) / 2;
    members[idx].y = Math.round(parseFloat(pvDrag.chip.style.top) * 2) / 2;
    var row = M.children[idx];
    if(row){
      var nums = row.querySelectorAll('input[type=number]');
      if(nums.length === 2){ nums[0].value = members[idx].x; nums[1].value = members[idx].y; }
    }
  }
  pvDrag = null;
  changed();
});

/* ---------- members ---------- */
function memberRow(m){
  var row = el('div', 'mrow');
  var del = el('button', 'del', '×'); del.type = 'button'; del.title = 'Убрать участника';
  del.addEventListener('click', function(){
    var i = members.indexOf(m);
    if(i >= 0) members.splice(i, 1);
    rels = rels.filter(function(r){ return r.from !== m.name && r.to !== m.name; });
    renderMembers(); renderRels(); changed();
  });
  row.appendChild(del);
  var name = el('input'); name.placeholder = 'Имя в Minecraft (аватарка подтянется сама)'; name.value = m.name;
  bind(name, m, 'name');
  var role = el('input'); role.placeholder = 'Роль'; role.value = m.role;
  bind(role, m, 'role');
  row.appendChild(name); row.appendChild(role);
  var note = document.createElement('textarea'); note.className = 'span2'; note.rows = 2; note.placeholder = 'Заметка: кто такой, титул, биография...';
  note.value = m.note; bind(note, m, 'note');
  row.appendChild(note);
  var geo = el('div', 'geo');
  var xlab = el('div', 'pos'); xlab.appendChild(document.createTextNode('X')); geo.appendChild(xlab);
  var x = el('input'); x.type = 'number'; x.min = '2'; x.max = '96'; x.step = '0.5'; x.value = m.x; bind(x, m, 'x', true);
  var ylab = el('div', 'pos'); ylab.appendChild(document.createTextNode('Y')); geo.appendChild(ylab);
  var y = el('input'); y.type = 'number'; y.min = '4'; y.max = '88'; y.step = '0.5'; y.value = m.y; bind(y, m, 'y', true);
  geo.appendChild(x); geo.appendChild(y);
  row.appendChild(geo);
  return row;
}
function renderMembers(){
  M.innerHTML = '';
  members.forEach(function(m){ M.appendChild(memberRow(m)); });
  changed();
}
document.getElementById('addMember').addEventListener('click', function(){
  members.push({ name: '', role: '', note: '', x: Math.round(10 + Math.random() * 76), y: Math.round(10 + Math.random() * 72) });
  renderMembers();
  var last = M.lastChild;
  if(last && last.querySelectorAll('input')[1]) last.querySelectorAll('input')[1].focus();
});

/* ---------- threads types ---------- */
function typeRow(t){
  var row = el('div', 'trow');
  var color = el('input'); color.type = 'color'; color.value = t.color;
  color.addEventListener('input', function(){ t.color = color.value; changed(); });
  row.appendChild(color);
  var label = el('input'); label.placeholder = 'Название связи (братья, женаты...)'; label.value = t.label;
  label.addEventListener('input', function(){ t.label = label.value; renderRels(); changed(); });
  row.appendChild(label);
  var del = el('button', 'del', '×'); del.type = 'button'; del.title = 'Убрать тип нити';
  del.addEventListener('click', function(){
    var used = rels.some(function(r){ return r.type === t.id; });
    if(used){ alert('Есть связи этого типа на доске — сначала уберите их.'); return; }
    var i = types.indexOf(t);
    if(i >= 0) types.splice(i, 1);
    renderTypes(); renderRels(); changed();
  });
  row.appendChild(del);
  return row;
}
function renderTypes(){
  T.innerHTML = '';
  types.forEach(function(t){ T.appendChild(typeRow(t)); });
  changed();
}
document.getElementById('addType').addEventListener('click', function(){
  types.push({ id: '', label: 'Новая связь', color: '#' + ('00000' + ((Math.random() * 0xffffff) | 0).toString(16)).slice(-6) });
  types[types.length - 1].id = uniqueId('thread');
  renderTypes();
});

/* ---------- relations ---------- */
function relRow(r){
  var row = el('div', 'rrow');
  var sFrom = el('select'); var sType = el('select'); var sTo = el('select');
  var dot = el('span', 'rel-dot');
  function refreshDot(){
    var t = typeById(r.type);
    dot.style.background = (t && t.color) || '#4a5064';
    dot.title = (t && t.label) || 'тип не выбран';
  }
  sFrom.appendChild(opt('—', ''));
  memberNames().forEach(function(n){ sFrom.appendChild(opt(n, n)); });
  sFrom.value = r.from; if(!sFrom.value) sFrom.selectedIndex = 0;
  sFrom.addEventListener('change', function(){ r.from = sFrom.value; changed(); });
  types.forEach(function(t){ sType.appendChild(opt(t.label || t.id, t.id)); });
  sType.value = r.type; if(!sType.value) sType.selectedIndex = 0;
  sType.addEventListener('change', function(){ r.type = sType.value; refreshDot(); changed(); });
  sTo.appendChild(opt('—', ''));
  memberNames().forEach(function(n){ sTo.appendChild(opt(n, n)); });
  sTo.value = r.to; if(!sTo.value) sTo.selectedIndex = 0;
  sTo.addEventListener('change', function(){ r.to = sTo.value; changed(); });
  var del = el('button', 'del', '×'); del.type = 'button';
  del.addEventListener('click', function(){
    var i = rels.indexOf(r);
    if(i >= 0) rels.splice(i, 1);
    renderRels(); changed();
  });
  row.appendChild(dot);
  row.appendChild(sFrom); row.appendChild(sType); row.appendChild(sTo); row.appendChild(del);
  refreshDot();
  return row;
}
function renderRels(){
  R.innerHTML = '';
  rels.forEach(function(r){ R.appendChild(relRow(r)); });
  changed();
}
document.getElementById('addRel').addEventListener('click', function(){
  var names = memberNames();
  if(names.length < 2){ alert('Сначала добавьте минимум двух участников.'); return; }
  var pair = null;
  outer:
  for(var i = 0; i < names.length; i++){
    for(var j = 0; j < names.length; j++){
      if(i === j) continue;
      var already = rels.some(function(r){ return (r.from === names[i] && r.to === names[j]) || (r.from === names[j] && r.to === names[i]); });
      if(!already){ pair = { from: names[i], to: names[j] }; break outer; }
    }
  }
  if(!pair){ alert('Все пары участников уже связаны. Добавьте участника или уберите связь.'); return; }
  rels.push({ from: pair.from, to: pair.to, type: (types[0] && types[0].id) || '' });
  renderRels(); changed();
});

document.getElementById('title').value = fam.title || '';
document.getElementById('tagline').value = fam.tagline || '';
document.getElementById('description').value = fam.description || '';

/* draft bar */
try {
  var hasDraft = localStorage.getItem(DRAFT_KEY) && JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
  if(hasDraft && !draftMatches()){
    document.getElementById('draftbar').hidden = false;
    window.__draftFrozen = true;
  }
} catch(e) {}
function unlockDraft(){ window.__draftFrozen = false; document.removeEventListener('input', unlockDraft, true); document.removeEventListener('click', unlockDraft, true); }
document.addEventListener('input', unlockDraft, true);
document.addEventListener('click', unlockDraft, true);

document.getElementById('save').addEventListener('click', function(){
  var out = document.getElementById('out');
  out.textContent = 'Сохранение...';
  var data = currentData();
  fetch('/fama/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: P.value, data: data }) })
    .then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return { status: r.status, json: j }; }); })
    .then(function(r){
      if(r.status === 200){
        try { localStorage.setItem('famaPass', P.value); } catch(e) {}
        clearDraft();
        document.getElementById('draftbar').hidden = true;
        out.textContent = 'Сохранено ✓ Доска уже показывает новые данные.';
      }
      else if(r.status === 403){ out.textContent = 'Неверный пароль. Не закрывайте страницу — черновик сохранён в браузере.'; }
      else { out.textContent = 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status); }
    })
    .catch(function(){ out.textContent = 'Сеть недоступна. Не закрывайте страницу — черновик сохранён в браузере.'; });
});

renderTypes();
renderMembers();
renderRels();
buildPreview();
"""

FAMA_EDIT_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Редактор фамы</title>
<style>
:root{--bg:#12141c;--bg2:#191d29;--card:#1e2432;--fg:#e7e9ef;--muted:#9aa1b7;--gold:#d8b45c;--line:#2e3446;--accent:#c9973f}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 500px at 50% -10%,#232a3d,var(--bg)) fixed;color:var(--fg);font-family:system-ui,sans-serif;padding:24px 14px 60px}
main{max-width:900px;margin:0 auto}
h1{color:var(--gold);font-size:26px;margin:0 0 4px}
p.h{color:var(--muted);margin:0 0 16px;font-size:13px}
input,textarea,select{width:100%;background:var(--bg2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:9px 12px;font:inherit;font-size:14px;margin:4px 0}
select{margin-top:2px}
.label{display:block;color:var(--muted);font-size:12px;margin-top:14px;text-transform:uppercase;letter-spacing:.05em}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}
.panel h3{margin:0 0 4px;font-size:15px;color:var(--gold)}
.panel p.sub{margin:0 0 8px;font-size:12px;color:var(--muted)}
.mrow{position:relative;display:grid;grid-template-columns:1.25fr .75fr;gap:6px 8px;border-top:1px dashed var(--line);padding-top:10px;margin-top:10px;padding-right:38px}
.mrow .del{position:absolute;top:12px;right:0;background:var(--bg2);color:#e07070;border:1px solid var(--line);border-radius:8px;width:30px;height:30px;font-size:15px;cursor:pointer}
.span2{grid-column:1 / -1}
.geo{grid-column:1 / -1;display:flex;gap:8px;align-items:center}
.geo input{margin:0}
.geo .pos{width:14px;color:var(--muted);font-size:12px;text-align:right}
.trow{display:grid;grid-template-columns:56px 1fr 32px;gap:8px;align-items:center;border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}
.trow input[type=color]{height:34px;padding:2px;cursor:pointer}
.trow .del,.rrow .del{background:var(--bg2);color:#e07070;border:1px solid var(--line);border-radius:8px;width:30px;height:30px;font-size:15px;cursor:pointer}
.rrow{display:grid;grid-template-columns:12px 1fr 1fr 1fr 32px;gap:8px;align-items:center;border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}
.rel-dot{width:10px;height:10px;border-radius:50%;background:#4a5064;box-shadow:0 0 0 1px rgba(255,255,255,.12)}
.pvwrap{position:sticky;top:0;z-index:2}
.pv{position:relative;width:100%;height:320px;border-radius:10px;background:radial-gradient(600px 300px at 60% 20%,#1c2030,transparent),linear-gradient(180deg,#181b28,#12141c);border:1px solid var(--line);overflow:hidden;box-shadow:inset 0 0 80px rgba(0,0,0,.5)}
.pv svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.pv .pvhint{position:absolute;bottom:6px;right:10px;font-size:10px;color:var(--muted);pointer-events:none}
.pvchip{position:absolute;width:110px;background:linear-gradient(180deg,#eee4cd,#d8c9a3);color:#2c210e;border-radius:3px;padding:5px 7px 6px;font-size:11px;line-height:1.3;cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;transform:rotate(var(--tilt,0deg));transform-origin:50% -8px;box-shadow:0 6px 14px rgba(0,0,0,.55)}
.pvchip.dragging{cursor:grabbing;z-index:3}
.pvchip b{display:block;color:#3a2a12;text-transform:uppercase;word-break:break-word}
.pvchip .role{display:block;color:#7a5f33;font-size:8.5px;text-transform:uppercase;letter-spacing:.06em}
.pvthread{fill:none;stroke-linecap:round;opacity:.95}
.draftbar{background:#332a12;border:1px solid #6a5520;color:#e6cf8f;border-radius:10px;padding:10px 12px;margin:10px 0;font-size:13px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.draftbar button{margin:0;padding:6px 12px;font-size:12.5px}
.draftbar button.ghost{background:#272310;color:#e6cf8f;border-color:#6a5520}
button.ghost{background:var(--bg2);color:var(--gold);border:1px solid var(--line);border-radius:8px;padding:8px 16px;cursor:pointer;margin-top:10px}
#save{display:block;margin:18px auto 0;background:var(--accent);color:#14161f;border:0;border-radius:10px;padding:12px 36px;font-size:15px;font-weight:600;cursor:pointer}
#out{text-align:center;color:var(--muted);margin-top:10px;font-size:13px;min-height:18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
</style>
</head>
<body>
<main>
<h1>Редактор фамы</h1>
<p class="h">Страница для старших фамы. Все правки сразу появляются на <a style="color:var(--accent)" href="/">главной доске</a>.</p>
<div class="draftbar" id="draftbar" hidden><span>Найден несохранённый черновик с прошлого раза.</span><button id="btnRestore" type="button">Восстановить</button><button id="btnDiscard" class="ghost" type="button">Удалить</button></div>
<div class="panel pvwrap">
  <h3>Живое превью доски</h3>
  <p class="sub">Это как будет выглядеть доска. Карточки можно таскать мышью прямо здесь — позиции сохранятся при сохранении.</p>
  <div class="pv" id="pvBoard"><svg id="pvThreads"></svg><span class="pvhint">тащи карточки мышью ✓</span></div>
</div>
<label class="label">Название фамы</label>
<input id="title" placeholder="Фама Ловатьер">
<div class="grid2">
  <div><label class="label">Короткая подпись</label><input id="tagline" placeholder="A Family United"></div>
  <div><label class="label">Пароль редактора</label><input id="pass" type="password" placeholder="FAMA_PASSWORD из настроек Render"></div>
</div>
<label class="label">Описание фамы</label>
<textarea id="description" rows="4" placeholder="История, принципы, вехи..."></textarea>
<div class="panel">
  <h3>Участники</h3>
  <p class="sub">Позицию карточки лучше менять перетаскиванием прямо на доске — здесь цифры X/Y как запасной вариант.</p>
  <div id="members"></div>
  <button class="ghost" id="addMember" type="button">+ Добавить участника</button>
</div>
<div class="panel">
  <h3>Типы нитей</h3>
  <p class="sub">Каждый тип — это цвет нити на доске: кровные, жена, братья, друзья, вражда... Можно добавить свои.</p>
  <div id="types"></div>
  <button class="ghost" id="addType" type="button">+ Добавить тип нити</button>
</div>
<div class="panel">
  <h3>Связи</h3>
  <p class="sub">Выберите двух участников и тип — между ними натянется нить выбранного цвета.</p>
  <div id="rels"></div>
  <button class="ghost" id="addRel" type="button">+ Добавить связь</button>
</div>
<button id="save" type="button">Сохранить</button>
<div id="out"></div>
</main>
<script>__JS__</script>
</body>
</html>
"""

async def handle_fama_edit(request):
    page = (FAMA_EDIT_PAGE
            .replace('__JS__', FAMA_EDIT_SCRIPT)
            .replace('__FAMA_DATA__', fam_json_safe(fam_state)))
    return web.Response(text=page, content_type='text/html', charset='utf-8', headers={
        "Content-Security-Policy": FAMA_PAGE_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    })

# ======================== Панель настроек ========================
PANEL_PAGE_STYLES = """
:root{--bg:#12141c;--bg2:#191d29;--card:#1e2432;--fg:#e7e9ef;--muted:#9aa1b7;--gold:#d8b45c;--line:#2e3446;--accent:#c9973f;--ok:#4caf6d;--err:#e07070}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 500px at 50% -10%,#232a3d,var(--bg)) fixed;color:var(--fg);font-family:system-ui,sans-serif;padding:24px 14px 60px}
main{max-width:760px;margin:0 auto}
h1{color:var(--gold);font-size:26px;margin:0 0 4px}
p.h{color:var(--muted);margin:0 0 16px;font-size:13px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}
.panel h3{margin:0 0 4px;font-size:15px;color:var(--gold)}
.panel p.sub{margin:0 0 8px;font-size:12px;color:var(--muted)}
label.label{display:block;color:var(--muted);font-size:12px;margin:12px 0 2px;text-transform:uppercase;letter-spacing:.05em}
input{width:100%;background:var(--bg2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:9px 12px;font:inherit;font-size:14px;margin:2px 0}
button{background:var(--accent);border:0;border-radius:9px;padding:9px 18px;cursor:pointer;color:#14161f;font-size:14px;font-weight:600;margin-top:10px}
button.ghost{background:var(--bg2);color:var(--gold);border:1px solid var(--line);font-weight:400}
.out{font-size:12.5px;color:var(--muted);margin-top:8px;min-height:16px;white-space:pre-wrap}
.out.ok{color:var(--ok)}.out.err{color:var(--err)}
.info{font-size:12.5px;color:var(--muted);line-height:1.75;margin:4px 0 0}
.info code{background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.info ol{margin:6px 0 0;padding-left:18px}
.hdr{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.hdr a{color:var(--accent);text-decoration:none;font-size:13px}
"""

PANEL_PAGE_JS = """
var P = document.getElementById('pass');
try { P.value = localStorage.getItem('famaPass') || ''; } catch(e) {}
function out(id, text, cls){ var el = document.getElementById(id); el.textContent = text; el.className = 'out ' + (cls || ''); }
function savePassIfNeeded(respStatus){
  if(respStatus === 200){ try { localStorage.setItem('famaPass', P.value); } catch(e) {} }
}
function post(url, body, outId, okText){
  out(outId, 'Сохранение...', '');
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return { status: r.status, json: j }; }); })
    .then(function(r){
      if(r.status === 200){ savePassIfNeeded(200); out(outId, okText || (r.json && r.json.message) || 'Сохранено ✓', 'ok'); }
      else if(r.status === 403){ out(outId, 'Неверный пароль.', 'err'); }
      else { out(outId, 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status), 'err'); }
    })
    .catch(function(){ out(outId, 'Сеть недоступна.', 'err'); });
}

document.getElementById('btnHost').addEventListener('click', function(){
  var v = document.getElementById('hostDisplay').value.trim();
  post('/panel/host', { password: P.value, host_display: v }, 'outHost', v ? 'Адрес сохранён ✓ На доске теперь показывается он.' : 'Сброшено ✓ Доска показывает адрес, который видит браузер.');
});

document.getElementById('btnPassword').addEventListener('click', function(){
  var np = document.getElementById('newPass').value;
  if(!np){ out('outPassword', 'Введите новый пароль.', 'err'); return; }
  post('/panel/password', { password: P.value, new_password: np }, 'outPassword', 'Пароль изменён ✓ Он действует для панели и редактора сразу.');
  document.getElementById('newPass').value = '';
});

document.getElementById('btnPing').addEventListener('click', function(){
  var tok = document.getElementById('token').value.trim();
  var url = document.getElementById('pingUrl').value.trim();
  var repo = document.getElementById('repo').value.trim() || __DEFAULT_REPO__;
  if(!tok){ out('outPing', 'Вставьте GitHub-токен (repo+workflow scope): он не сохраняется на сервере.', 'err'); return; }
  if(!url){ out('outPing', 'Введите адрес для пинга (например https://ваш-домен/api/version).', 'err'); return; }
  out('outPing', 'Обновляю PING_URL.txt в репозитории...', '');
  fetch('/panel/ping', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: P.value, token: tok, url: url, repo: repo }) })
    .then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return { status: r.status, json: j }; }); })
    .then(function(r){
      if(r.status === 200){ out('outPing', 'Готово ✓ Анти-сон теперь пингует: ' + (r.json.ping_url || url), 'ok'); document.getElementById('token').value = ''; }
      else if(r.status === 403){ out('outPing', 'Неверный пароль панели.', 'err'); }
      else { out('outPing', 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status), 'err'); }
    })
    .catch(function(){ out('outPing', 'Сеть недоступна.', 'err'); });
});

document.getElementById('curPing').textContent = 'Сейчас пингуется: ' + (__PING_URL__ || 'адрес по умолчанию из репозитория');
document.getElementById('curHost').textContent = 'Сейчас показывается на доске: ' + (__HOST_DISPLAY__ || '(автоматически — адрес из браузера)');
document.getElementById('curEditorPass').textContent = 'Пароль редактора: задан ' + (__HAS_PASS__ ? '(да)' : '(НЕТ — редактор выключен)');
"""

PANEL_PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Панель настроек · Фама</title>
<style>__STYLES__</style>
</head>
<body>
<main>
<div class="hdr"><h1>Панель настроек</h1><a href="/">← на доску</a></div>
<p class="h">Всё управляется отсюда, без Render и без правки кода. Пароль здесь тот же, что и в редакторе доски.</p>

<div class="panel">
  <h3>Пароль</h3>
  <p class="sub">Пароль от панели и редактора доски.</p>
  <input id="pass" type="password" placeholder="Текущий пароль">
  <label class="label">Новый пароль (не оставляйте пустым)</label>
  <input id="newPass" type="password" placeholder="Новый пароль">
  <button id="btnPassword" type="button">Сменить пароль</button>
  <div class="info" id="curEditorPass"></div>
  <div class="out" id="outPassword"></div>
</div>

<div class="panel">
  <h3>Адрес для доски (домен)</h3>
  <p class="sub">Что показывать на доске как «Figura Cloud IP». Полезно, если игрокам нужно вписывать красивое имя домена.</p>
  <input id="hostDisplay" placeholder="arch-family.indevs.in (пусто — показывать реальный адрес)">
  <button id="btnHost" type="button">Сохранить адрес</button>
  <div class="info" id="curHost"></div>
  <div class="out" id="outHost"></div>
  <div class="info"><b>Как привязать свой домен:</b>
  <ol>
    <li>Render → твой сервис → Settings → Custom Domains → Add Custom Domain → вписать <code>arch-family.indevs.in</code></li>
    <li>Render покажет CNAME-запись — добавь её в панели DNS у поставщика домена (тип: CNAME, имя: <code>arch-family</code>, значение: как показал Render)</li>
    <li>Дождись статуса Verified — HTTPS Render поднимет сам</li>
    <li>Вернись сюда и впиши этот адрес в поле выше — доска сразу покажет его игрокам</li>
  </ol></div>
</div>

<div class="panel">
  <h3>Анти-сон (пинг каждые 5 минут)</h3>
  <p class="sub">GitHub Actions пингует сервер, чтобы он не засыпал. Здесь можно сменить адрес, который пингуется — нужен GitHub-токен с доступом к репозиторию (scope: repo, workflow). Токен нигде не сохраняется.</p>
  <input id="repo" placeholder="репозиторий (по умолчанию MrXerobrinYt/figura-unchained-backend)">
  <label class="label">Новый адрес пинга</label>
  <input id="pingUrl" placeholder="https://arch-family.indevs.in/api/version">
  <label class="label">GitHub-токен</label>
  <input id="token" placeholder="ghp_...">
  <button id="btnPing" type="button">Обновить адрес анти-сна</button>
  <div class="info" id="curPing"></div>
  <div class="out" id="outPing"></div>
</div>

</main>
<script>__JS__</script>
</body>
</html>
"""

async def handle_panel(request):
    if not fama_effective_password():
        return web.Response(status=503, text="Panel disabled: set FAMA_PASSWORD or open /panel after setting a password in .env")
    page = (PANEL_PAGE_TEMPLATE
            .replace('__STYLES__', PANEL_PAGE_STYLES)
            .replace('__JS__', PANEL_PAGE_JS)
            .replace('__DEFAULT_REPO__', json.dumps(PANEL_DEFAULT_REPO))
            .replace('__PING_URL__', json.dumps(settings_state.get('ping_url') or ''))
            .replace('__HOST_DISPLAY__', json.dumps(settings_state.get('host_display') or ''))
            .replace('__HAS_PASS__', json.dumps(bool(fama_effective_password()))))
    return web.Response(text=page, content_type='text/html', charset='utf-8', headers={
        "Content-Security-Policy": FAMA_PAGE_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    })

def panel_unauthorized(ip, reason):
    log_info(f"[PANEL] REJECT ip={ip} reason={reason}")
    return web.json_response({"error": "Wrong password"}, status=403)

async def panel_check(request, body):
    ip = request['real_ip']
    if not isinstance(body, dict):
        return web.json_response({"error": "Invalid data"}, status=400)
    password = body.get("password")
    if not isinstance(password, str) or not secrets.compare_digest(password, fama_effective_password()):
        return panel_unauthorized(ip, "wrong-password")
    return None

async def handle_panel_host(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    deny = await panel_check(request, body)
    if deny:
        return deny
    host = fam_clean_text((body.get("host_display") or "").strip(), 253)
    if host and not FAMA_HOST_RE.match(host):
        return web.json_response({"error": "Invalid host (allowed: letters, digits, dots, dash; optional :port)"}, status=400)
    settings_state["host_display"] = host
    save_settings_state()
    log_info(f"[PANEL] host_display set to {host or '(auto)'} ip={request['real_ip']}")
    return web.json_response({"status": "success", "host_display": host})

async def handle_panel_password(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    deny = await panel_check(request, body)
    if deny:
        return deny
    new_password = fam_clean_text(body.get("new_password") or "", 128)
    if len(new_password) < 8:
        return web.json_response({"error": "Password must be at least 8 characters"}, status=400)
    settings_state["fama_password"] = new_password
    save_settings_state()
    log_info(f"[PANEL] password changed ip={request['real_ip']}")
    return web.json_response({"status": "success"})

async def handle_panel_ping(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    deny = await panel_check(request, body)
    if deny:
        return deny
    ip = request['real_ip']
    token = (body.get("token") or "").strip()
    url = fam_clean_text((body.get("url") or "").strip(), 253)
    repo = fam_clean_text((body.get("repo") or "").strip(), 120) or PANEL_DEFAULT_REPO
    if not PANEL_TOKEN_RE.match(token):
        return web.json_response({"error": "Invalid GitHub token format"}, status=400)
    if not PANEL_REPO_RE.match(repo):
        return web.json_response({"error": "Invalid repository name"}, status=400)
    if not url.startswith("https://") and not url.startswith("http://"):
        return web.json_response({"error": "Ping URL must start with http(s)://"}, status=400)
    if not PANEL_URL_RE.match(url.split("//", 1)[1] if url.startswith(("https://", "http://")) else url):
        return web.json_response({"error": "Invalid ping URL"}, status=400)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "figura-unchained-panel",
        "Content-Type": "application/json",
    }
    api_base = f"https://api.github.com/repos/{repo}/contents/PING_URL.txt"
    async with ClientSession() as session:
        try:
            async with session.get(api_base, headers=headers, timeout=ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    sha = (await resp.json()).get("sha")
                elif resp.status in (401, 403, 404):
                    text = await resp.text()
                    log_warn(f"[PANEL] ping update rejected: status={resp.status} body={text[:120]!r}")
                    return web.json_response({"error": f"GitHub rejected the token or repo is wrong (HTTP {resp.status})"}, status=502)
                else:
                    sha = None
            payload = {
                "message": "panel: update ping url",
                "content": base64.b64encode((url + "\n").encode("utf-8")).decode("ascii"),
            }
            if sha:
                payload["sha"] = sha
            async with session.put(api_base, headers=headers, json=payload, timeout=ClientTimeout(total=30)) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    log_warn(f"[PANEL] ping update failed: status={resp.status} body={text[:120]!r}")
                    return web.json_response({"error": f"GitHub update failed (HTTP {resp.status})"}, status=502)
        except Exception as e:
            log_warn(f"[PANEL] ping update error: {e!r}")
            return web.json_response({"error": "Cannot reach GitHub API"}, status=502)
    settings_state["ping_url"] = url
    settings_state["ping_repo"] = repo
    save_settings_state()
    log_info(f"[PANEL] ping url updated to {url} repo={repo} ip={ip}")
    return web.json_response({"status": "success", "ping_url": url})

# ======================== OG-картинка и статус ========================
SERVER_STARTED_AT = time.time()

def og_png_bytes(width, height, rows):
    """rows: list of RGB bytes, one per row."""
    import struct as _struct
    import zlib as _zlib
    def chunk(tag, data):
        c = _struct.pack(">I", len(data)) + tag + data
        c += _struct.pack(">I", _zlib.crc32(tag + data) & 0xffffffff)
        return c
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", _struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", _zlib.compress(raw, 6))
            + chunk(b"IEND", b""))

async def handle_og(request):
    W, H = 1200, 630
    # --- фон: тёмный градиент + виньетка ---
    xs = [min(1.0, (min(x, W - 1 - x) / 140.0)) * 0.55 + 0.45 for x in range(W)]
    rows = []
    base_rows = []
    for y in range(H):
        fy = y / H
        r0 = int(11 + 14 * fy)
        g0 = int(13 + 10 * (1 - fy))
        b0 = int(19 + 18 * fy)
        row = bytearray(W * 3)
        for x in range(W):
            k = xs[x]
            i = x * 3
            row[i] = int(r0 * k)
            row[i + 1] = int(g0 * k)
            row[i + 2] = int(b0 * k)
        base_rows.append(row)

    def blend(row, x, y, color, alpha):
        if 0 <= x < W and 0 <= y < H:
            i = x * 3
            row[i] = min(255, int(row[i] * (1 - alpha) + color[0] * alpha))
            row[i + 1] = min(255, int(row[i + 1] * (1 - alpha) + color[1] * alpha))
            row[i + 2] = min(255, int(row[i + 2] * (1 - alpha) + color[2] * alpha))

    def disc(row, cx, cy, radius, color, alpha):
        r2 = radius * radius
        for yy in range(max(0, int(cy - radius)), min(H, int(cy + radius) + 1)):
            for xx in range(max(0, int(cx - radius)), min(W, int(cx + radius) + 1)):
                dx, dy = xx - cx, yy - cy
                if dx * dx + dy * dy <= r2:
                    blend(base_rows[yy], xx, yy, color, alpha)

    def rect(x0, y0, w, h, color, alpha=1.0):
        for yy in range(max(0, int(y0)), min(H, int(y0 + h))):
            for xx in range(max(0, int(x0)), min(W, int(x0 + w))):
                blend(base_rows[yy], xx, yy, color, alpha)

    # --- сетка карты ---
    for gx in range(0, W, 80):
        for y in range(H):
            blend(base_rows[y], gx, y, (255, 255, 255), 0.02)
    for gy in range(0, H, 80):
        row = base_rows[gy]
        for x in range(W):
            blend(row, x, gy, (255, 255, 255), 0.02)

    # --- карточки участников ---
    members = fam_state.get("members") or []
    card_pos = []
    for m in members[:8]:
        mx = (float(m.get("x", 50)) / 100.0) * (W - 170) + 10
        my = (float(m.get("y", 50)) / 100.0) * (H - 130) + 10
        rect(mx, my, 150, 110, (40, 30, 12), 0.55)      # тень
        rect(mx, my - 4, 150, 110, (14, 11, 6), 0.9)    # рамка
        rect(mx + 3, my - 1, 144, 104, (232, 220, 190), 1.0)  # бумага
        rect(mx + 12, my + 12, 60, 6, (122, 95, 51), 1.0)     # роль-полоска
        rect(mx + 12, my + 26, 100, 5, (90, 70, 40), 0.8)
        card_pos.append((mx + 75, my - 4))

    # --- нити между карточками ---
    type_colors = {t.get("id"): t.get("color", "#c9973f") for t in (fam_state.get("threads") or [])}
    idx = {m.get("name"): i for i, m in enumerate(members[:8])}
    for rel in (fam_state.get("relations") or []):
        a, b = idx.get(rel.get("from")), idx.get(rel.get("to"))
        if a is None or b is None:
            continue
        hx = type_colors.get(rel.get("type"), "#c9973f")
        try:
            color = tuple(int(hx.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            color = (201, 151, 63)
        ax, ay = card_pos[a]
        bx, by = card_pos[b]
        cx, cy = (ax + bx) / 2, (ay + by) / 2 + 60
        for step in range(121):
            t = step / 120.0
            px = (1 - t) ** 2 * ax + 2 * (1 - t) * t * cx + t ** 2 * bx
            py = (1 - t) ** 2 * ay + 2 * (1 - t) * t * cy + t ** 2 * by
            disc(base_rows, px, py, 7, color, 0.10)
            disc(base_rows, px, py, 2, color, 0.95)

    return web.Response(body=og_png_bytes(W, H, base_rows), content_type="image/png", headers={
        "Cache-Control": "public, max-age=600",
        "X-Content-Type-Options": "nosniff",
    })

STATUS_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Статус · Фама</title>
<style>
:root{--bg:#12141c;--card:#1e2432;--fg:#e7e9ef;--muted:#9aa1b7;--gold:#d8b45c;--line:#2e3446;--ok:#4caf6d;--err:#e07070}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 500px at 50% -10%,#232a3d,var(--bg)) fixed;color:var(--fg);font-family:system-ui,sans-serif;padding:24px 14px 60px}
main{max-width:680px;margin:0 auto}
h1{color:var(--gold);font-size:26px;margin:0 0 16px}
.row{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 16px;margin:10px 0;display:flex;gap:12px;align-items:center}
.dot{width:12px;height:12px;border-radius:50%;flex:0 0 auto;background:var(--muted)}
.dot.ok{background:var(--ok);box-shadow:0 0 8px rgba(76,175,109,.6)}
.dot.err{background:var(--err);box-shadow:0 0 8px rgba(224,112,112,.6)}
.row b{display:block;font-size:14px}
.row span{color:var(--muted);font-size:12.5px}
a{color:#c9973f;text-decoration:none}
a:hover{text-decoration:underline}
</style>
</head>
<body>
<main>
<h1>Статус сервера</h1>
__ROWS__
<p style="color:var(--muted);font-size:12.5px">← <a href="/">на доску</a> · <a href="/fama/edit">редактировать</a> · <a href="/panel">панель настроек</a></p>
</main>
</body>
</html>
"""

def status_row(ok, title, sub):
    cls = "ok" if ok else "err"
    return (f'<div class="row"><span class="dot {cls}"></span>'
            f'<div><b>{html.escape(title)}</b><span>{html.escape(sub)}</span></div></div>')

async def handle_status(request):
    rows = []
    rows.append(status_row(True, "Сервер работает", f"аптайм: {int((time.time() - SERVER_STARTED_AT) / 60)} мин · время сервера: {time.strftime('%Y-%m-%d %H:%M')}"))
    try:
        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{os.environ.get('PORT', '10000')}/api/version", timeout=ClientTimeout(total=5)) as resp:
                rows.append(status_row(resp.status == 200, "API /api/version", f"HTTP {resp.status}"))
    except Exception as e:
        rows.append(status_row(False, "API /api/version", f"недоступен: {e!r}"))
    n_members = len(fam_state.get("members") or [])
    n_rels = len(fam_state.get("relations") or [])
    rows.append(status_row(True, "Доска фамы", f"участников: {n_members} · связей: {n_rels} · обновлено: {fam_state.get('updated') or '—'}"))
    has_pass = bool(fama_effective_password())
    rows.append(status_row(has_pass, "Редактор доски", "пароль задан ✓" if has_pass else "FAMA_PASSWORD не задан — редактор выключен"))
    host_d = settings_state.get("host_display") or ""
    rows.append(status_row(True, "Адрес на доске", host_d if host_d else "автоматический (из браузера)"))
    ping = settings_state.get("ping_url") or ""
    if not ping:
        try:
            with open(PING_URL_PATH, encoding="utf-8") as f:
                ping = f.read().strip()
        except OSError:
            ping = ""
    rows.append(status_row(bool(ping), "Анти-сон (пинг)", ping if ping else "PING_URL.txt не найден"))
    page = STATUS_PAGE.replace("__ROWS__", "".join(rows))
    return web.Response(text=page, content_type="text/html", charset="utf-8", headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache",
    })

ROOT_SCRIPT = """(function(){
var b=document.getElementById('copy'),a=document.getElementById('addr');
b.hidden=false;
function done(ok){
b.textContent=ok?'Copied':'Press Ctrl+C';
setTimeout(function(){b.textContent='Copy'},1600);
}
function legacy(){
var r=document.createRange();
r.selectNodeContents(a);
var s=window.getSelection();
s.removeAllRanges();
s.addRange(r);
var ok=false;
try{ok=document.execCommand('copy')}catch(e){}
done(ok);
}
b.addEventListener('click',function(){
var t=a.textContent.trim();
if(navigator.clipboard&&window.isSecureContext){
navigator.clipboard.writeText(t).then(function(){done(true)},legacy);
}else{
legacy();
}
});
})();"""

ROOT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Not here · Figura Unchained</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%9A%AB%3C/text%3E%3C/svg%3E">
<style>
:root{--bg1:#f6f7fb;--bg2:#e3e7f7;--card:#fff;--fg:#1b1e2b;--muted:#5d6479;--accent:#6c5ce7;--btnfg:#fff;--pill:#f0efff;--border:#dcdff0}
@media (prefers-color-scheme:dark){:root{--bg1:#0e1020;--bg2:#1a1e3d;--card:#1a1d36;--fg:#eceefa;--muted:#9aa1bd;--accent:#a29bfe;--btnfg:#12142b;--pill:#25284a;--border:#2c3058}}
*{box-sizing:border-box}
html,body{min-height:100%;margin:0}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--fg);background:radial-gradient(1200px 600px at 20% 0%,var(--bg2),var(--bg1))}
main{width:100%;max-width:680px;padding:40px 32px;text-align:center;background:var(--card);border:1px solid var(--border);border-radius:20px;box-shadow:0 20px 60px rgba(20,20,60,.18)}
.emoji{font-size:56px;line-height:1}
h1{margin:16px 0 8px;font-size:34px;letter-spacing:-.02em}
p{margin:0 0 14px;color:var(--muted);line-height:1.55}
.row{display:flex;gap:10px;align-items:stretch;margin:22px 0 8px}
.addr{flex:1;min-width:0;margin:0;padding:14px 16px;border:1px dashed var(--accent);border-radius:12px;background:var(--pill);color:var(--accent);font:600 18px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all;text-align:left;user-select:all}
button{padding:0 20px;border:0;border-radius:12px;background:var(--accent);color:var(--btnfg);font:600 15px system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;cursor:pointer;white-space:nowrap}
button:hover{filter:brightness(1.08)}
button:active{transform:translateY(1px)}
.hint{font-size:13px}
</style>
</head>
<body>
<main>
<div class="emoji">🚫</div>
<h1>Not here.</h1>
<p>This address is not a website. Don't open it in a browser. Paste it into the <b>Figura Cloud IP</b> field in Figura's settings instead.</p>
<div class="row"><code class="addr" id="addr">{{HOST}}</code><button type="button" id="copy" hidden>Copy</button></div>
<p class="hint">The Figura Unchained addon must be installed.</p>
</main>
<script>{{SCRIPT}}</script>
</body>
</html>
""".replace('{{SCRIPT}}', ROOT_SCRIPT)

ROOT_SCRIPT_HASH = base64.b64encode(hashlib.sha256(ROOT_SCRIPT.encode('utf-8')).digest()).decode('ascii')

ROOT_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?$')

ROOT_HEADERS = {
    "Content-Security-Policy": f"default-src 'none'; script-src 'sha256-{ROOT_SCRIPT_HASH}'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}

async def handle_info(request):
    host = request.host if ROOT_HOST_RE.match(request.host or '') else 'your-server-address'
    return web.Response(text=ROOT_PAGE.replace('{{HOST}}', host), content_type='text/html', charset='utf-8', headers=ROOT_HEADERS)

app = web.Application(middlewares=[security_middleware], client_max_size=MAX_AVATAR_SIZE)

app.on_startup.append(start_background_tasks)
app.on_cleanup.append(cleanup_background_tasks)

app.router.add_get('/', handle_root)
app.router.add_get('/info', handle_info)
app.router.add_get('/fama.json', handle_fama_data)
app.router.add_post('/fama/save', handle_fama_save)
app.router.add_get('/fama/edit', handle_fama_edit)
app.router.add_get('/panel', handle_panel)
app.router.add_post('/panel/host', handle_panel_host)
app.router.add_post('/panel/password', handle_panel_password)
app.router.add_post('/panel/ping', handle_panel_ping)
app.router.add_get('/status', handle_status)
app.router.add_get('/og.png', handle_og)
app.router.add_get('/api/', handle_api_check)
app.router.add_get('/api', handle_api_check)
app.router.add_get('/api/version', handle_version)
app.router.add_get('/api/limits', handle_limits)
app.router.add_get('/api/motd', handle_motd)
app.router.add_post('/api/auth/register', handle_register)
app.router.add_post('/api/auth/login', handle_login)
app.router.add_get(r'/api/{uuid:[0-9a-fA-F\-]{36}}', handle_user_profile)
app.router.add_get(r'/api/avatar/{uuid:[0-9a-fA-F\-]{36}}', download_avatar)
app.router.add_get(r'/assets/v2/{uuid:[0-9a-fA-F\-]{36}}', download_avatar)
app.router.add_get(r'/api/{uuid:[0-9a-fA-F\-]{36}}/avatar', download_avatar)
app.router.add_put('/api/avatar', upload_avatar)
app.router.add_post('/api/equip', equip_avatar)
app.router.add_post('/api/temp_badges', set_badge)
for assets_prefix in ('/assets', '/api/assets', '/api//assets'):
    app.router.add_get(assets_prefix + r'/{version:v\d+}', assets_index)
    app.router.add_get(assets_prefix + r'/{version:v\d+}/{path:.+}', assets_file)
app.router.add_delete('/api/avatar', delete_avatar)
app.router.add_get('/ws', websocket_handler)
app.router.add_get('/api/ws', websocket_handler)
app.router.add_get('/api//ws', websocket_handler)

def log_startup_summary():
    with db_lock:
        users = db_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    avatars = len(glob.glob(os.path.join(AVATAR_DIR, "*.nbt")))
    log_info(f"[SYS] Assets: repo={ASSETS_REPO}@{ASSETS_REF} update_every={ASSETS_UPDATE_INTERVAL_HOURS}h limit={ASSETS_RATE_LIMIT_REQUESTS}/{ASSETS_RATE_LIMIT_WINDOW}s github_token={'set' if GITHUB_TOKEN else 'not set'} | badges max_id={MAX_PRIDE_BADGE_ID} stored={len(user_badges)}")
    log_info(f"[SYS] Identity: usernames_known={len(user_canonical_uuids)} min_addon_version={MIN_ADDON_VERSION_RAW}")
    log_info(f"[SYS] Starting on {HOST}:{PORT} | debug={DEBUG} require_auth={REQUIRE_AUTH} | registered_users={users} avatars={avatars}")
    log_info(f"[SYS] Limits: avatar={MAX_AVATAR_SIZE}B (unpacked {MAX_AVATAR_UNPACKED_SIZE}B), ping={MAX_PING_SIZE}B/{MAX_PING_BPS}Bps | http={RATE_LIMIT_REQUESTS}/{RATE_LIMIT_WINDOW}s, auth={LOGIN_RATE_LIMIT_REQUESTS}/{LOGIN_RATE_LIMIT_WINDOW}s | ws: {WS_MAX_CONNECTIONS_PER_IP} conn/ip, {WS_MAX_MESSAGES_PER_SEC} msg/s, {WS_MAX_SUBS_PER_CLIENT} subs | trusted_proxies={len(trusted_networks)}")

if __name__ == '__main__':
    try:
        log_startup_summary()
        web.run_app(app, host=HOST, port=PORT, access_log=None, print=None)
        log_info("[SYS] Server stopped")
    except OSError as e:
        if e.errno in (98, 10048):
            print(f"\n[FATAL ERROR] Port {PORT} is already in use!")
            print("HOW TO FIX THIS:")
            print(f"1. Close the program holding port {PORT}")
            print("2. OR open .env, change PORT to a different number")
            print("   and update the 'Server IP' in Figura mod settings to localhost:new_port")
            print("\nExiting...")
            sys.exit(1)
        else:
            raise
