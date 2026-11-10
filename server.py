# ======================== Сайт фамы (собран из блоков) ========================
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
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

def log_info(msg):
    logging.info(msg)

def log_warn(msg):
    logging.warning(msg)

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

def fam_member_uid(name):
    digest = hashlib.sha256(("fama:" + name).encode("utf-8")).hexdigest()
    return str(int(digest, 16) % (10 ** 16)).zfill(16)

def fam_member_number(value, limit):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    if value < 0 or value > limit:
        return 0
    return value

def fam_clean_member(raw):
    if not isinstance(raw, dict):
        raise ValueError("member must be an object")
    name = re.sub(r"\s+", " ", fam_clean_text(raw.get("name"), 48))
    if not name:
        raise ValueError("every member needs a name")
    raw_id = fam_clean_text(raw.get("id"), 20)
    member_id = raw_id if re.match(r"^[0-9]{8,20}$", raw_id or "") else fam_member_uid(name)
    birth = fam_member_number(raw.get("birth"), 2100)
    if birth and birth < 1900:
        birth = 0
    return {
        "id": member_id,
        "name": name,
        "role": fam_clean_text(raw.get("role"), 64),
        "note": fam_clean_text(raw.get("note"), 500),
        "birth": birth or "",
        "generation": fam_member_number(raw.get("generation"), 9),
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
    seen_ids = set()
    for m in members:
        if m["id"] in seen_ids:
            m["id"] = fam_member_uid(m["name"])
        seen_ids.add(m["id"])
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
        "updated": fam_clean_text(data.get("updated"), 32),
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
FAMA_PASSWORD_HASH = os.environ.get("FAMA_PASSWORD_HASH", "").strip()
FAMA_SETTINGS_KEYS = (
    "host_display",
    "fama_password",
    "fama_password_hash",
    "ping_url",
    "ping_repo",
    "server_name",
)

def load_settings_state():
    global settings_state
    try:
        with open(FAMA_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings_state = {
                "host_display": fam_clean_text(data.get("host_display") or "", 253),
                "fama_password": fam_clean_text(data.get("fama_password") or "", 128),
                "fama_password_hash": fam_clean_text(data.get("fama_password_hash") or "", 200),
                "ping_url": fam_clean_text(data.get("ping_url") or "", 253),
                "ping_repo": fam_clean_text(data.get("ping_repo") or "", 120),
                "server_name": fam_clean_text(data.get("server_name") or "", 40),
            }
            return
    except (OSError, ValueError):
        pass
    settings_state = {k: "" for k in FAMA_SETTINGS_KEYS}

def save_settings_state():
    os.makedirs(os.path.dirname(FAMA_SETTINGS_PATH), exist_ok=True)
    payload = {k: settings_state.get(k) or "" for k in FAMA_SETTINGS_KEYS}
    save_secure_file(FAMA_SETTINGS_PATH, json.dumps(payload, ensure_ascii=False, indent=2))

def fama_effective_password():
    """Открытый пароль (для совместимости с FAMA_PASSWORD и старым settings.json)."""
    return settings_state.get("fama_password") or FAMA_PASSWORD

def fama_password_hash_value():
    return (settings_state.get("fama_password_hash") or FAMA_PASSWORD_HASH or "").strip()

def fama_password_configured():
    return bool(fama_password_hash_value() or fama_effective_password())

def fama_check_password(password):
    if not isinstance(password, str):
        return False
    pw_hash = fama_password_hash_value()
    if pw_hash.startswith("$2"):
        try:
            # bcrypt учитывает только первые 72 байта - обрезаем, чтобы не падать на длинных паролях
            return bcrypt.checkpw(password.encode("utf-8")[:72], pw_hash.encode("ascii"))
        except (ValueError, TypeError):
            return False
    expected = fama_effective_password()
    if not expected:
        return False
    return secrets.compare_digest(password, expected)

def fama_server_name():
    return settings_state.get("server_name") or "North"

# -------- роли фамы (единый список для карточек, редактора и Discord-бота) --------
FAMA_ROLES = [
    {"id": "founder", "label": "👑 Founder", "group": "Основной состав",
     "full": "Founder of the Haruka Arch family - основательница семьи Arch", "color": "#c9a2ff"},
    {"id": "deputy", "label": "👑 Deputy Founder", "group": "Основной состав",
     "full": "Deputy Founder - заместитель основательницы семьи", "color": "#b388ff"},
    {"id": "husband", "label": "💍 Муж основательницы", "group": "Основной состав",
     "full": "Муж основательницы семьи", "color": "#9b6bff"},
    {"id": "haruka_arch", "label": "🌹 Haruka Arch", "group": "Основной состав",
     "full": "Участник семьи, выше обычных Arch", "color": "#8a63d6"},
    {"id": "arch", "label": "🌹 Arch", "group": "Основной состав",
     "full": "Участник семьи", "color": "#6a3fb0"},
]

def fama_role_full_map():
    return {r["label"]: r["full"] for r in FAMA_ROLES}

def fama_role_color_map():
    return {r["label"]: r.get("color", "#9b6bff") for r in FAMA_ROLES}

def fama_roles_html():
    groups = []
    seen = []
    for role in FAMA_ROLES:
        if role["group"] not in seen:
            seen.append(role["group"])
    parts = ['<div class="roles">']
    for group in seen:
        parts.append(f'<h4>{html.escape(group)}</h4><ul>')
        for role in FAMA_ROLES:
            if role["group"] != group:
                continue
            parts.append(
                f'<li><b>{html.escape(role["label"])}</b>'
                f'<span>{html.escape(role["full"])}</span></li>'
            )
        parts.append('</ul>')
    parts.append('</div>')
    return "".join(parts)

def fama_roles_options():
    return "".join(
        f'<option value="{html.escape(r["label"])}"></option>' for r in FAMA_ROLES
    )

def fama_public_url():
    return (
        os.environ.get("FAMA_PUBLIC_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        or "https://figura-unchained-backend.onrender.com"
    ).rstrip("/")

fama_write_lock = asyncio.Lock()

async def fama_add_member(name, role, note="", discord_id=""):
    """Добавляет или обновляет участника (используется Discord-ботом)."""
    name = re.sub(r"\s+", " ", fam_clean_text(name, 48))
    if not name:
        return False, "Не указан ник.", ""
    role = fam_clean_text(role, 64)
    note = fam_clean_text(note, 500)
    async with fama_write_lock:
        members = fam_state.get("members") or []
        existing = next(
            (m for m in members if (m.get("name") or "").lower() == name.lower()),
            None,
        )
        if existing:
            return False, f'Ник "{name}" уже есть на доске - выбери другой ник.', ""
        if len(members) >= 200:
            return False, "На доске уже 200 участников - больше нельзя.", ""
        member = {
            "id": fam_member_uid(name),
            "name": name,
            "role": role,
            "note": note,
            "birth": "",
            "generation": 0,
            "x": round(10 + random.random() * 76, 1),
            "y": round(8 + random.random() * 72, 1),
        }
        members.append(member)
        created = True
        data = fam_sanitize(fam_state)
        data["updated"] = time.strftime("%Y-%m-%d %H:%M")
        fam_state.clear()
        fam_state.update(data)
        saved = next(
            (m for m in fam_state["members"] if (m.get("name") or "").lower() == name.lower()),
            None,
        )
        member_id = (saved or {}).get("id") or ""
    try:
        await asyncio.to_thread(
            save_secure_file,
            FAMA_DATA_PATH,
            json.dumps(fam_state, ensure_ascii=False, indent=2),
        )
    except OSError as e:
        log_warn(f"[FAMA] disk save failed (bot): {e!r}")
    schedule_fama_backup()
    safe_name = name.encode("ascii", "replace").decode("ascii")
    safe_role = role.encode("ascii", "replace").decode("ascii")
    log_info(
        f"[BOT] {'added' if created else 'updated'} member={safe_name!r} role={safe_role!r} "
        f"discord={discord_id or '-'}"
    )
    return True, ("добавлен на доску" if created else "карточка обновлена"), member_id

load_settings_state()

# -------- авто-бэкап фамы в GitHub --------
FAMA_GITHUB_TOKEN = os.environ.get("FAMA_GITHUB_TOKEN", "").strip()
FAMA_GITHUB_REPO = os.environ.get("FAMA_GITHUB_REPO", "").strip() or PANEL_DEFAULT_REPO
fam_backup_state = {"last_ok": "", "last_error": ""}

def fama_github_token():
    return FAMA_GITHUB_TOKEN or os.environ.get("GITHUB_TOKEN", "").strip()

async def fama_github_backup():
    token = fama_github_token()
    if not token:
        fam_backup_state["last_error"] = "токен не задан"
        return False
    try:
        with open(FAMA_DATA_PATH, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        fam_backup_state["last_error"] = f"нет файла данных: {e}"
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "figura-unchained-autosave",
        "Content-Type": "application/json",
    }
    api = f"https://api.github.com/repos/{FAMA_GITHUB_REPO}/contents/data/fam.json"
    try:
        async with ClientSession() as session:
            sha = None
            async with session.get(api, headers=headers, timeout=ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    sha = (await resp.json()).get("sha")
                elif resp.status != 404:
                    text = await resp.text()
                    fam_backup_state["last_error"] = f"GitHub GET {resp.status}: {text[:100]}"
                    log_warn(f"[FAMA] backup GET failed status={resp.status}")
                    return False
            payload = {
                "message": f"fam: autosave {time.strftime('%Y-%m-%d %H:%M')}",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }
            if sha:
                payload["sha"] = sha
            async with session.put(api, headers=headers, json=payload, timeout=ClientTimeout(total=20)) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    fam_backup_state["last_error"] = f"GitHub PUT {resp.status}: {text[:100]}"
                    log_warn(f"[FAMA] backup PUT failed status={resp.status}")
                    return False
    except Exception as e:
        fam_backup_state["last_error"] = repr(e)
        log_warn(f"[FAMA] backup error: {e!r}")
        return False
    fam_backup_state["last_ok"] = time.strftime("%Y-%m-%d %H:%M")
    fam_backup_state["last_error"] = ""
    log_info(f"[FAMA] backup OK repo={FAMA_GITHUB_REPO}")
    return True

def schedule_fama_backup():
    if not fama_github_token():
        return
    try:
        asyncio.get_running_loop().create_task(fama_github_backup())
    except RuntimeError:
        pass

# -------- защита от перебора пароля --------
LOGIN_FAIL_LIMIT = 5
LOGIN_FAIL_WINDOW = 15 * 60
LOGIN_BLOCK_SECONDS = 15 * 60
login_guard = {}

def login_block_remaining(ip):
    entry = login_guard.get(ip)
    if not entry:
        return 0
    now = time.time()
    if entry.get("blocked_until", 0) > now:
        return int(entry["blocked_until"] - now)
    if now - entry.get("first", 0) > LOGIN_FAIL_WINDOW:
        login_guard.pop(ip, None)
    return 0

def note_login_failure(ip):
    now = time.time()
    entry = login_guard.get(ip)
    if not entry or now - entry.get("first", 0) > LOGIN_FAIL_WINDOW:
        entry = {"fails": 0, "first": now, "blocked_until": 0}
    entry["fails"] += 1
    if entry["fails"] >= LOGIN_FAIL_LIMIT:
        entry["blocked_until"] = now + LOGIN_BLOCK_SECONDS
        entry["fails"] = 0
        entry["first"] = now
        log_warn(f"[GUARD] ip={ip} blocked for {LOGIN_BLOCK_SECONDS}s after {LOGIN_FAIL_LIMIT} wrong passwords")
    login_guard[ip] = entry

def note_login_success(ip):
    login_guard.pop(ip, None)

def too_many_attempts(ip):
    remaining = login_block_remaining(ip)
    if remaining <= 0:
        return None
    log_info(f"[GUARD] REJECT ip={ip} reason=bruteforce wait={remaining}s")
    minutes = max(1, (remaining + 59) // 60)
    return web.json_response(
        {"error": f"Слишком много попыток ввода пароля. Подождите {minutes} мин."},
        status=429,
        headers={"Retry-After": str(remaining)},
    )


FAMA_PAGE_STYLES = """
:root{--bg:#0a080f;--panel:rgba(18,14,30,.82);--fg:#e8e4f2;--muted:#9a8cc6;--gold:#d8b4ff;--line:#2d2442;--accent:#8b5cf6;--accent-glow:#c084fc;--ink:#0f0a1a;--ok:#4caf6d;--err:#e07070}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;overflow:hidden;background:radial-gradient(1400px 800px at 15% 10%,#2d1f4a 0%,transparent 45%),radial-gradient(900px 700px at 85% 90%,#1a1030 0%,transparent 40%),var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Inter",system-ui,sans-serif}
#board{position:fixed;inset:0;overflow:hidden;background:#1a102e;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='none' stroke='rgba(255,255,255,.02)'/%3E%3C/svg%3E"),radial-gradient(ellipse at 20% 25%,rgba(139,92,246,.15),transparent 55%),radial-gradient(ellipse at 80% 75%,rgba(217,70,239,.12),transparent 50%);background-size:40px 40px,cover,cover;box-shadow:inset 0 0 100px rgba(2,0,10,.4)}
#threads{position:absolute;inset:0;width:100%;height:100%;z-index:1;pointer-events:none}
.thread-glow{fill:none;opacity:.18;stroke-linecap:round}
.thread-main{fill:none;opacity:.95;stroke-linecap:round}
.knot{opacity:.9}
#threads path.cold,#threads circle.cold{opacity:.05 !important}
#threads path.thread-main.hot{opacity:1}
#threads path.thread-glow.hot{opacity:.5}
#threads circle.hot{opacity:1}
.profile{top:12px;right:12px;width:min(380px,92vw);max-height:75vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px 22px 20px;box-shadow:0 30px 60px rgba(0,0,0,.6);backdrop-filter:blur(20px);font-family:system-ui,sans-serif;animation:fadeIn .5s ease-out}
.profile .ph{display:flex;gap:14px;align-items:center;position:relative;margin-bottom:14px}
.profile .ph img{width:56px;height:56px;image-rendering:pixelated;border-radius:10px;background:#cbbce8;flex:0 0 auto;box-shadow:0 6px 16px rgba(0,0,0,.35)}
.profile .ava-tile{width:56px;height:56px;line-height:56px;font-size:26px;flex:0 0 auto;background:linear-gradient(135deg,#8b5cf6,#d946ef);color:#fff;text-align:center;font-weight:700}
.profile h3{margin:0;color:var(--gold);font-size:20px;word-break:break-word}
.profile .role{color:#170b26;font-size:10px;text-transform:uppercase;letter-spacing:.08em;margin-top:6px;padding:3px 12px;border-radius:10px;display:inline-block;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);font-weight:600}
.profile .role-full{display:block;color:var(--muted);font-size:10.5px;line-height:1.45;margin-top:3px;max-width:440px}
.profile .note{white-space:pre-line;font-size:14px;line-height:1.65;color:var(--fg);margin:12px 0 0}
.profile .rel{display:flex;gap:10px;align-items:center;font-size:13.5px;padding:7px 0;border-top:1px solid var(--line);color:var(--fg)}
.profile .rel .sw{width:24px;height:3px;border-radius:2px;flex:0 0 auto}
.profile .close{position:absolute;top:12px;right:12px;background:none;border:1px solid var(--line);border-radius:12px;color:var(--muted);width:32px;height:32px;cursor:pointer;font-size:15px;line-height:1;transition:all .15s}
.profile .close:hover{color:var(--fg);background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.2)}
.profile .none{font-size:12.5px;color:var(--muted);margin:8px 0 0}
.profile .meta-line{font-size:11.5px;color:var(--muted);margin:3px 0 0}
.profile .linkrow{display:flex;gap:8px;margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.profile .linkrow input{flex:1;min-width:0;background:rgba(20,14,30,.5);border:1px solid var(--line);border-radius:10px;color:var(--fg);font:11.5px system-ui,sans-serif;padding:9px 12px}
.profile .linkrow input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,92,246,.15)}
.profile .linkrow button{background:var(--accent);color:#0a080f;border:0;border-radius:10px;padding:0 16px;font:12px system-ui,sans-serif;cursor:pointer;white-space:nowrap;transition:all .15s}
.profile .linkrow button:hover{background:#a78bfa;transform:translateY(-1px);box-shadow:0 8px 20px rgba(139,92,246,.3)}
.photo{position:absolute;width:150px;z-index:2;transform:rotate(var(--tilt,0deg));transform-origin:50% -30px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:var(--fg);border-radius:14px;padding:12px 13px 14px;box-shadow:0 24px 48px rgba(0,0,0,.5),inset 0 0 0 1px rgba(255,255,255,.15);touch-action:none;cursor:grab;user-select:none;-webkit-user-select:none;backdrop-filter:blur(12px);transition:all .2s;animation:fadeIn .5s ease-out both}
.photo.dragging{cursor:grabbing;z-index:4;box-shadow:0 32px 64px rgba(0,0,0,.7),inset 0 0 0 1px rgba(255,255,255,.15);transform:rotate(var(--tilt,0deg)) scale(1.04)}
.photo::before{content:'';position:absolute;top:-10px;left:50%;transform:translateX(-50%) rotate(var(--tape,-2deg));width:58px;height:16px;background:rgba(201,162,255,.2);border:1px solid rgba(255,255,255,.15);box-shadow:0 2px 5px rgba(0,0,0,.3);border-radius:7px}
.photo img{display:block;width:44px;height:44px;margin:0 auto 7px;image-rendering:pixelated;border-radius:6px;background:#cbbce8;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.ava-tile{display:block;width:44px;height:44px;margin:0 auto 7px;border-radius:6px;background:linear-gradient(135deg,#8b5cf6,#d946ef);color:#f4ecff;font-size:20px;line-height:44px;text-align:center;font-family:system-ui,sans-serif;text-transform:uppercase;font-weight:700}
.mrow .ava-tile{width:26px;height:26px;line-height:26px;font-size:14px;font-weight:600}
.photo b{display:block;font-size:12px;letter-spacing:.02em;color:var(--fg);text-transform:uppercase;word-break:break-word;text-align:center;margin:3px 0 2px}
.photo .role{display:inline-block;color:var(--gold);font-size:8.5px;margin:1px 0 3px;text-transform:uppercase;letter-spacing:.05em;text-align:center;padding:1.5px 7px;border-radius:5px;font-weight:800;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2)}
.photo .note{margin:5px 0 0;white-space:pre-line;font-size:9.5px;line-height:1.4;color:var(--muted);max-height:6.5em;overflow:hidden;text-align:center}
.photo .card-badge{position:absolute;top:4px;left:5px;background:rgba(36,18,60,.85);color:#f2e8ff;font:9px/1.5 system-ui,sans-serif;border-radius:5px;padding:0 5px;letter-spacing:.05em;pointer-events:none}
.photo .card-year{position:absolute;top:4px;right:5px;background:rgba(255,255,255,.55);color:#4a2f6e;font:9px/1.5 system-ui,sans-serif;border-radius:5px;padding:0 5px;pointer-events:none}
.hud{position:fixed;z-index:50;font-family:system-ui,sans-serif}
.hud button{background:var(--panel);color:var(--gold);border:1px solid var(--line);border-radius:12px;padding:11px 18px;cursor:pointer;font-size:13px;font-weight:500;transition:all .15s;backdrop-filter:blur(6px)}
.hud button:hover{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.2);color:#e8e4f2;transform:translateY(-1px)}
.hud button.on{background:var(--accent);color:#0a080f;border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,92,246,.2)}
.hud button:active{transform:translateY(0)}
.btns{top:12px;right:12px;display:flex;gap:8px}
.panel-box{top:60px;right:12px;width:min(370px,90vw);background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px 22px;box-shadow:0 30px 60px rgba(0,0,0,.6);backdrop-filter:blur(20px)}
.panel-box .desc{white-space:pre-line;line-height:1.6;font-size:13.5px;color:var(--fg)}
.panel-box .roles{margin-top:16px;border-top:1px solid var(--line);padding-top:12px;max-height:32vh;overflow:auto}
.panel-box .roles h4{margin:10px 0 6px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.panel-box .roles h4:first-child{margin-top:0}
.panel-box .roles ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.panel-box .roles li{font-size:13px;line-height:1.4;color:var(--fg)}
.panel-box .roles li b{color:var(--gold);font-weight:700;display:block}
.panel-box .roles li span{color:var(--muted);font-size:11.5px}
.panel-box .meta{margin-top:14px;font-size:11.5px;color:var(--muted);line-height:1.6}
.panel-box .meta a{color:var(--accent);text-decoration:none}
.panel-box .meta a:hover{text-decoration:underline}
.panel-box .meta code{background:rgba(20,14,30,.6);border:1px solid var(--line);border-radius:7px;padding:1px 7px;color:var(--fg)}
.members{top:60px;right:12px;width:min(360px,90vw);max-height:60vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:14px 18px;box-shadow:0 30px 60px rgba(0,0,0,.6);backdrop-filter:blur(20px)}
.mrow{padding:9px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:flex-start}
.mrow:last-child{border-bottom:0}
.mrow img{width:26px;height:26px;image-rendering:pixelated;border-radius:6px;flex:0 0 auto;margin-top:2px}
.mrow .who{min-width:0}
.mrow b{color:var(--gold);font-size:14px}
.mrow .role{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;margin-left:6px}
.mrow .note{font-size:12px;margin:3px 0 0;white-space:pre-line;color:var(--fg)}
.legend{bottom:12px;left:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;max-width:50vw;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:8px 14px;font-size:12px;color:var(--muted);backdrop-filter:blur(12px)}
.legend .sw{display:inline-block;width:24px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.hint{bottom:12px;right:12px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:8px 14px;font-size:11.5px;color:var(--muted);text-align:right;line-height:1.6;backdrop-filter:blur(12px)}
.hint a{color:var(--accent);text-decoration:none}
.hint a:hover{text-decoration:underline}
.hint code{background:rgba(20,14,30,.6);border:1px solid var(--line);border-radius:7px;padding:1px 6px;color:var(--fg)}
#btnSavePos{background:#2a3d2c;color:#8fd6a0;border-color:#3c5a40}
#btnSavePos:hover{background:#35503a}
#btnAddMember{background:var(--accent);color:#0a080f;border:0;border-radius:12px;padding:9px 16px;font-size:12.5px;font-weight:600;cursor:pointer;transition:all .15s}
#btnAddMember:hover{background:#a78bfa;transform:translateY(-1px);box-shadow:0 8px 20px rgba(139,92,246,.3)}
#btnAddMember:active{transform:translateY(0)}
#btnAddMember.hidden{display:none}
.photo .card-badge{position:absolute;top:4px;left:5px;background:rgba(36,18,60,.85);color:#f2e8ff;font:9px/1.5 system-ui,sans-serif;border-radius:5px;padding:0 5px;letter-spacing:.05em;pointer-events:none}
.photo .card-year{position:absolute;top:4px;right:5px;background:rgba(255,255,255,.55);color:#4a2f6e;font:9px/1.5 system-ui,sans-serif;border-radius:5px;padding:0 5px;pointer-events:none}
.profile .meta-line{font-size:12px;color:var(--muted);margin:3px 0 0}
.profile .linkrow{display:flex;gap:8px;margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.profile .linkrow input{flex:1;min-width:0;background:rgba(20,14,30,.5);border:1px solid var(--line);border-radius:10px;color:var(--fg);font:11.5px system-ui,sans-serif;padding:9px 12px}
.profile .linkrow button{background:var(--accent);color:#0a080f;border:0;border-radius:10px;padding:0 16px;font:12px system-ui,sans-serif;cursor:pointer;white-space:nowrap;transition:all .15s}
.profile .linkrow button:hover{background:#a78bfa;transform:translateY(-1px)}
/* ---------- стартовое окно ---------- */
#gate{position:fixed;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(12,8,24,.85);backdrop-filter:blur(14px);font-family:system-ui,sans-serif}
#gate[hidden]{display:none}
.gate-card{width:min(500px,92vw);background:linear-gradient(180deg,#251b3a,#1a102e);color:#e8e4f2;border:1px solid rgba(255,255,255,.06);border-radius:24px;padding:34px 34px 30px;box-shadow:0 40px 100px rgba(0,0,0,.7)}
.gate-step{font-size:11px;color:#8b5cf6;letter-spacing:.16em;text-transform:uppercase}
.gate-card h2{margin:6px 0 8px;font-size:26px;letter-spacing:.01em;color:#f2e8ff}
.gate-card p{margin:0 0 24px;color:#b8a8d0;font-size:15px;line-height:1.6}
.gate-choices{display:grid;gap:12px}
.gate-opt{display:flex;gap:14px;align-items:center;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:16px 18px;background:rgba(255,255,255,.02);cursor:pointer;font:inherit;color:inherit;text-align:left;transition:all .2s}
.gate-opt:hover{border-color:rgba(139,92,246,.5);background:rgba(255,255,255,.06)}
.gate-opt.on{border-color:#8b5cf6;background:rgba(139,92,246,.12);box-shadow:0 0 0 3px rgba(139,92,246,.18)}
.gate-opt .ico{font-size:28px;flex:0 0 auto;width:36px;text-align:center}
.gate-opt b{display:block;font-size:17px;color:#f2e8ff}
.gate-opt span{display:block;font-size:13px;color:#b8a8d0;margin-top:1px}
.gate-next{margin-top:22px;display:flex;justify-content:space-between;align-items:center;gap:10px}
.gate-next button{background:var(--accent);color:#0a080f;border:0;border-radius:14px;padding:12px 26px;font-size:15px;font-weight:600;cursor:pointer;transition:all .2s}
.gate-next button:hover{transform:translateY(-1px);box-shadow:0 12px 28px rgba(139,92,246,.35)}
.gate-next button:disabled{opacity:.45;cursor:default}
.gate-back{background:none !important;border:0;color:var(--muted)!important;cursor:pointer;font-size:13px;text-decoration:underline;padding:0 !important}
/* ---------- фильтры ---------- */
.chip{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:5px 11px;color:var(--muted);font:11.5px system-ui,sans-serif;cursor:pointer;display:flex;align-items:center;gap:6px}
.chip.on{color:#0a080f;background:var(--gold);border-color:var(--gold);font-weight:600}
.photo.muted{opacity:.4;filter:grayscale(.45)}
#threads path.muted,#threads circle.muted{opacity:.03 !important}
/* ---------- тосты ---------- */
#toast-container{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:200;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast{background:rgba(20,14,30,.94);border:1px solid var(--line);color:var(--fg);padding:10px 16px;border-radius:10px;box-shadow:0 12px 28px rgba(0,0,0,.5);backdrop-filter:blur(10px);font-size:13px;min-width:220px;max-width:340px;text-align:center;opacity:0;transform:translateY(20px);transition:opacity .25s,transform .25s;pointer-events:auto}
.toast.show{opacity:1;transform:translateY(0)}
.toast.ok{border-color:var(--ok);color:#dcfce8}
.toast.err{border-color:var(--err);color:#fecaca}
.toast.warn{border-color:#d97706;color:#fef3c7}
/* ---------- форма быстрого добавления ---------- */
#quick-add-form{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:150;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:26px 30px;box-shadow:0 30px 80px rgba(0,0,0,.6);backdrop-filter:blur(14px);min-width:300px;max-width:90vw;display:none;animation:fadeIn .3s ease-out}
#quick-add-form.hidden{display:none}
#quick-add-form h3{margin:0 0 18px;font-size:19px;color:var(--gold)}
#quick-add-form .label{display:block;color:var(--muted);font-size:11px;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.1em;font-weight:700}
#quick-add-form input,#quick-add-form select{width:100%;background:rgba(20,14,30,.5);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font:inherit;font-size:14px;margin:0}
#quick-add-form input:focus,#quick-add-form select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,92,246,.15)}
#quick-add-form .actions{display:flex;gap:10px;margin-top:22px;justify-content:flex-end}
#quick-add-form button{padding:9px 18px;font-size:13px}
#quick-add-close{background:none;border:0;border-radius:10px;color:var(--muted);cursor:pointer;position:absolute;top:12px;right:12px;font-size:20px;line-height:1;transition:all .15s}
#quick-add-close:hover{color:var(--fg);background:rgba(255,255,255,.06)}
/* ---------- мобильный режим ---------- */
html.m-mode .photo{width:116px}
html.m-mode .photo b{font-size:10.5px}
html.m-mode .btns{top:8px;right:8px;gap:7px;flex-wrap:wrap;justify-content:flex-end;max-width:70vw}
html.m-mode .btns button{padding:7px 10px;font-size:12px}
html.m-mode .legend{max-width:56vw;font-size:11px;padding:7px 12px}
html.m-mode .panel-box,html.m-mode .members,html.m-mode .profile{top:auto;left:8px;right:8px;bottom:8px;width:auto;max-height:56vh;border-radius:16px}
@media(max-width:640px){.title h1{font-size:20px}.photo{width:110px}.photo b{font-size:10px}.legend{max-width:56vw;font-size:10px;padding:6px 10px}}
@keyframes fadeIn{from{opacity:0;transform:scale(.92) rotate(var(--tilt,0deg))}to{opacity:1;transform:scale(1) rotate(var(--tilt,0deg))}}
"""

FAMA_PAGE_JS = """
var fam = __FAMA_DATA__;
var SERVER_NAME = __SERVER_NAME__;
var ROLE_FULL = __ROLES_JSON__;
var ROLE_COLORS = __ROLE_COLORS_JSON__;
var ROMAN = ['','I','II','III','IV','V','VI','VII','VIII','IX'];

function el(tag, cls, text){ var e = document.createElement(tag); if(cls) e.className = cls; if(text != null) e.textContent = text; return e; }

var infoDesc = el('p', 'desc'); infoDesc.textContent = fam.description || '';
document.getElementById('infoText').appendChild(infoDesc);
document.getElementById('infoMeta').innerHTML = '';
document.getElementById('infoMeta').textContent = '';
document.getElementById('infoMeta').appendChild(document.createTextNode('обновлено: ' + (fam.updated || '-')));

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
  if(m.role){
    var roleEl = el('span', 'role', m.role);
    if(ROLE_FULL[m.role]) roleEl.title = ROLE_FULL[m.role];
    roleEl.style.background = ROLE_COLORS[m.role] || '#9b6bff';
    roleEl.style.color = '#1a1029';
    d.appendChild(roleEl);
  }
  if(m.note){ d.appendChild(el('p', 'note', m.note)); }
  if(m.generation){ d.appendChild(el('span', 'card-badge', ROMAN[m.generation] || ('G' + m.generation))); }
  if(m.birth){ d.appendChild(el('span', 'card-year', String(m.birth))); }
  d.dataset.name = m.name;
  if(m.id){ d.dataset.id = m.id; }
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
    glow.setAttribute('data-type', rel.type);
    svg.appendChild(glow);
    var main = document.createElementNS(SVGNS, 'path');
    main.setAttribute('d', dPath);
    main.setAttribute('class', 'thread-main');
    main.setAttribute('stroke', color);
    main.setAttribute('stroke-width', 2.2);
    main.setAttribute('data-a', rel.from);
    main.setAttribute('data-b', rel.to);
    main.setAttribute('data-type', rel.type);
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
      knot.setAttribute('data-type', rel.type);
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
  if(m.role && ROLE_FULL[m.role]) ht.appendChild(el('span', 'role-full', ROLE_FULL[m.role]));
  head.appendChild(ht);
  var close = el('button', 'close', 'x'); close.type = 'button';
  close.addEventListener('click', function(){ profilePanel.hidden = true; });
  head.appendChild(close);
  profilePanel.appendChild(head);
  var meta = [];
  if(m.generation){ meta.push('поколение ' + (ROMAN[m.generation] || m.generation)); }
  if(m.birth){ meta.push(m.birth + ' г.'); }
  if(meta.length){ profilePanel.appendChild(el('div', 'meta-line', meta.join(' - '))); }
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
      row.title = 'Открыть профиль: ' + other;
      row.addEventListener('click', function(){ openProfile(other); });
      profilePanel.appendChild(row);
    });
  } else {
    profilePanel.appendChild(el('p', 'none', 'Связей пока нет.'));
  }
  var link = location.origin + '/member/' + encodeURIComponent(m.id || name);
  var linkRow = el('div', 'linkrow');
  var inp = el('input'); inp.readOnly = true; inp.value = link; inp.title = link;
  var copyBtn = el('button', null, 'Копировать'); copyBtn.type = 'button';
  copyBtn.addEventListener('click', function(){
    function done(){ copyBtn.textContent = 'Готово OK'; setTimeout(function(){ copyBtn.textContent = 'Копировать'; }, 1500); }
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(link).then(done, function(){ inp.select(); });
    } else { inp.select(); try { document.execCommand('copy'); done(); } catch(e) {} }
  });
  linkRow.appendChild(inp); linkRow.appendChild(copyBtn);
  profilePanel.appendChild(linkRow);
  profilePanel.hidden = false;
}

btnSavePos.addEventListener('click', function(){
  var pass = null;
  try { pass = localStorage.getItem('famaPass'); } catch(err) {}
  if(!pass){ pass = prompt('Пароль редактора:'); if(pass === null) return; }
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
        btnSavePos.textContent = 'Сохранено OK';
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

/* ---------- тосты-уведомления ---------- */
var toastContainer = document.getElementById('toast-container');
function showToast(text, type){
  if(!toastContainer) return;
  var t = document.createElement('div');
  t.className = 'toast ' + (type || '');
  t.textContent = text;
  toastContainer.appendChild(t);
  requestAnimationFrame(function(){ t.classList.add('show'); });
  setTimeout(function(){
    t.classList.remove('show');
    setTimeout(function(){ if(t.parentNode) t.parentNode.removeChild(t); }, 250);
  }, 3000);
}

/* ---------- быстрое добавление участника с доски ---------- */
var btnAddMember = document.getElementById('btnAddMember');
var quickForm = document.getElementById('quick-add-form');
var qaClose = document.getElementById('quick-add-close');
var qaCancel = document.getElementById('qa-cancel');
var qaSave = document.getElementById('qa-save');
var qaName = document.getElementById('qa-name');
var qaRole = document.getElementById('qa-role');

function openQuickAdd(){
  qaName.value = '';
  qaRole.innerHTML = '';
  (fam.roles || []).forEach(function(r){
    var o = document.createElement('option');
    o.value = r.label;
    o.textContent = r.label;
    qaRole.appendChild(o);
  });
  quickForm.classList.remove('hidden');
  qaName.focus();
}
function closeQuickAdd(){
  quickForm.classList.add('hidden');
}
btnAddMember.addEventListener('click', openQuickAdd);
qaClose.addEventListener('click', closeQuickAdd);
qaCancel.addEventListener('click', closeQuickAdd);

qaSave.addEventListener('click', function(){
  var name = qaName.value.trim();
  if(!name){ showToast('Введите ник', 'err'); return; }
  var role = qaRole.value || '';
  var pass = null;
  try { pass = localStorage.getItem('famaPass'); } catch(err) {}
  if(!pass){ pass = prompt('Пароль редактора:'); if(pass === null) return; }
  qaSave.textContent = 'Добавление...';
  qaSave.disabled = true;
  var newMember = {
    id: 'new-' + Date.now(),
    name: name,
    role: role,
    note: '',
    birth: '',
    generation: 0,
    x: Math.round(10 + Math.random() * 76),
    y: Math.round(10 + Math.random() * 76)
  };
  var data = {
    title: fam.title, tagline: fam.tagline, description: fam.description,
    members: (fam.members || []).concat([newMember]),
    threads: fam.threads || [],
    relations: fam.relations || []
  };
  fetch('/fama/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pass, data: data }) })
    .then(function(r){ return r.json().catch(function(){ return {}; }).then(function(j){ return { status: r.status, json: j }; }); })
    .then(function(r){
      qaSave.textContent = 'Добавить';
      qaSave.disabled = false;
      if(r.status === 200){
        try { localStorage.setItem('famaPass', pass); } catch(err) {}
        closeQuickAdd();
        location.reload();
      } else if(r.status === 403){
        showToast('Неверный пароль', 'err');
        try { localStorage.removeItem('famaPass'); } catch(err) {}
      } else {
        showToast('Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status), 'err');
      }
    })
    .catch(function(){ qaSave.textContent = 'Добавить'; qaSave.disabled = false; showToast('Сеть недоступна', 'err'); });
});

window.addEventListener('keydown', function(e){
  if(e.key === 'Escape' && !quickForm.classList.contains('hidden')) closeQuickAdd();
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

/* ---------- стартовый экран ---------- */
var gate = document.getElementById('gate');
var gateStepEl = document.getElementById('gateStep');
var gateTitle = document.getElementById('gateTitle');
var gateSub = document.getElementById('gateSub');
var gateChoices = document.getElementById('gateChoices');
var gateNextBtn = document.getElementById('gateNext');
var gateBackBtn = document.getElementById('gateBack');
var gateStep = 0;
var gateMode = null;
var gateServer = SERVER_NAME;
var MODE_KEY = 'famaMode';
var SERVER_KEY = 'famaServer';
function readStore(k){ try { return localStorage.getItem(k); } catch(e){ return null; } }
function writeStore(k, v){ try { localStorage.setItem(k, v); } catch(e) {} }
function applyModeClass(mode){ document.documentElement.classList.toggle('m-mode', mode === 'mobile'); }
function autoMode(){
  try { return (window.matchMedia && window.matchMedia('(max-width: 760px)').matches) ? 'mobile' : 'pc'; } catch(e){ return 'pc'; }
}
function renderGate(){
  gateChoices.innerHTML = '';
  if(gateStep === 0){
    gateStepEl.textContent = 'Шаг 1 из 2';
    gateTitle.textContent = 'Как смотришь?';
    gateSub.textContent = 'Выбери версию сайта - её можно будет поменять в любой момент.';
    var opts = [
      { v: 'pc', ico: 'PC', t: 'ПК-версия', s: 'Полная доска, панели сбоку, удобно мышью' },
      { v: 'mobile', ico: 'M', t: 'Мобильная версия', s: 'Компактные карточки, панели снизу, под палец' }
    ];
    opts.forEach(function(o){
      var b = el('button', 'gate-opt' + (gateMode === o.v ? ' on' : ''));
      b.type = 'button';
      b.appendChild(el('span', 'ico', o.ico));
      var tx = el('span');
      tx.appendChild(el('b', null, o.t));
      tx.appendChild(el('span', null, o.s));
      b.appendChild(tx);
      b.addEventListener('click', function(){ gateMode = o.v; renderGate(); });
      gateChoices.appendChild(b);
    });
    gateNextBtn.textContent = 'Дальше';
    gateNextBtn.disabled = !gateMode;
    gateBackBtn.hidden = true;
  } else {
    gateStepEl.textContent = 'Шаг 2 из 2';
    gateTitle.textContent = 'На каком сервере Харука-Арч?';
    gateSub.textContent = 'Пока доступен один сервер - "North". Другие появятся позже.';
    var sb = el('button', 'gate-opt on'); sb.type = 'button';
    sb.appendChild(el('span', 'ico', 'P'));
    var st = el('span');
    st.appendChild(el('b', null, SERVER_NAME));
    st.appendChild(el('span', null, 'Основной сервер Харука-Арч'));
    sb.appendChild(st);
    sb.addEventListener('click', function(){ gateServer = SERVER_NAME; renderGate(); });
    gateChoices.appendChild(sb);
    gateNextBtn.textContent = 'На доску';
    gateNextBtn.disabled = false;
    gateBackBtn.hidden = false;
  }
}
function openGate(step){
  gateStep = step || 0;
  renderGate();
  gate.hidden = false;
}
function closeGate(){
  gate.hidden = true;
  if(gateMode){ writeStore(MODE_KEY, gateMode); applyModeClass(gateMode); }
  if(gateServer){ writeStore(SERVER_KEY, gateServer); }
  var srvEl = document.getElementById('srvName');
  if(srvEl){ srvEl.textContent = gateServer || SERVER_NAME; }
}
gateNextBtn.addEventListener('click', function(){
  if(gateStep === 0){ gateStep = 1; renderGate(); return; }
  closeGate();
});
gateBackBtn.addEventListener('click', function(){ gateStep = 0; renderGate(); });
var gateChange = document.getElementById('gateChange');
if(gateChange){
  gateChange.addEventListener('click', function(e){
    e.preventDefault();
    gateMode = readStore(MODE_KEY) || autoMode();
    gateServer = readStore(SERVER_KEY) || SERVER_NAME;
    openGate(0);
  });
}

/* глубокие ссылки на профиль: /?m=<id> или #member=Имя */
function openDeepLink(){
  var key = null;
  try { key = new URLSearchParams(location.search).get('m'); } catch(e) {}
  if(!key && location.hash.indexOf('member=') === 0){ key = decodeURIComponent(location.hash.slice(7)); }
  if(!key){ return false; }
  var found = null;
  (fam.members || []).forEach(function(m){
    if(found){ return; }
    if(String(m.id) === key || m.name === key){ found = m; }
  });
  if(!found){ return false; }
  openProfile(found.name);
  return true;
}

(function initGate(){
  var storedMode = readStore(MODE_KEY);
  var storedServer = readStore(SERVER_KEY);
  gateMode = storedMode || autoMode();
  gateServer = storedServer || SERVER_NAME;
  applyModeClass(gateMode);
  var srvEl = document.getElementById('srvName');
  if(srvEl){ srvEl.textContent = gateServer; }
  if(openDeepLink()){ gate.hidden = true; return; }
  if(storedMode && storedServer){ gate.hidden = true; return; }
  openGate(storedMode ? 1 : 0);
})();
"""

FAMA_PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>__TITLE__ - Доска Харука-Арч</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230b0d13'/%3E%3Cpath d='M8 8 Q16 17 24 24' stroke='%23c9973f' stroke-width='2' fill='none'/%3E%3Ccircle cx='8' cy='8' r='4' fill='%23d6b35c'/%3E%3Ccircle cx='24' cy='24' r='4' fill='%23d6b35c'/%3E%3C/svg%3E">
<meta property="og:title" content="__TITLE__ - Доска Харука-Арч">
<meta property="og:description" content="__OGDESC__">
<meta property="og:type" content="website">
<meta property="og:image" content="https://__HOST__/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__TITLE__ - Доска Харука-Арч">
<meta name="twitter:description" content="__OGDESC__">
<meta name="twitter:image" content="https://__HOST__/og.png">
<style>__STYLES__</style>
</head>
<body>
<div id="board"><svg id="threads"></svg></div>

<div id="gate" hidden>
  <div class="gate-card">
    <div class="gate-step" id="gateStep">Шаг 1 из 2</div>
    <h2 id="gateTitle">Как смотришь?</h2>
    <p id="gateSub"></p>
    <div class="gate-choices" id="gateChoices"></div>
    <div class="gate-next">
      <button type="button" class="gate-back" id="gateBack" hidden><- назад</button>
      <span></span>
      <button type="button" id="gateNext">Дальше</button>
    </div>
  </div>
</div>

<div class="hud btns">
<button id="btnSavePos" type="button" hidden>Сохранить позиции</button>
<button id="btnMembers" type="button">Участники</button>
<button id="btnInfo" type="button">О Харука-Арч</button>
</div><button id="btnAddMember" type="button">+ Участник</button>
</div>

<div id="toast-container"></div>

<div id="quick-add-form" class="hidden">
  <button id="quick-add-close" type="button">x</button>
  <h3>Новый участник</h3>
  <div class="label">Ник</div>
  <input id="qa-name" type="text" placeholder="Имя на доске">
  <div class="label">Роль</div>
  <select id="qa-role"></select>
  <div class="actions">
    <button id="qa-cancel" type="button" class="ghost">Отмена</button>
    <button id="qa-save" type="button">Добавить</button>
  </div>
</div>

<div class="hud panel-box" id="infoPanel" hidden>
<div id="infoText"></div>
__ROLES__
<div class="meta" id="infoMeta"></div>
<div class="meta">Наш адрес для Figura: <code>__HOST__</code><br><a href="/fama/edit">редактировать доску</a> - <a href="/info">что это за адрес?</a></div>
</div>

<div class="hud members" id="membersPanel" hidden></div>

<div class="hud profile" id="profilePanel" hidden></div>

<div class="hud legend" id="legend"></div>

<div class="hud hint">
Сервер Харука-Арч: <code id="srvName">North</code> - <a href="#" id="gateChange">сменить</a><br>
Figura Cloud IP: <code>__HOST__</code><br>
</div>

<script>__JS__</script>
</body>
</html>
"""

FAMA_PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: https://mc-heads.net https://minotar.net; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

FAMA_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]{1,253}(:[0-9]{1,5})?$')

async def handle_root(request):
    host_source = settings_state.get("host_display") or request.host
    host = host_source if FAMA_HOST_RE.match(host_source or '') else 'your-server-address'
    og_desc = fam_state.get('tagline') or fam_state.get('description') or 'Доска нашей семьи'
    og_desc = ' '.join(og_desc.split())[:200]
    page = (FAMA_PAGE_TEMPLATE
            .replace('__TITLE__', html.escape(fam_state.get('title') or 'Харука-Арч'))
            .replace('__OGDESC__', html.escape(og_desc))
            .replace('__STYLES__', FAMA_PAGE_STYLES)
            .replace('__JS__', FAMA_PAGE_JS)
            .replace('__FAMA_DATA__', fam_json_safe(fam_state))
            .replace('__SERVER_NAME__', json.dumps(fama_server_name(), ensure_ascii=False))
            .replace('__ROLES__', fama_roles_html())
            .replace('__ROLES_JSON__', fam_json_safe(fama_role_full_map()))
            .replace('__ROLE_COLORS_JSON__', fam_json_safe(fama_role_color_map()))
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
    if not fama_password_configured():
        return web.json_response({"error": "Editor disabled: set FAMA_PASSWORD in .env or use panel"}, status=503)
    ip = request['real_ip']
    blocked = too_many_attempts(ip)
    if blocked:
        return blocked
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid data"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Invalid data"}, status=400)
    password = body.get("password")
    if not fama_check_password(password):
        note_login_failure(ip)
        log_info(f"[FAMA] save REJECT ip={ip} reason=wrong-password")
        return web.json_response({"error": "Wrong password"}, status=403)
    note_login_success(ip)
    try:
        data = fam_sanitize(body.get("data"))
    except ValueError as e:
        return web.json_response({"error": f"Invalid fam data: {e}"}, status=400)
    data["updated"] = time.strftime('%Y-%m-%d %H:%M')
    async with fama_write_lock:
        fam_state.clear()
        fam_state.update(data)
    try:
        await asyncio.to_thread(save_secure_file, FAMA_DATA_PATH, json.dumps(fam_state, ensure_ascii=False, indent=2))
    except OSError as e:
        log_warn(f"[FAMA] disk save failed: {e!r} (page still serves the new data from memory)")
    schedule_fama_backup()
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

var members = (fam.members || []).map(function(m){ return { id: m.id || '', name: m.name || '', role: m.role || '', note: m.note || '', birth: (typeof m.birth === 'number' ? m.birth : ''), generation: (m.generation || 0), x: (typeof m.x === 'number' ? m.x : 50), y: (typeof m.y === 'number' ? m.y : 50) }; });
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
  members = (f.members || []).map(function(m){ return { id: m.id || '', name: m.name || '', role: m.role || '', note: m.note || '', birth: (typeof m.birth === 'number' ? m.birth : ''), generation: (m.generation || 0), x: (typeof m.x === 'number' ? m.x : 50), y: (typeof m.y === 'number' ? m.y : 50) }; });
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
  var del = el('button', 'del', 'x'); del.type = 'button'; del.title = 'Убрать участника';
  del.addEventListener('click', function(){
    var i = members.indexOf(m);
    if(i >= 0) members.splice(i, 1);
    rels = rels.filter(function(r){ return r.from !== m.name && r.to !== m.name; });
    renderMembers(); renderRels(); changed();
  });
  row.appendChild(del);
  var name = el('input'); name.placeholder = 'Имя в Minecraft (аватарка подтянется сама)'; name.value = m.name;
  bind(name, m, 'name');
  var role = el('input'); role.placeholder = 'Роль'; role.value = m.role; role.setAttribute('list', 'rolePresets');
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
  var blab = el('div', 'pos'); blab.appendChild(document.createTextNode('Год')); geo.appendChild(blab);
  var birth = el('input'); birth.type = 'number'; birth.min = '1900'; birth.max = '2100'; birth.placeholder = '1998'; birth.value = m.birth || '';
  birth.addEventListener('input', function(){ m.birth = birth.value ? Number(birth.value) : ''; changed(); });
  geo.appendChild(birth);
  var glab = el('div', 'pos'); glab.appendChild(document.createTextNode('Пок.')); geo.appendChild(glab);
  var gen = el('input'); gen.type = 'number'; gen.min = '1'; gen.max = '9'; gen.placeholder = '1'; gen.value = m.generation || '';
  gen.addEventListener('input', function(){ m.generation = gen.value ? Number(gen.value) : 0; changed(); });
  geo.appendChild(gen);
  row.appendChild(geo);
  var idline = el('div', 'mid span2');
  idline.textContent = m.id ? ('ID профиля: ' + m.id + ' - /member/' + m.id) : 'ID профиля появится после сохранения';
  row.appendChild(idline);
  return row;
}
function renderMembers(){
  M.innerHTML = '';
  members.forEach(function(m){ M.appendChild(memberRow(m)); });
  changed();
}
document.getElementById('addMember').addEventListener('click', function(){
  members.push({ id: '', name: '', role: '', note: '', birth: '', generation: 0, x: Math.round(10 + Math.random() * 76), y: Math.round(10 + Math.random() * 72) });
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
  var del = el('button', 'del', 'x'); del.type = 'button'; del.title = 'Убрать тип нити';
  del.addEventListener('click', function(){
    var used = rels.some(function(r){ return r.type === t.id; });
    if(used){ alert('Есть связи этого типа на доске - сначала уберите их.'); return; }
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
  sFrom.appendChild(opt('-', ''));
  memberNames().forEach(function(n){ sFrom.appendChild(opt(n, n)); });
  sFrom.value = r.from; if(!sFrom.value) sFrom.selectedIndex = 0;
  sFrom.addEventListener('change', function(){ r.from = sFrom.value; changed(); });
  types.forEach(function(t){ sType.appendChild(opt(t.label || t.id, t.id)); });
  sType.value = r.type; if(!sType.value) sType.selectedIndex = 0;
  sType.addEventListener('change', function(){ r.type = sType.value; refreshDot(); changed(); });
  sTo.appendChild(opt('-', ''));
  memberNames().forEach(function(n){ sTo.appendChild(opt(n, n)); });
  sTo.value = r.to; if(!sTo.value) sTo.selectedIndex = 0;
  sTo.addEventListener('change', function(){ r.to = sTo.value; changed(); });
  var del = el('button', 'del', 'x'); del.type = 'button';
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
        out.textContent = 'Сохранено OK Доска уже показывает новые данные.';
      }
      else if(r.status === 403){ out.textContent = 'Неверный пароль. Не закрывайте страницу - черновик сохранён в браузере.'; }
      else { out.textContent = 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status); }
    })
    .catch(function(){ out.textContent = 'Сеть недоступна. Не закрывайте страницу - черновик сохранён в браузере.'; });
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
<title>Редактор Харука-Арч</title>
<style>
:root{--bg:#0a080f;--bg2:#140d26;--card:#161028;--fg:#e8e4f2;--muted:#9a8cc6;--gold:#d8b4ff;--line:#2d2442;--accent:#8b5cf6;--ink:#0f0a1a}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 500px at 50% 0pct,#2d1f4a,var(--bg)) fixed;color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;padding:24px 14px 100px}
main{max-width:900px;margin:0 auto}
h1{color:var(--gold);font-size:24px;margin:0 0 4px;letter-spacing:.02em}
p.h{color:var(--muted);margin:0 0 20px;font-size:13px}
input,textarea,select{width:100%;background:var(--bg2);color:var(--fg);border:1px solid var(--line);border-radius:12px;padding:11px 16px;font:inherit;font-size:14px;margin:0}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,92,246,.15)}
select{margin-top:2px}
.label{display:block;color:var(--muted);font-size:11px;margin:16px 0 8px;text-transform:uppercase;letter-spacing:.1em;font-weight:700}
.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 24px;margin:0 0 20px;box-shadow:0 24px 50px rgba(0,0,0,.45);backdrop-filter:blur(14px)}
.panel h3{margin:0 0 8px;font-size:16px;color:var(--gold);letter-spacing:.01em}
.panel p.sub{margin:0 0 14px;font-size:13px;color:var(--muted);line-height:1.5}
.mrow{position:relative;display:grid;grid-template-columns:1.25fr .75fr;gap:10px 12px;border-top:1px solid var(--line);padding-top:14px;margin-top:14px;padding-right:44px}
.mrow:first-child{border-top:none;padding-top:0}
.mrow .del{position:absolute;top:12px;right:0;background:var(--bg2);color:#e07070;border:1px solid var(--line);border-radius:10px;width:34px;height:34px;font-size:15px;cursor:pointer;transition:all .15s}
.mrow .del:hover{color:#fff;background:#e07070}
.span2{grid-column:1 / -1}
.geo{grid-column:1 / -1;display:flex;gap:10px;align-items:center}
.geo input{margin:0}
.geo .pos{min-width:14px;width:auto;color:var(--muted);font-size:11px;text-align:right}
.mid{font-size:10px;color:var(--muted);font-family:ui-monospace,Consolas,monospace;letter-spacing:.02em}
.trow{display:grid;grid-template-columns:48px 1fr 36px;gap:10px;align-items:center;border-top:1px solid var(--line);padding-top:10px;margin-top:10px}
.trow input[type=color]{height:36px;padding:2px;cursor:pointer;border:none;width:36px;height:36px;border-radius:8px}
.trow .del,.rrow .del{background:var(--bg2);color:#e07070;border:1px solid var(--line);border-radius:10px;width:32px;height:32px;font-size:14px;cursor:pointer;transition:all .15s}
.trow .del:hover,.rrow .del:hover{color:#fff;background:#e07070}
.rrow{display:grid;grid-template-columns:10px 1fr 1fr 1fr 36px;gap:10px;align-items:center;border-top:1px solid var(--line);padding-top:10px;margin-top:10px}
.rel-dot{width:10px;height:10px;border-radius:50%;background:#4a5064;box-shadow:0 0 0 1px rgba(255,255,255,.12)}
.pvwrap{position:sticky;top:0;z-index:2}
.pv{position:relative;width:100%;height:280px;border-radius:16px;background:radial-gradient(600px 300px at 60% 20%,#1c2030,transparent),linear-gradient(180deg,#181b28,#12141c);border:1px solid var(--line);overflow:hidden;box-shadow:inset 0 0 70px rgba(0,0,0,.4)}
.pv svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.pv .pvhint{position:absolute;bottom:8px;right:12px;font-size:10px;color:var(--muted);pointer-events:none}
.pvchip{position:absolute;width:104px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#e8e4f2;border-radius:10px;padding:7px 10px 8px;font-size:10px;line-height:1.3;cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;transform:rotate(var(--tilt,0deg));transform-origin:50% -8px;box-shadow:0 10px 20px rgba(0,0,0,.4);backdrop-filter:blur(6px)}
.pvchip.dragging{cursor:grabbing;z-index:3}
.pvchip b{display:block;color:#2d1f44;text-transform:uppercase;word-break:break-word}
.pvchip .role{display:block;color:#5b3a9e;font-size:8px;text-transform:uppercase;letter-spacing:.05em}
.pvthread{fill:none;stroke-linecap:round;opacity:.95}
.draftbar{background:rgba(255,255,255,.04);border:1px solid var(--line);color:#e6e2f0;border-radius:12px;padding:14px 18px;margin:14px 0;font-size:13px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.draftbar button{margin:0;padding:7px 16px;font-size:13px}
button.ghost{background:var(--bg2);color:var(--gold);border:1px solid var(--line);border-radius:11px;padding:10px 20px;cursor:pointer;margin-top:12px;font-weight:500;transition:all .15s}
button.ghost:hover{background:rgba(139,92,246,.08);border-color:var(--accent);color:#e8e4f2}
#save{display:block;margin:24px auto 0;background:var(--accent);color:#0a080f;border:0;border-radius:12px;padding:14px 44px;font-size:15px;font-weight:600;cursor:pointer;transition:all .15s}
#save:hover{transform:translateY(-1px);box-shadow:0 10px 24px rgba(139,92,246,.25)}
#save:active{transform:translateY(0)}
#out{text-align:center;color:var(--muted);margin-top:14px;font-size:13px;min-height:18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){body{padding:16px 12px 60px}main{max-width:100%}.grid2{grid-template-columns:1fr}.mrow{grid-template-columns:1fr;padding-right:12px}}
</style>
</head>
<body>
<main>
<h1>Редактор Харука-Арч</h1>
<p class="h">Страница для старших Харука-Арч. Все правки сразу появляются на <a style="color:var(--accent)" href="/">главной доске</a>.</p>
<div class="draftbar" id="draftbar" hidden><span>Найден несохранённый черновик с прошлого раза.</span><button id="btnRestore" type="button">Восстановить</button><button id="btnDiscard" class="ghost" type="button">Удалить</button></div>
<div class="panel pvwrap">
  <h3>Живое превью доски</h3>
  <p class="sub">Это как будет выглядеть доска. Карточки можно таскать мышью прямо здесь - позиции сохранятся при сохранении.</p>
  <div class="pv" id="pvBoard"><svg id="pvThreads"></svg><span class="pvhint">тащи карточки мышью OK</span></div>
</div>
<label class="label">Название Харука-Арч</label>
<input id="title" placeholder="Харука-Арч">
<div class="grid2">
  <div><label class="label">Короткая подпись</label><input id="tagline" placeholder="A Family United"></div>
  <div><label class="label">Пароль редактора</label><input id="pass" type="password" placeholder="пароль редактора"></div>
</div>
<label class="label">Описание Харука-Арч</label>
<textarea id="description" rows="4" placeholder="История, принципы, вехи..."></textarea>
<div class="panel">
  <h3>Участники</h3>
  <p class="sub">Позицию карточки лучше менять перетаскиванием прямо на доске - здесь цифры X/Y как запасной вариант.</p>
  <div id="members"></div>
  <datalist id="rolePresets">
__ROLES_OPTIONS__
  </datalist>
  <button class="ghost" id="addMember" type="button">+ Добавить участника</button>
</div>
<div class="panel">
  <h3>Типы нитей</h3>
  <p class="sub">Каждый тип - это цвет нити на доске: кровные, жена, братья, друзья, вражда... Можно добавить свои.</p>
  <div id="types"></div>
  <button class="ghost" id="addType" type="button">+ Добавить тип нити</button>
</div>
<div class="panel">
  <h3>Связи</h3>
  <p class="sub">Выберите двух участников и тип - между ними натянется нить выбранного цвета.</p>
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
            .replace('__ROLES_OPTIONS__', fama_roles_options())
            .replace('__FAMA_DATA__', fam_json_safe(fam_state)))
    return web.Response(text=page, content_type='text/html', charset='utf-8', headers={
        "Content-Security-Policy": FAMA_PAGE_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    })

# ======================== Страница участника ========================
FAM_ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX']

MEMBER_PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>__TITLE__</title>
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__DESC__">
<meta property="og:type" content="profile">
<meta property="og:image" content="https://__HOST__/og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="__TITLE__">
<meta name="twitter:description" content="__DESC__">
<meta name="twitter:image" content="https://__HOST__/og.png">
<style>
:root{--bg:#0a080f;--card:#161028;--fg:#e8e4f2;--muted:#9a8cc6;--gold:#d8b4ff;--line:#2d2442;--accent:#8b5cf6}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 500px at 50% 0pct,#2d1f4a,var(--bg)) fixed;color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;padding:24px 14px 80px}
main{max-width:640px;margin:0 auto}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px 26px 22px;box-shadow:0 24px 60px rgba(0,0,0,.5);backdrop-filter:blur(16px)}
.head{display:flex;gap:18px;align-items:center}
.ava{width:80px;height:80px;flex:0 0 auto;border-radius:12px;background:#cbbce8;image-rendering:pixelated;box-shadow:0 8px 20px rgba(0,0,0,.4)}
.tile{width:80px;height:80px;flex:0 0 auto;border-radius:12px;background:linear-gradient(135deg,#8b5cf6,#d946ef);color:#f4ecff;font-size:40px;line-height:80px;text-align:center;text-transform:uppercase;font-weight:700;box-shadow:0 8px 20px rgba(0,0,0,.4)}
.head h1{margin:0;font-size:28px;color:var(--gold);word-break:break-word}
.role{color:#170b26;font-size:11px;text-transform:uppercase;letter-spacing:.09em;margin-top:5px;padding:4px 14px;border-radius:10px;display:inline-block;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);font-weight:700}
.role-full{display:block;color:var(--muted);font-size:11px;line-height:1.5;margin-top:4px;max-width:450px}
.meta{color:var(--muted);font-size:12px;margin-top:8px}
.note{white-space:pre-line;line-height:1.7;font-size:14.5px;margin:18px 0 8px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:24px 0 8px}
.rel{display:flex;gap:10px;align-items:center;font-size:14px;padding:9px 0;border-top:1px solid var(--line);color:var(--fg)}
.rel .sw{width:24px;height:3px;border-radius:2px;flex:0 0 auto}
.rel .who{margin-left:auto;color:var(--muted);font-size:12px}
.none{color:var(--muted);font-size:13px;margin:8px 0}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px}
.btn{display:inline-block;background:var(--accent);color:#0a080f;border-radius:12px;padding:12px 22px;font-size:14px;font-weight:600;text-decoration:none;transition:all .15s}
.btn:hover{transform:translateY(-1px);box-shadow:0 10px 24px rgba(139,92,246,.3)}
.btn.ghost{background:transparent;color:var(--gold);border:1px solid var(--line);font-weight:500}
.btn.ghost:hover{background:rgba(139,92,246,.08);border-color:var(--accent);color:#e8e4f2}
.foot{margin-top:22px;font-size:12px;color:var(--muted)}
.foot code{background:rgba(20,14,30,.6);border:1px solid var(--line);border-radius:8px;padding:2px 10px;color:var(--fg)}
@media(max-width:640px){body{padding:16px 12px 60px}main{max-width:100%}.card{padding:18px 18px 16px}}
</style>
</head>
<body>
<main>
<div class="card">
  <div class="head">
    __AVATAR__
    <div>
      <h1>__NAME__</h1>
      __ROLE__
      __META__
    </div>
  </div>
  __NOTE__
  <h2>Связи</h2>
  __RELS__
  <div class="actions">
    <a class="btn" href="/?m=__MEMBER_ID__">Показать на доске</a>
    <a class="btn ghost" href="/"><- на доску Харука-Арч</a>
    <button class="btn ghost" id="btnCopyLink">Копировать ссылку</button>
  </div>
</div>
<div class="foot">Ссылка на профиль: <code>__PATH__</code></div>
</main>
<script>
(function(){
  var img = document.getElementById('avaImg');
  if(img){ img.onerror = function(){ img.style.display = 'none'; var t = document.getElementById('avaTile'); if(t){ t.hidden = false; } }; }
})();
document.getElementById('btnCopyLink').addEventListener('click', function(){
  var url = window.location.href;
  navigator.clipboard.writeText(url).then(function(){
    var btn = document.getElementById('btnCopyLink');
    var old = btn.textContent;
    btn.textContent = 'Скопировано OK';
    setTimeout(function(){ btn.textContent = old; }, 1500);
  });
});
</script>
</body>
</html>
"""

async def handle_member(request):
    from urllib.parse import quote
    key = (request.match_info.get("key") or "").strip()
    members = fam_state.get("members") or []
    member = None
    for m in members:
        if str(m.get("id") or "") == key:
            member = m
            break
    if member is None:
        low = key.lower()
        for m in members:
            if (m.get("name") or "").lower() == low:
                member = m
                break
    if member is None:
        return web.Response(status=404, text="Участник не найден", content_type="text/plain", charset="utf-8")
    name = member.get("name") or ""
    member_id = str(member.get("id") or "")
    threads = fam_state.get("threads") or []
    type_map = {t.get("id"): t for t in threads}
    rels_rows = []
    for rel in fam_state.get("relations") or []:
        if rel.get("from") != name and rel.get("to") != name:
            continue
        other_name = rel.get("to") if rel.get("from") == name else rel.get("from")
        other = next((x for x in members if x.get("name") == other_name), None)
        t = type_map.get(rel.get("type")) or {}
        color = t.get("color") if re.match(r"^#[0-9a-fA-F]{3,8}$", str(t.get("color") or "")) else "#c9973f"
        label = t.get("label") or rel.get("type") or "связь"
        other_url = "/member/" + (str(other.get("id")) if other and other.get("id") else quote(other_name or ""))
        rels_rows.append(
            f'<div class="rel"><span class="sw" style="background:{html.escape(color)}"></span>'
            f'{html.escape(label)}: <a href="{html.escape(other_url)}">{html.escape(other_name or "?")}</a>'
            f'<span class="who">открыть профиль -></span></div>'
        )
    rels_html = "".join(rels_rows) if rels_rows else '<p class="none">Связей пока нет.</p>'
    meta_bits = []
    generation = int(member.get("generation") or 0)
    if generation:
        meta_bits.append("поколение " + (FAM_ROMAN[generation] if generation < len(FAM_ROMAN) else str(generation)))
    birth = member.get("birth")
    if birth:
        meta_bits.append(str(birth) + " г.")
    host_source = settings_state.get("host_display") or request.host
    host = host_source if FAMA_HOST_RE.match(host_source or "") else "your-server-address"
    note_text = (member.get("note") or "").strip()
    desc = " ".join(note_text.split())[:200] or ("Профиль участника Харука-Арч " + name)
    role_raw = (member.get("role") or "").strip()
    role_text = html.escape(role_raw)
    role_full = fama_role_full_map().get(role_raw) or ""
    role_color = fama_role_color_map().get(role_raw, "#9b6bff")
    role_html = f'<div class="role" style="background:{html.escape(role_color)};color:#1a1029;padding:2px 8px;border-radius:6px;display:inline-block;">{role_text}</div>' if role_text else ""
    if role_full:
        role_html += f'<div class="role-full">{html.escape(role_full)}</div>'
    page = (MEMBER_PAGE_TEMPLATE
            .replace("__TITLE__", html.escape(name + (" - " + role_raw if role_raw else "") + " - Харука-Арч"))
            .replace("__DESC__", html.escape(desc))
            .replace("__HOST__", html.escape(host))
            .replace("__AVATAR__",
                     f'<img class="ava" id="avaImg" alt="" src="https://mc-heads.net/avatar/{quote(name)}/96">'
                     f'<span class="tile" id="avaTile" hidden>{html.escape((name or "?")[:1].upper())}</span>')
            .replace("__NAME__", html.escape(name))
            .replace("__ROLE__", role_html)
            .replace("__META__", f'<div class="meta">{html.escape(" - ".join(meta_bits))}</div>' if meta_bits else "")
            .replace("__NOTE__", f'<p class="note">{html.escape(note_text)}</p>' if note_text else "")
            .replace("__RELS__", rels_html)
            .replace("__MEMBER_ID__", quote(member_id or name))
            .replace("__PATH__", html.escape("/member/" + (member_id or quote(name)))))
    return web.Response(text=page, content_type="text/html", charset="utf-8", headers={
        "Content-Security-Policy": FAMA_PAGE_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    })

# ======================== Панель настроек ========================
PANEL_PAGE_STYLES = """
:root{--bg:#0a080f;--bg2:#140d26;--card:#161028;--fg:#e8e4f2;--muted:#9a8cc6;--gold:#d8b4ff;--line:#2d2442;--accent:#8b5cf6;--ok:#4caf6d;--err:#e07070}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 600px at 20% 10%,#2a1d4a 0%,transparent 50%),radial-gradient(900px 500px at 80% 90%,#1a1030 0%,transparent 45%),var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;padding:24px 14px 100px}
main{max-width:800px;margin:0 auto}
.hdr{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:8px}
.hdr h1{margin:0;font-size:24px;color:var(--gold);letter-spacing:.02em}
.hdr a{color:var(--accent);text-decoration:none;font-size:13px}
h1{margin:0}
p.h{color:var(--muted);margin:0 0 20px;font-size:13px;line-height:1.5}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 24px;margin:0 0 20px;box-shadow:0 24px 50px rgba(0,0,0,.45);backdrop-filter:blur(16px)}
.panel h3{margin:0 0 8px;font-size:16px;color:var(--gold);letter-spacing:.01em}
.panel p.sub{margin:0 0 14px;font-size:13px;color:var(--muted);line-height:1.5}
.label{display:block;color:var(--muted);font-size:11px;margin:16px 0 8px;text-transform:uppercase;letter-spacing:.1em;font-weight:700}
input,textarea,select{width:100%;background:var(--bg2);color:var(--fg);border:1px solid var(--line);border-radius:12px;padding:11px 16px;font:inherit;font-size:14px;margin:0}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(139,92,246,.15)}
button{background:var(--accent);color:#0a080f;border:0;border-radius:12px;padding:11px 22px;font-size:14px;font-weight:600;cursor:pointer;margin-top:12px;transition:all .15s}
button:hover{transform:translateY(-1px);box-shadow:0 10px 24px rgba(139,92,246,.3)}
button:active{transform:translateY(0)}
button.ghost{background:var(--bg2);color:var(--gold);border:1px solid var(--line);font-weight:500}
button.ghost:hover{background:rgba(139,92,246,.08);border-color:var(--accent);color:#e8e4f2}
.out{font-size:12.5px;color:var(--muted);margin-top:10px;min-height:18px;white-space:pre-wrap}
.out.ok{color:var(--ok)}.out.err{color:var(--err)}
.info{font-size:12.5px;color:var(--muted);line-height:1.6;margin:12px 0 0}
.info code{background:rgba(20,14,30,.6);border:1px solid var(--line);border-radius:8px;padding:1px 8px;color:var(--fg);font-size:12px}
.info ol{margin:10px 0 0;padding-left:22px}
.info li{margin:5px 0}
.hdr{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.hdr a{color:var(--accent);text-decoration:none;font-size:13px}
@media(max-width:640px){body{padding:16px 12px 60px}main{max-width:100%}.panel{padding:16px 18px}}
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
      if(r.status === 200){ savePassIfNeeded(200); out(outId, okText || (r.json && r.json.message) || 'Сохранено OK', 'ok'); }
      else if(r.status === 403){ out(outId, 'Неверный пароль.', 'err'); }
      else { out(outId, 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status), 'err'); }
    })
    .catch(function(){ out(outId, 'Сеть недоступна.', 'err'); });
}

document.getElementById('btnHost').addEventListener('click', function(){
  var v = document.getElementById('hostDisplay').value.trim();
  post('/panel/host', { password: P.value, host_display: v }, 'outHost', v ? 'Адрес сохранён OK На доске теперь показывается он.' : 'Сброшено OK Доска показывает адрес, который видит браузер.');
});

document.getElementById('btnServer').addEventListener('click', function(){
  var v = document.getElementById('serverName').value.trim();
  if(!v){ out('outServer', 'Введите название сервера.', 'err'); return; }
  post('/panel/server', { password: P.value, server_name: v }, 'outServer', 'Сервер сохранён OK Теперь на старте показывается "' + v + '".');
});

document.getElementById('btnPassword').addEventListener('click', function(){
  var np = document.getElementById('newPass').value;
  if(!np){ out('outPassword', 'Введите новый пароль.', 'err'); return; }
  post('/panel/password', { password: P.value, new_password: np }, 'outPassword', 'Пароль изменён OK Он действует для панели и редактора сразу.');
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
      if(r.status === 200){ out('outPing', 'Готово OK Анти-сон теперь пингует: ' + (r.json.ping_url || url), 'ok'); document.getElementById('token').value = ''; }
      else if(r.status === 403){ out('outPing', 'Неверный пароль панели.', 'err'); }
      else { out('outPing', 'Ошибка: ' + ((r.json && r.json.error) ? r.json.error : r.status), 'err'); }
    })
    .catch(function(){ out('outPing', 'Сеть недоступна.', 'err'); });
});

document.getElementById('curPing').textContent = 'Сейчас пингуется: ' + (__PING_URL__ || 'адрес по умолчанию из репозитория');
document.getElementById('curHost').textContent = 'Сейчас показывается на доске: ' + (__HOST_DISPLAY__ || '(автоматически - адрес из браузера)');
document.getElementById('curEditorPass').textContent = 'Пароль редактора: задан ' + (__HAS_PASS__ ? '(да)' : '(НЕТ - редактор выключен)');
document.getElementById('curServer').textContent = 'Сейчас на сайте: ' + __SERVER_NAME__;
document.getElementById('serverName').value = __SERVER_NAME__;
document.getElementById('curBackup').textContent = __BACKUP_INFO__;
"""

PANEL_PAGE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Панель настроек - Харука-Арч</title>
<style>__STYLES__</style>
</head>
<body>
<main>
<div class="hdr"><h1>Панель настроек</h1><a href="/"><- на доску</a></div>
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
  <h3>Сервер Харука-Арч</h3>
  <p class="sub">Название сервера, которое видят игроки на стартовом экране и на доске. Пока один - "North".</p>
  <input id="serverName" placeholder="North">
  <button id="btnServer" type="button">Сохранить сервер</button>
  <div class="info" id="curServer"></div>
  <div class="out" id="outServer"></div>
</div>

<div class="panel">
  <h3>Авто-бэкап доски в GitHub</h3>
  <p class="sub">Когда задан токен, каждое сохранение доски коммитит data/fam.json в репозиторий. Данные переживают перезапуск Render. Токен в панели не хранится - только в Environment.</p>
  <div class="info" id="curBackup"></div>
</div>

<div class="panel">
  <h3>Адрес для доски (домен)</h3>
  <p class="sub">Что показывать на доске как "Figura Cloud IP". Полезно, если игрокам нужно вписывать красивое имя домена.</p>
  <input id="hostDisplay" placeholder="arch-family.indevs.in (пусто - показывать реальный адрес)">
  <button id="btnHost" type="button">Сохранить адрес</button>
  <div class="info" id="curHost"></div>
  <div class="out" id="outHost"></div>
  <div class="info"><b>Как привязать свой домен:</b>
  <ol>
    <li>Render -> твой сервис -> Settings -> Custom Domains -> Add Custom Domain -> вписать <code>arch-family.indevs.in</code></li>
    <li>Render покажет CNAME-запись - добавь её в панели DNS у поставщика домена (тип: CNAME, имя: <code>arch-family</code>, значение: как показал Render)</li>
    <li>Дождись статуса Verified - HTTPS Render поднимет сам</li>
    <li>Вернись сюда и впиши этот адрес в поле выше - доска сразу покажет его игрокам</li>
  </ol></div>
</div>

<div class="panel">
  <h3>Анти-сон (пинг каждые 5 минут)</h3>
  <p class="sub">GitHub Actions пингует сервер, чтобы он не засыпал. Здесь можно сменить адрес, который пингуется - нужен GitHub-токен с доступом к репозиторию (scope: repo, workflow). Токен нигде не сохраняется.</p>
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
    if not fama_password_configured():
        return web.Response(status=503, text="Panel disabled: set FAMA_PASSWORD or add a password hash to data/settings.json")
    if fama_github_token():
        backup_info = f"Включён OK Репозиторий: {FAMA_GITHUB_REPO}"
        if fam_backup_state.get("last_ok"):
            backup_info += f"\nПоследний бэкап: {fam_backup_state['last_ok']}"
        if fam_backup_state.get("last_error"):
            backup_info += f"\nПоследняя ошибка: {fam_backup_state['last_error']}"
    else:
        backup_info = "Выключен. Добавьте FAMA_GITHUB_TOKEN (fine-grained, Contents: Read and write) в Environment на Render."
    page = (PANEL_PAGE_TEMPLATE
            .replace('__STYLES__', PANEL_PAGE_STYLES)
            .replace('__JS__', PANEL_PAGE_JS)
            .replace('__DEFAULT_REPO__', json.dumps(PANEL_DEFAULT_REPO))
            .replace('__PING_URL__', json.dumps(settings_state.get('ping_url') or ''))
            .replace('__HOST_DISPLAY__', json.dumps(settings_state.get('host_display') or ''))
            .replace('__HAS_PASS__', json.dumps(fama_password_configured()))
            .replace('__SERVER_NAME__', json.dumps(fama_server_name(), ensure_ascii=False))
            .replace('__BACKUP_INFO__', json.dumps(backup_info, ensure_ascii=False)))
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
    blocked = too_many_attempts(ip)
    if blocked:
        return blocked
    if not isinstance(body, dict):
        return web.json_response({"error": "Invalid data"}, status=400)
    password = body.get("password")
    if not fama_check_password(password):
        note_login_failure(ip)
        return panel_unauthorized(ip, "wrong-password")
    note_login_success(ip)
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

async def handle_panel_server(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    deny = await panel_check(request, body)
    if deny:
        return deny
    name = fam_clean_text((body.get("server_name") or "").strip(), 40)
    if not name:
        return web.json_response({"error": "Server name cannot be empty"}, status=400)
    settings_state["server_name"] = name
    save_settings_state()
    log_info(f"[PANEL] server_name set to {name!r} ip={request['real_ip']}")
    return web.json_response({"status": "success", "server_name": name})

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
    settings_state["fama_password_hash"] = bcrypt.hashpw(
        new_password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)
    ).decode("ascii")
    settings_state["fama_password"] = ""
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
<title>Статус - Харука-Арч</title>
<style>
:root{--bg:#0a080f;--card:#161028;--fg:#e8e4f2;--muted:#9a8cc6;--gold:#d8b4ff;--line:#2d2442;--ok:#4caf6d;--err:#e07070}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{background:radial-gradient(1200px 500px at 50% 0pct,#2d1f4a,var(--bg)) fixed;color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;padding:24px 14px 60px}
main{max-width:680px;margin:0 auto}
h1{color:var(--gold);font-size:24px;margin:0 0 16px;letter-spacing:.02em}
.row{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 18px;margin:10px 0;display:flex;gap:14px;align-items:center;transition:all .2s}
.row:hover{border-color:rgba(139,92,246,.3)}
.dot{width:14px;height:14px;border-radius:50%;flex:0 0 auto;background:var(--muted)}
.dot.ok{background:var(--ok);box-shadow:0 0 12px rgba(76,175,109,.5)}
.dot.err{background:var(--err);box-shadow:0 0 12px rgba(224,112,112,.5)}
.row b{display:block;font-size:14px;color:var(--fg)}
.row span{color:var(--muted);font-size:12.5px}
a{color:#8b5cf6;text-decoration:none}
a:hover{text-decoration:underline}
@media(max-width:640px){body{padding:16px 12px 60px}main{max-width:100%}}
</style>
</head>
<body>
<main>
<h1>Статус сервера</h1>
__ROWS__
<p style="color:var(--muted);font-size:12.5px"><- <a href="/">на доску</a> - <a href="/fama/edit">редактировать</a> - <a href="/panel">панель настроек</a></p>
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
    rows.append(status_row(True, "Сервер работает", f"аптайм: {int((time.time() - SERVER_STARTED_AT) / 60)} мин - время сервера: {time.strftime('%Y-%m-%d %H:%M')}"))
    try:
        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{os.environ.get('PORT', '10000')}/api/version", timeout=ClientTimeout(total=5)) as resp:
                rows.append(status_row(resp.status == 200, "API /api/version", f"HTTP {resp.status}"))
    except Exception as e:
        rows.append(status_row(False, "API /api/version", f"недоступен: {e!r}"))
    n_members = len(fam_state.get("members") or [])
    n_rels = len(fam_state.get("relations") or [])
    rows.append(status_row(True, "Доска Харука-Арч", f"участников: {n_members} - связей: {n_rels} - обновлено: {fam_state.get('updated') or '-'}"))
    has_pass = fama_password_configured()
    rows.append(status_row(has_pass, "Редактор доски", "пароль задан OK" if has_pass else "пароль не задан - редактор выключен"))
    host_d = settings_state.get("host_display") or ""
    rows.append(status_row(True, "Адрес на доске", host_d if host_d else "автоматический (из браузера)"))
    rows.append(status_row(True, "Сервер Харука-Арч", fama_server_name()))
    if fama_github_token():
        if fam_backup_state.get("last_error"):
            rows.append(status_row(False, "Авто-бэкап в GitHub", f"{FAMA_GITHUB_REPO} - ошибка: {fam_backup_state['last_error']}"))
        elif fam_backup_state.get("last_ok"):
            rows.append(status_row(True, "Авто-бэкап в GitHub", f"{FAMA_GITHUB_REPO} - последний: {fam_backup_state['last_ok']}"))
        else:
            rows.append(status_row(True, "Авто-бэкап в GitHub", f"{FAMA_GITHUB_REPO} - ждёт первого сохранения"))
    else:
        rows.append(status_row(False, "Авто-бэкап в GitHub", "выключен - задайте FAMA_GITHUB_TOKEN"))
    blocked_now = sum(1 for guard_ip in list(login_guard) if login_block_remaining(guard_ip) > 0)
    rows.append(status_row(True, "Защита от перебора", f"заблокировано IP сейчас: {blocked_now} - лимит {LOGIN_FAIL_LIMIT} попыток за {LOGIN_FAIL_WINDOW // 60} мин"))
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if bot_token:
        guild = os.environ.get("FAMA_DISCORD_GUILD_ID", "").strip()
        rows.append(status_row(True, "Discord-бот", f"включён - сервер {guild or 'все серверы (глобально)'}"))
    else:
        rows.append(status_row(False, "Discord-бот", "выключен - задайте DISCORD_BOT_TOKEN"))
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


