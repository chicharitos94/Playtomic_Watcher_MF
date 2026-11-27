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
_raw_token = os.environ.get("TELEGRAM_TOKEN", "")
# Token auch dann finden, wenn beim Kopieren Text/Leerzeichen/Anfuehrungszeichen mitgekommen sind
_m = re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", _raw_token)
TOKEN = _m.group(0) if _m else _raw_token.strip()
if _raw_token:
    print(f"::notice::Secret-Laenge {len(_raw_token)} Zeichen, Token-Format erkannt: {bool(_m)}"
          + ("" if not _m or _m.group(0) == _raw_token else " (Zusatztext entfernt)"))
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


def handle_commands(state: dict, events, wait: int = 0):
    """Liest neue Telegram-Nachrichten. Mit wait>0 wartet Telegram bis zu wait Sekunden
    auf eine Nachricht (Long-Polling) – so antwortet der Bot sofort."""
    if not TOKEN:
        time.sleep(wait)
        return
    res = tg("getUpdates", offset=state.get("tg_offset", 0), timeout=wait)
    if not res:
        time.sleep(min(wait, 30))
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
            last = state.get("last_check")
            send(state, ("⏸ Pausiert." if state.get("paused") else f"▶️ Läuft – prüft alle {INTERVAL // 60} Minuten.")
                 + (f"\nLetzte erfolgreiche Prüfung: {last}" if last else ""))
        elif cmd in ("liste", "list"):
            lines = [f"{'🔴' if e['taken'] >= e['max'] else '🟢'} {e['date']} – {html.escape(e['title'])} "
                     f"({e['taken']}/{e['max']})" for e in (events or [])]
            send(state, "\n".join(lines) if events
                 else "Playtomic war eben nicht erreichbar – versuch es in ein paar Minuten nochmal.")


# ---------- Ablauf ----------
INTERVAL = 180  # Sekunden zwischen zwei Playtomic-Pruefungen


def save(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")


def fetch_all():
    events = []
    for p in PAGES:
        got = get_events(p)
        if got is None:          # Playtomic hat diesen Server geblockt
            return None
        events += got
    if not events:
        print("::error::Keine Events gefunden – hat sich die Seite geändert?")
    return events


def check(state: dict, events: list[dict]):
    first_run = "events" not in state
    known: dict = state.get("events", {})
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
    state["last_check"] = time.strftime("%d.%m. %H:%M", time.localtime())


def main():
    """Laeuft bis zu --minutes Minuten und prueft alle INTERVAL Sekunden.
    Exit-Code 3 = dieser Server wird von Playtomic geblockt -> Workflow startet neu."""
    minutes = int(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 0
    deadline = time.time() + minutes * 60
    state = json.loads(STATE_FILE.read_text("utf-8")) if STATE_FILE.exists() else {}
    blocked, ok = 0, 0
    while True:
        events = fetch_all()
        if events is None:
            blocked += 1
            print(f"::warning::Playtomic blockt diesen Server ({blocked}x)")
        else:
            blocked = 0
            ok += 1
            if events:
                check(state, events)
        save(state)
        if blocked >= 2 or time.time() + INTERVAL > deadline:
            break
        # Wartezeit fuer Telegram-Befehle nutzen (Antwort kommt sofort)
        next_check = time.time() + INTERVAL
        while time.time() < next_check - 5:
            handle_commands(state, events, wait=int(min(50, next_check - time.time())))
            save(state)
    handle_commands(state, events)
    save(state)
    print(f"::notice::{ok} erfolgreiche Pruefungen, Ende wegen {'Blockade' if blocked >= 2 else 'Zeitlimit'}")
    if blocked >= 2:
        sys.exit(3)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        print(f"::error::{type(exc).__name__} in Zeile {tb.lineno} ({tb.name}): {str(exc).replace(TOKEN, '***') if TOKEN else exc}")
        raise
