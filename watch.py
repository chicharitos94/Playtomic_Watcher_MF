"""
Playtomic-Watcher fuer den elPadel Social Club Winterthur.

Prueft Turniere und Public Classes und schickt eine Telegram-Nachricht, sobald
in einem Event ein Platz frei wird (volles Event wird frei, oder neues Event
mit freien Plaetzen erscheint).

Steuerung per Telegram-Nachricht an den Bot:  /aus  /an  /status  /liste

Umgebungsvariablen:
  TELEGRAM_TOKEN    Bot-Token von @BotFather (Pflicht fuer Nachrichten)
  TELEGRAM_CHAT_ID  optional; sonst wird der Chat genommen, der dem Bot zuerst schreibt
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CLUB = "elpadel-social-club"
PAGES = ["competitions/tournaments", "academy/public-classes"]
# Nur Events melden, deren Titel eines dieser Woerter enthaelt (leer = alle), z.B. ["Sunrise"]
TITLE_FILTER: list[str] = []

STATE_FILE = Path(__file__).with_name("state.json")
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en",
}


def http_get(url: str) -> str:
    # Playtomic (CloudFront) blockt Pythons eigenen HTTP-Client, curl wird durchgelassen.
    cmd = ["curl", "-sS", "--fail", "--compressed", "-m", "30"]
    for k, v in UA.items():
        cmd += ["-H", f"{k}: {v}"]
    for attempt in range(3):
        out = subprocess.run(cmd + [url], capture_output=True)
        if out.returncode == 0:
            return out.stdout.decode("utf-8")
        print(f"::warning::Abruf {url} fehlgeschlagen (Versuch {attempt + 1}): "
              f"{out.stderr.decode('utf-8', 'replace').strip()}")
        if out.returncode == 22 and b"403" in out.stderr:
            break  # geblockt: Wiederholen bringt auf demselben Server nichts
        time.sleep(10)
    # Kein Fehler-Status: der naechste Lauf versucht es einfach wieder
    print("::warning::Playtomic nicht erreichbar, Pruefung uebersprungen")
    return None


# ---------- Playtomic ----------
def get_events(page: str) -> list[dict]:
    url = f"https://playtomic.com/clubs/{CLUB}/{page}"
    doc = http_get(url)
    if doc is None:
        return None
    doc = re.sub(r"(?s)<script.*?</script>", "", doc)
    doc = re.sub(r"(?s)<svg.*?</svg>", "", doc)
    # Next.js streamt Karteninhalt und Karten-Links in getrennten Bloecken, gleiche Reihenfolge
    chunks = doc.split('<div hidden id="S:')
    links = re.findall(r'href="(?:https://playtomic\.com)?(/activities/[a-z-]+/[0-9a-f-]{36})"', doc)
    cards = [c for c in chunks if 'data-slot="card"' in c and "/activities/" not in c]
    events = []
    for n, c in enumerate(cards):
        parts = [html.unescape(p).strip() for p in re.sub(r"<[^>]+>", "\n", c).split("\n")]
        parts = [p for p in parts if p]
        text = "\n".join(parts)
        cnt = re.search(r"(?m)^(\d+)\n/\n(\d+)$", text)
        if not cnt:
            continue
        date = next((p for p in parts if re.match(r"^\w+day, \w+ \d+ \| \d", p)), "")
        title = parts[parts.index(date) + 1] if date else parts[1]
        link = links[n] if len(links) == len(cards) else None
        events.append({
            "id": f"{page}|{date}|{title}",
            "title": title,
            "date": date.replace(" | ", " "),
            "taken": int(cnt.group(1)),
            "max": int(cnt.group(2)),
            "url": f"https://playtomic.com{link}" if link else url,
        })
    return events


# ---------- Telegram ----------
def tg(method: str, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"::warning::Telegram-Fehler bei {method}: {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
    except Exception as e:  # Netzwerkfehler sollen den Lauf nicht abbrechen
        print(f"::warning::Telegram-Fehler bei {method}: {type(e).__name__}")
    return {}


def send(state: dict, text: str):
    chat = os.environ.get("TELEGRAM_CHAT_ID") or state.get("chat_id")
    if not TOKEN or not chat:
        print("[keine Telegram-Verbindung] " + text)
        return
    tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview="true")


def fmt(e: dict) -> str:
    free = e["max"] - e["taken"]
    return (f"<b>{html.escape(e['title'])}</b>\n{html.escape(e['date'])}\n"
            f"{free} von {e['max']} Plätzen frei\n<a href=\"{e['url']}\">In Playtomic öffnen</a>")


def handle_commands(state: dict, events: list[dict]):
    if not TOKEN:
        print("::notice::TELEGRAM_TOKEN ist nicht gesetzt")
        return
    me = tg("getMe")
    res = tg("getUpdates", offset=state.get("tg_offset", 0), timeout=0)
    print(f"::notice::Telegram-Bot erreichbar: {bool(me.get('ok'))} | neue Nachrichten: "
          f"{len(res.get('result', []))} | Chat verbunden: {bool(state.get('chat_id') or os.environ.get('TELEGRAM_CHAT_ID'))}")
    for u in res.get("result", []):
        state["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        if not chat:
            continue
        allowed = os.environ.get("TELEGRAM_CHAT_ID") or state.get("chat_id")
        if not allowed:
            state["chat_id"] = allowed = str(chat)
            send(state, "✅ Verbunden! Ich melde dir hier freie Plätze bei elPadel.\n"
                        "Befehle: /aus /an /status /liste")
        if str(chat) != str(allowed):
            continue  # fremde Chats ignorieren
        cmd = (msg.get("text") or "").strip().lower().lstrip("/").split("@")[0]
        if cmd in ("aus", "stop", "pause", "off"):
            state["paused"] = True
            send(state, "⏸ Pausiert. Mit /an geht's weiter.")
        elif cmd in ("an", "ein", "on"):
            state["paused"] = False
            send(state, "▶️ Läuft wieder. Du bekommst Meldungen zu freien Plätzen.")
        elif cmd == "status":
            send(state, "⏸ Pausiert." if state.get("paused") else "▶️ Läuft – prüft etwa alle 5–15 Minuten.")
        elif cmd in ("liste", "list"):
            lines = [f"{'🔴' if e['taken'] >= e['max'] else '🟢'} {e['date']} – {html.escape(e['title'])} "
                     f"({e['taken']}/{e['max']})" for e in events]
            send(state, "\n".join(lines) if events is not None
                 else "Playtomic war diesmal nicht erreichbar – versuch es gleich nochmal.")


# ---------- Ablauf ----------
def main():
    state = json.loads(STATE_FILE.read_text("utf-8")) if STATE_FILE.exists() else {}
    first_run = "events" not in state
    known: dict = state.get("events", {})

    events = []
    for p in PAGES:
        got = get_events(p)
        if got is None:          # Playtomic hat diesen Server geblockt
            events = None
            break
        events += got
    if events == []:
        print("::error::Keine Events gefunden – hat sich die Seite geändert?")

    # Befehle immer verarbeiten, auch wenn Playtomic gerade nicht erreichbar war
    handle_commands(state, events)
    if not events:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
        return

    new_known = {}
    for e in events:
        full = e["taken"] >= e["max"]
        new_known[e["id"]] = full  # gespeichert wird nur "voll ja/nein" -> wenig Commits
        prev = known.get(e["id"])
        wanted = not TITLE_FILTER or any(f.lower() in e["title"].lower() for f in TITLE_FILTER)
        if first_run or state.get("paused") or full or not wanted:
            continue
        if prev is True:
            send(state, "🎾 <b>Platz wieder frei!</b>\n" + fmt(e))
            print("Gemeldet (wieder frei):", e["title"], e["date"])
        elif prev is None:
            send(state, "🆕 <b>Neues Event</b>\n" + fmt(e))
            print("Gemeldet (neu):", e["title"], e["date"])

    state["events"] = new_known
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
    for e in events:
        print(f"{e['taken']:>3}/{e['max']:<3} {e['date']:<32} {e['title']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        print(f"::error::{type(exc).__name__} in Zeile {tb.lineno} ({tb.name}): {str(exc).replace(TOKEN, '***') if TOKEN else exc}")
        raise
