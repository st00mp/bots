"""Livraison : mise en forme du digest et envoi Telegram.
Interface volontairement minimale (build → send) pour pouvoir ajouter un canal (TRMNL…) plus tard."""
import datetime
import html
import os
import sys

import httpx
import yaml

from .db import BASE, connect

API = "https://api.telegram.org"
MAX_LEN = 3900  # marge sous la limite Telegram de 4096


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def item_html(a, theme_names: dict, alert: bool = False) -> str:
    title = esc(a["title"])
    head = title if a["url"].startswith("vigie://") else f'<a href="{a["url"]}">{title}</a>'
    if alert:
        head = f"🚨 <b>ALERTE</b> — <b>{head}</b>"
    else:
        label = theme_names.get(a["theme"], a["theme"])
        head = f"<b>{head}</b>\n<i>{esc(label)} · impact {a['impact']}</i>"
    parts = [head]
    if a["summary"]:
        parts.append(esc(a["summary"]))
    if a["why"]:
        parts.append("→ " + esc(a["why"]))
    return "\n".join(parts)


def build(articles, theme_names: dict) -> list[str]:
    """Blocs du digest, dans l'ordre imposé : ALERTE → Thème 1 → reste par impact → Parking."""
    alerts = [a for a in articles if a["tripwire"]]
    parking = [a for a in articles if not a["tripwire"] and a["parking"]]
    main = [a for a in articles if not a["tripwire"] and not a["parking"] and a["impact"] >= 1]
    zeros = [a for a in articles if not a["tripwire"] and not a["parking"] and a["impact"] == 0]
    t1 = sorted([a for a in main if a["theme"] == "t1-ldm"], key=lambda a: -a["impact"])
    rest = sorted([a for a in main if a["theme"] != "t1-ldm"], key=lambda a: -a["impact"])

    today = datetime.date.today().isoformat()
    blocks = [f"🏔 <b>Vigie — digest du {today}</b>"]
    blocks += [item_html(a, theme_names, alert=True) for a in alerts]
    blocks += [item_html(a, theme_names) for a in t1]
    blocks += [item_html(a, theme_names) for a in rest]
    if parking:
        blocks.append("🅿️ <b>Parking</b> — <i>idées et corvées repérées, rien à construire</i>")
        blocks += [item_html(a, theme_names) for a in parking]
    if zeros:
        titles = " · ".join(esc((a["title"] or "")[:60]) for a in zeros)
        blocks.append(f"<i>Aussi passés au crible, sans impact : {titles}</i>")
    return blocks


def chunk(blocks: list[str]) -> list[str]:
    msgs, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > MAX_LEN:
            msgs.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        msgs.append(cur)
    return msgs


def send(messages: list[str]) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    with httpx.Client(timeout=30, transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1)) as c:
        for m in messages:
            r = c.post(f"{API}/bot{token}/sendMessage", json={
                "chat_id": chat_id, "text": m, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            if not r.json().get("ok"):
                raise RuntimeError(f"Telegram a refusé le message : {r.text[:300]}")


def run() -> None:
    con = connect()
    articles = con.execute(
        "select * from articles where status='synthesized' order by tripwire desc, impact desc"
    ).fetchall()
    if not articles:
        print("rien à livrer (aucun article en statut 'synthesized')")
        return
    themes = yaml.safe_load((BASE / "themes.yaml").read_text())["themes"]
    theme_names = {t["key"]: t["name"] for t in themes}
    messages = chunk(build(articles, theme_names))
    send(messages)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    con.executemany("update articles set status='delivered', delivered_at=? where id=?",
                    [(now, a["id"]) for a in articles])
    con.commit()
    n_alert = sum(1 for a in articles if a["tripwire"])
    print(f"digest livré : {len(articles)} articles en {len(messages)} message(s), dont {n_alert} ALERTE")
