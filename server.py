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



FAMA_PAGE_STYLES = """
/* ===== PRIMITIVE TOKENS ===== */
:root{
  /* Colors */
  --color-bg:#0a080f;
  --color-panel:#161028;
  --color-card:#1c1430;
  --color-fg:#e8e4f2;
  --color-muted:#9a8cc6;
  --color-accent:#8b5cf6;
  --color-accent-hover:#a78bfa;
  --color-gold:#d8b4ff;
  --color-line:#2d2442;
  --color-ink:#0f0a1a;
  --color-ok:#4caf6d;
  --color-err:#e07070;
  --color-warn:#d97706;
  /* Radius */
  --radius-sm:8px;
  --radius-md:12px;
  --radius-lg:16px;
  --radius-xl:20px;
  /* Shadows */
  --shadow-sm:0 4px 12px rgba(0,0,0,.3);
  --shadow-md:0 12px 32px rgba(0,0,0,.45);
  --shadow-lg:0 24px 48px rgba(0,0,0,.6);
  /* Spacing */
  --space-2:4px;
  --space-3:8px;
  --space-4:12px;
  --space-5:16px;
  --space-6:24px;
  --space-8:32px;
  /* Typography */
  --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Inter",system-ui,sans-serif;
  --font-size-sm:11px;
  --font-size-md:13px;
  --font-size-lg:15px;
  --font-size-xl:17px;
  --font-size-2xl:20px;
  --font-size-3xl:24px;
  --font-size-4xl:30px;
  /* Transitions */
  --transition-fast:0.15s ease;
  --transition-base:0.25s ease;
  --transition-slow:0.4s ease;
}
/* ===== SEMANTIC TOKENS ===== */
:root{
  --color-surface:var(--color-card);
  --color-surface-hover:rgba(255,255,255,.06);
  --color-text-primary:var(--color-fg);
  --color-text-secondary:var(--color-muted);
  --color-text-accent:var(--color-gold);
  --color-border:var(--color-line);
  --color-border-hover:rgba(139,92,246,.4);
  --color-button-bg:var(--color-accent);
  --color-button-text:#0a080f;
  --color-button-hover:var(--color-accent-hover);
  --color-input-bg:rgba(20,14,30,.5);
  --color-input-border:var(--color-line);
  --color-input-focus-ring:rgba(139,92,246,.15);
}
/* ===== COMPONENT TOKENS ===== */
:root{
  --card-bg:var(--color-surface);
  --card-border:var(--color-border);
  --card-shadow:var(--shadow-md);
  --card-radius:var(--radius-lg);
  --panel-bg:var(--color-panel);
  --panel-border:var(--color-border);
  --panel-shadow:var(--shadow-lg);
  --panel-radius:var(--radius-xl);
  --button-bg:var(--color-button-bg);
  --button-text:var(--color-button-text);
  --button-hover:var(--color-button-hover);
  --button-radius:var(--radius-md);
  --input-bg:var(--color-input-bg);
  --input-border:var(--color-input-border);
  --input-radius:var(--radius-md);
  --input-focus-ring:var(--color-input-focus-ring);
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;overflow:hidden;background:radial-gradient(1400px 800px at 15% 10%,#2d1f4a 0%,transparent 45%),radial-gradient(900px 700px at 85% 90%,#1a1030 0%,transparent 40%),var(--color-bg);color:var(--color-text-primary);font-family:var(--font-sans)}
#board{position:fixed;inset:0;overflow:hidden;background:#1a102e;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='none' stroke='rgba(255,255,255,.02)'/%3E%3C/svg%3E"),radial-gradient(ellipse at 20% 25%,rgba(139,92,246,.15),transparent 55%),radial-gradient(ellipse at 80% 75%,rgba(217,70,239,.12),transparent 50%);background-size:40px 40px,cover,cover;box-shadow:inset 0 0 100px rgba(2,0,10,.4)}
#threads{position:absolute;inset:0;width:100%;height:100%;z-index:1;pointer-events:none}
.thread-glow{fill:none;opacity:.18;stroke-linecap:round}
.thread-main{fill:none;opacity:.95;stroke-linecap:round}
.knot{opacity:.9}
#threads path.cold,#threads circle.cold{opacity:.05 !important}
#threads path.thread-main.hot{opacity:1}
#threads path.thread-glow.hot{opacity:.5}
#threads circle.hot{opacity:1}
.profile{top:12px;right:12px;width:min(380px,92vw);max-height:75vh;overflow:auto;background:var(--panel-bg);border:var(--panel-border) solid var(--panel-border);border-radius:var(--panel-radius);padding:20px 22px 20px;box-shadow:var(--panel-shadow);backdrop-filter:blur(20px);font-family:var(--font-sans);animation:fadeIn .5s ease-out}
.profile .ph{display:flex;gap:14px;align-items:center;position:relative;margin-bottom:14px}
.profile .ph img{width:56px;height:56px;image-rendering:pixelated;border-radius:10px;background:#cbbce8;flex:0 0 auto;box-shadow:0 6px 16px rgba(0,0,0,.35)}
.profile .ava-tile{width:56px;height:56px;line-height:56px;font-size:26px;flex:0 0 auto;background:linear-gradient(135deg,#8b5cf6,#d946ef);color:#fff;text-align:center;font-weight:700}
.profile h3{margin:0;color:var(--color-text-accent);font-size:var(--font-size-2xl);word-break:break-word}
.profile .role{color:#0a080f;font-size:10px;text-transform:uppercase;letter-spacing:.08em;margin-top:6px;padding:3px 12px;border-radius:10px;display:inline-block;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);font-weight:600}
.profile .role-full{display:block;color:var(--color-text-secondary);font-size:10.5px;line-height:1.45;margin-top:3px;max-width:440px}
.profile .note{white-space:pre-line;font-size:14px;line-height:1.65;color:var(--color-text-primary);margin:12px 0 0}
.profile .rel{display:flex;gap:10px;align-items:center;font-size:13.5px;padding:7px 0;border-top:1px solid var(--color-border);color:var(--color-text-primary)}
.profile .rel .sw{width:24px;height:3px;border-radius:2px;flex:0 0 auto}
.profile .close{position:absolute;top:12px;right:12px;background:none;border:1px solid var(--color-border);border-radius:12px;color:var(--color-text-secondary);width:32px;height:32px;cursor:pointer;font-size:15px;line-height:1;transition:var(--transition-fast)}
.profile .close:hover{color:var(--color-text-primary);background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.2)}
.profile .none{font-size:12.5px;color:var(--color-text-secondary);margin:8px 0 0}
.profile .meta-line{font-size:11.5px;color:var(--color-text-secondary);margin:3px 0 0}
.profile .linkrow{display:flex;gap:8px;margin-top:16px;border-top:1px solid var(--color-border);padding-top:14px}
.profile .linkrow input{flex:1;min-width:0;background:rgba(20,14,30,.5);border:1px solid var(--color-input-border);border-radius:var(--input-radius);color:var(--color-text-primary);font:11.5px var(--font-sans);padding:9px 12px}
.profile .linkrow input:focus{outline:none;border-color:var(--color-accent);box-shadow:0 0 0 3px var(--color-input-focus-ring)}
.profile .linkrow button{background:var(--color-button-bg);color:var(--color-button-text);border:0;border-radius:10px;padding:0 16px;font:12px var(--font-sans);cursor:pointer;white-space:nowrap;transition:var(--transition-fast)}
.profile .linkrow button:hover{background:#a78bfa;transform:translateY(-1px);box-shadow:0 8px 20px rgba(139,92,246,.3)}
.photo{position:absolute;width:150px;z-index:2;transform:rotate(var(--tilt,0deg));transform-origin:50% -30px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:var(--color-text-primary);border-radius:14px;padding:12px 13px 14px;box-shadow:0 24px 48px rgba(0,0,0,.5),inset 0 0 0 1px rgba(255,255,255,.15);touch-action:none;cursor:grab;user-select:none;-webkit-user-select:none;backdrop-filter:blur(12px);transition:var(--transition-base);animation:fadeIn .5s ease-out both}
.photo.dragging{cursor:grabbing;z-index:4;box-shadow:0 32px 64px rgba(0,0,0,.7),inset 0 0 0 1px rgba(255,255,255,.15);transform:rotate(var(--tilt,0deg)) scale(1.04)}
.photo::before{content:'';position:absolute;top:-10px;left:50%;transform:translateX(-50%) rotate(var(--tape,-2deg));width:58px;height:16px;background:rgba(201,162,255,.2);border:1px solid rgba(255,255,255,.15);box-shadow:0 2px 5px rgba(0,0,0,.3);border-radius:7px}
.photo img{display:block;width:44px;height:44px;margin:0 auto 7px;image-rendering:pixelated;border-radius:6px;background:#cbbce8;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.ava-tile{display:block;width:44px;height:44px;margin:0 auto 7px;border-radius:6px;background:linear-gradient(135deg,#8b5cf6,#d946ef);color:#f4ecff;font-size:20px;line-height:44px;text-align:center;font-family:var(--font-sans);text-transform:uppercase;font-weight:700}
.mrow .ava-tile{width:26px;height:26px;line-height:26px;font-size:14px;font-weight:600}
.photo b{display:block;font-size:12px;letter-spacing:.02em;color:var(--color-text-primary);text-transform:uppercase;word-break:break-word;text-align:center;margin:3px 0 2px}
.photo .role{display:inline-block;color:var(--color-text-accent);font-size:8.5px;margin:1px 0 3px;text-transform:uppercase;letter-spacing:.05em;text-align:center;padding:1.5px 7px;border-radius:5px;font-weight:800;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2)}
.photo .note{margin:5px 0 0;white-space:pre-line;font-size:9.5px;line-height:1.4;color:var(--color-text-secondary);max-height:6.5em;overflow:hidden;text-align:center}
.photo .card-badge{position:absolute;top:4px;left:5px;background:rgba(36,18,60,.85);color:#f2e8ff;font:9px/1.5 var(--font-sans);border-radius:5px;padding:0 5px;letter-spacing:.05em;pointer-events:none}
.photo .card-year{position:absolute;top:4px;right:5px;background:rgba(255,255,255,.55);color:#4a2f6e;font:9px/1.5 var(--font-sans);border-radius:5px;padding:0 5px;pointer-events:none}
.hud{position:fixed;z-index:50;font-family:var(--font-sans)}
.hud button{background:var(--panel-bg);color:var(--color-text-accent);border:1px solid var(--color-border);border-radius:12px;padding:11px 18px;cursor:pointer;font-size:13px;font-weight:500;transition:var(--transition-fast);backdrop-filter:blur(6px)}
.hud button:hover{background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.2);color:#e8e4f2;transform:translateY(-1px)}
.hud button.on{background:var(--color-button-bg);color:var(--color-button-text);border-color:var(--color-button-bg);box-shadow:0 0 0 3px rgba(139,92,246,.2)}
.hud button:active{transform:translateY(0)}
.btns{top:12px;right:12px;display:flex;gap:8px}
.panel-box{top:60px;right:12px;width:min(370px,90vw);background:var(--panel-bg);border:1px solid var(--color-border);border-radius:var(--panel-radius);padding:20px 22px;box-shadow:var(--panel-shadow);backdrop-filter:blur(20px)}
.panel-box .desc{white-space:pre-line;line-height:1.6;font-size:13.5px;color:var(--color-text-primary)}
.panel-box .roles{margin-top:16px;border-top:1px solid var(--color-border);padding-top:12px;max-height:32vh;overflow:auto}
.panel-box .roles h4{margin:10px 0 6px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-text-secondary)}
.panel-box .roles h4:first-child{margin-top:0}
.panel-box .roles ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.panel-box .roles li{font-size:13px;line-height:1.4;color:var(--color-text-primary)}
.panel-box .roles li b{color:var(--color-text-accent);font-weight:700;display:block}
.panel-box .roles li span{color:var(--color-text-secondary);font-size:11.5px}
.panel-box .meta{margin-top:14px;font-size:11.5px;color:var(--color-text-secondary);line-height:1.6}
.panel-box .meta a{color:var(--color-accent);text-decoration:none}
.panel-box .meta a:hover{text-decoration:underline}
.panel-box .meta code{background:rgba(20,14,30,.6);border:1px solid var(--color-border);border-radius:7px;padding:1px 7px;color:var(--color-text-primary)}
.members{top:60px;right:12px;width:min(360px,90vw);max-height:60vh;overflow:auto;background:var(--panel-bg);border:1px solid var(--color-border);border-radius:var(--panel-radius);padding:14px 18px;box-shadow:var(--panel-shadow);backdrop-filter:blur(20px)}
.mrow{padding:9px 0;border-bottom:1px solid var(--color-border);display:flex;gap:10px;align-items:flex-start}
.mrow:last-child{border-bottom:0}
.mrow img{width:26px;height:26px;image-rendering:pixelated;border-radius:6px;flex:0 0 auto;margin-top:2px}
.mrow .who{min-width:0}
.mrow b{color:var(--color-text-accent);font-size:14px}
.mrow .role{color:var(--color-text-secondary);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;margin-left:6px}
.mrow .note{font-size:12px;margin:3px 0 0;white-space:pre-line;color:var(--color-text-primary)}
.legend{bottom:12px;left:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;max-width:50vw;background:var(--panel-bg);border:1px solid var(--color-border);border-radius:16px;padding:8px 14px;font-size:12px;color:var(--color-text-secondary);backdrop-filter:blur(12px)}
.legend .sw{display:inline-block;width:24px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.hint{bottom:12px;right:12px;background:var(--panel-bg);border:1px solid var(--color-border);border-radius:16px;padding:8px 14px;font-size:11.5px;color:var(--color-text-secondary);text-align:right;line-height:1.6;backdrop-filter:blur(12px)}
.hint a{color:var(--color-accent);text-decoration:none}
.hint a:hover{text-decoration:underline}
.hint code{background:rgba(20,14,30,.6);border:1px solid var(--color-border);border-radius:7px;padding:1px 6px;color:var(--color-text-primary)}
#btnSavePos{background:#2a3d2c;color:#8fd6a0;border-color:#3c5a40}
#btnSavePos:hover{background:#35503a}
#btnAddMember{background:var(--color-button-bg);color:var(--color-button-text);border:0;border-radius:12px;padding:9px 16px;font-size:12.5px;font-weight:600;cursor:pointer;transition:var(--transition-fast)}
#btnAddMember:hover{background:#a78bfa;transform:translateY(-1px);box-shadow:0 8px 20px rgba(139,92,246,.3)}
#btnAddMember:active{transform:translateY(0)}
#btnAddMember.hidden{display:none}
/* ===== TOASTS ===== */
#toast-container{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:200;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast{background:rgba(20,14,30,.94);border:1px solid var(--color-border);color:var(--color-text-primary);padding:10px 16px;border-radius:10px;box-shadow:0 12px 28px rgba(0,0,0,.5);backdrop-filter:blur(10px);font-size:13px;min-width:220px;max-width:340px;text-align:center;opacity:0;transform:translateY(20px);transition:opacity .25s,transform .25s;pointer-events:auto}
.toast.show{opacity:1;transform:translateY(0)}
.toast.ok{border-color:var(--color-ok);color:#dcfce8}
.toast.err{border-color:var(--color-err);color:#fecaca}
.toast.warn{border-color:var(--color-warn);color:#fef3c7}
/* ===== QUICK ADD FORM ===== */
#quick-add-form{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:150;background:var(--panel-bg);border:1px solid var(--color-border);border-radius:18px;padding:26px 30px;box-shadow:0 30px 80px rgba(0,0,0,.6);backdrop-filter:blur(14px);min-width:300px;max-width:90vw;display:none;animation:fadeIn .3s ease-out}
#quick-add-form.hidden{display:none}
#quick-add-form h3{margin:0 0 18px;font-size:19px;color:var(--color-text-accent)}
#quick-add-form .label{display:block;color:var(--color-text-secondary);font-size:11px;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.1em;font-weight:700}
#quick-add-form input,#quick-add-form select{width:100%;background:rgba(20,14,30,.5);border:1px solid var(--color-border);border-radius:10px;padding:10px 14px;font:inherit;font-size:14px;margin:0}
#quick-add-form input:focus,#quick-add-form select:focus{outline:none;border-color:var(--color-accent);box-shadow:0 0 0 3px var(--color-input-focus-ring)}
#quick-add-form .actions{display:flex;gap:10px;margin-top:22px;justify-content:flex-end}
#quick-add-form button{padding:9px 18px;font-size:13px}
#quick-add-close{background:none;border:0;border-radius:10px;color:var(--color-text-secondary);cursor:pointer;position:absolute;top:12px;right:12px;font-size:20px;line-height:1;transition:var(--transition-fast)}
#quick-add-close:hover{color:var(--color-text-primary);background:rgba(255,255,255,.06)}
/* ===== GATE ===== */
#gate{position:fixed;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(12,8,24,.85);backdrop-filter:blur(14px);font-family:var(--font-sans)}
#gate[hidden]{display:none}
.gate-card{width:min(500px,92vw);background:linear-gradient(180deg,#251b3a,#1a102e);color:#e8e4f2;border:1px solid rgba(255,255,255,.06);border-radius:24px;padding:34px 34px 30px;box-shadow:0 40px 100px rgba(0,0,0,.7)}
.gate-step{font-size:11px;color:#8b5cf6;letter-spacing:.16em;text-transform:uppercase}
.gate-card h2{margin:6px 0 8px;font-size:26px;letter-spacing:.01em;color:#f2e8ff}
.gate-card p{margin:0 0 24px;color:#b8a8d0;font-size:15px;line-height:1.6}
.gate-choices{display:grid;gap:12px}
.gate-opt{display:flex;gap:14px;align-items:center;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:16px 18px;background:rgba(255,255,255,.03);cursor:pointer;font:inherit;color:inherit;text-align:left;transition:var(--transition-base)}
.gate-opt:hover{border-color:rgba(139,92,246,.5);background:rgba(255,255,255,.08)}
.gate-opt.on{border-color:#8b5cf6;background:rgba(139,92,246,.12);box-shadow:0 0 0 3px rgba(139,92,246,.18)}
.gate-opt .ico{font-size:28px;flex:0 0 auto;width:34px;text-align:center}
.gate-opt b{display:block;font-size:17px;color:#f2e8ff}
.gate-opt span{display:block;font-size:13px;color:#b8a8d0;margin-top:1px}
.gate-next{margin-top:22px;display:flex;justify-content:space-between;align-items:center;gap:10px}
.gate-next button{background:var(--color-button-bg);color:var(--color-button-text);border:0;border-radius:14px;padding:12px 26px;font-size:15px;font-weight:600;cursor:pointer;transition:var(--transition-base)}
.gate-next button:hover{transform:translateY(-1px);box-shadow:0 12px 28px rgba(139,92,246,.35)}
.gate-next button:disabled{opacity:.45;cursor:default}
.gate-back{background:none !important;border:0;color:var(--color-text-secondary)!important;cursor:pointer;font-size:13px;text-decoration:underline;padding:0 !important}
/* ===== FILTERS ===== */
.chip{background:var(--panel-bg);border:1px solid var(--color-border);border-radius:999px;padding:5px 11px;color:var(--color-text-secondary);font:11.5px var(--font-sans);cursor:pointer;display:flex;align-items:center;gap:6px}
.chip.on{color:#0a080f;background:var(--color-gold);border-color:var(--color-gold);font-weight:600}
.photo.muted{opacity:.4;filter:grayscale(.45)}
#threads path.muted,#threads circle.muted{opacity:.03 !important}
/* ===== MOBILE ===== */
html.m-mode .photo{width:116px}
html.m-mode .photo b{font-size:10.5px}
html.m-mode .btns{top:8px;right:8px;gap:7px;flex-wrap:wrap;justify-content:flex-end;max-width:70vw}
html.m-mode .btns button{padding:7px 10px;font-size:12px}
html.m-mode .legend{max-width:56vw;font-size:11px;padding:7px 12px}
html.m-mode .panel-box,html.m-mode .members,html.m-mode .profile{top:auto;left:8px;right:8px;bottom:8px;width:auto;max-height:56vh;border-radius:16px}
@media(max-width:640px){.title h1{font-size:20px}.photo{width:110px}.photo b{font-size:10px}.legend{max-width:56vw;font-size:10px;padding:6px 10px}}
@keyframes fadeIn{from{opacity:0;transform:scale(.92) rotate(var(--tilt,0deg))}to{opacity:1;transform:scale(1) rotate(var(--tilt,0deg))}}
"""
