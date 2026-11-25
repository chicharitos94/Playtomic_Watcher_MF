# Playtomic-Watcher – elPadel Social Club Winterthur

Prüft per GitHub Actions etwa alle 5–15 Minuten die Turniere und Public Classes des
[elPadel Social Club](https://playtomic.com/clubs/elpadel-social-club) und schickt eine
Telegram-Nachricht, sobald in einem Event ein Platz frei wird oder ein neues Event mit freien Plätzen erscheint.

## Telegram-Befehle (an den Bot schicken)
| Befehl | Wirkung |
|---|---|
| `/aus` | Meldungen pausieren |
| `/an` | Meldungen wieder einschalten |
| `/status` | läuft oder pausiert? |
| `/liste` | aktuelle Events mit Belegung |

Befehle werden beim nächsten Lauf verarbeitet, also nach ein paar Minuten.

## Einrichtung
- Repository-Secret `PLAYTOMIC_WATCHER_MF_BOT` = Bot-Token von @BotFather
- Dem Bot einmal `/start` schreiben, dann wird dieser Chat automatisch verbunden.

## Komplett abschalten
GitHub → Tab **Actions** → „Playtomic Watcher“ → **⋯** → *Disable workflow*.

## Anpassen
In `watch.py`: `TITLE_FILTER` (nur bestimmte Events melden), `PAGES` (welche Bereiche geprüft werden).
