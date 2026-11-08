"""Smoke-тест страниц фамы.

Запускается после старта server.py на тестовом порту.
Проверяет, что основные страницы живы, данные читаются и сохранение работает.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:" + os.environ.get("PORT", "8099")
PASSWORD = os.environ.get("FAMA_PASSWORD", "smoke-test-password")
failures = []


def get(path, timeout=10):
    request = urllib.request.Request(BASE + path, headers={"User-Agent": "fama-smoke"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def post(path, payload, timeout=10):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "fama-smoke"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait_ready(attempts=60):
    for _ in range(attempts):
        try:
            status, _ = get("/api/version", timeout=3)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def check(name, condition, detail=""):
    if condition:
        print("PASS " + name)
        return
    print("FAIL " + name + ((" :: " + str(detail)) if detail else ""))
    failures.append(name)


def main():
    if not wait_ready():
        print("FAIL server did not start")
        return 1

    status, body = get("/")
    page = body.decode("utf-8", "replace")
    check("/ 200", status == 200, status)
    check("start gate", 'id="gate"' in page and "gateChoices" in page)
    check("server name", "Норт" in page)
    check("filter chips", 'id="filterChips"' in page)
    check("no search input", 'id="searchInput"' not in page)
    check("roles legend", "Founder of the Haruka Arch family" in page and "Союзные семьи" in page)
    check("card roles", "👑 Founder" in page and "🌹 Arch" in page and "ROLE_FULL" in page)
    check("generations button", 'id="btnGen"' in page)
    check("og tags", "og:image" in page and "twitter:card" in page)
    check("favicon", 'rel="icon"' in page)

    status, body = get("/og.png")
    check("/og.png 200", status == 200, status)
    check("/og.png png magic", body[:8] == b"\x89PNG\r\n\x1a\n")
    check("/og.png size", len(body) > 5000, len(body))

    status, body = get("/status")
    status_page = body.decode("utf-8", "replace")
    check("/status 200", status == 200, status)
    check("/status rows", status_page.count('class="row"') >= 8)
    check("/status backup row", "Авто-бэкап" in status_page)
    check("/status bot row", "Discord-бот" in status_page)

    status, body = get("/panel")
    panel = body.decode("utf-8", "replace")
    check("/panel 200", status == 200, status)
    check("/panel server field", 'id="serverName"' in panel)
    check("/panel backup info", "Авто-бэкап" in panel)

    status, body = get("/fama/edit")
    editor = body.decode("utf-8", "replace")
    check("/fama/edit 200", status == 200, status)
    check("/fama/edit generation field", "Пок." in editor)
    check("/fama/edit role presets", 'id="rolePresets"' in editor and "👑 Founder" in editor)

    status, body = get("/fama.json")
    check("/fama.json 200", status == 200, status)
    fam = json.loads(body.decode("utf-8"))
    members = fam.get("members") or []

    if members:
        member_id = members[0].get("id")
        check("member has numeric id", bool(member_id) and str(member_id).isdigit(), member_id)
        status, body = get("/member/" + str(member_id))
        member_page = body.decode("utf-8", "replace")
        check("/member/<id> 200", status == 200, status)
        check("/member/<id> content", members[0].get("name", "") in member_page)

    payload = {
        "title": fam.get("title") or "Фама",
        "tagline": fam.get("tagline") or "",
        "description": fam.get("description") or "",
        "members": members,
        "threads": fam.get("threads") or [],
        "relations": fam.get("relations") or [],
    }
    status, _ = post("/fama/save", {"password": "definitely-wrong", "data": payload})
    check("wrong password rejected", status == 403, status)
    status, _ = post("/fama/save", {"password": PASSWORD, "data": payload})
    check("save succeeds", status == 200, status)

    print("SUMMARY: " + ("OK" if not failures else str(len(failures)) + " failed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
