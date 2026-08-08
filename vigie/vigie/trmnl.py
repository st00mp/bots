"""Écran TRMNL : payload teaser (≤ 2 Ko) poussé au webhook du plugin.
Le TRMNL est une vitrine passive — le digest complet vit sur Telegram."""
import datetime
import json
import os
import sys

import httpx

from .db import connect

MOIS = ["JAN", "FÉV", "MAR", "AVR", "MAI", "JUIN",
        "JUIL", "AOÛT", "SEP", "OCT", "NOV", "DÉC"]
MAX_BYTES = 1900  # marge sous la limite webhook de 2 Ko


def _cut(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_payload(con) -> dict | None:
    row = con.execute(
        "select date(delivered_at) d from articles where delivered_at is not null "
        "order by delivered_at desc limit 1").fetchone()
    if not row:
        return None
    day = row["d"]
    arts = con.execute(
        "select * from articles where status='delivered' and date(delivered_at)=? "
        "order by tripwire desc, impact desc, case when theme='t1-ldm' then 0 else 1 end",
        (day,)).fetchall()
    if not arts:
        return None

    une = next((a for a in arts if not a["parking"]), arts[0])
    others = [a for a in arts if a["id"] != une["id"] and (a["impact"] or 0) >= 1][:5]
    scanned = con.execute("select count(*) from articles where date(fetched_at)=?", (day,)).fetchone()[0]
    d = datetime.date.fromisoformat(day)

    payload = {
        "date": f"{d.day:02d} {MOIS[d.month - 1]}",
        "scan": scanned,
        "gardes": len(arts),
        "alertes": sum(1 for a in arts if a["tripwire"]),
        "alerte": bool(une["tripwire"]),
        "une": {
            "titre": _cut(une["title"], 90),
            "resume": _cut(une["summary"], 260),
            "pourquoi": _cut(une["why"], 110),
        },
        "items": [{"t": _cut(a["title"], 60), "s": _cut(a["why"] or a["summary"], 80),
                   "i": a["impact"] or 0} for a in others],
    }
    while len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_BYTES and payload["items"]:
        payload["items"].pop()
    return payload


def push() -> None:
    uuid = os.environ.get("TRMNL_PLUGIN_UUID")
    if not uuid:
        print("TRMNL_PLUGIN_UUID absent : écran TRMNL désactivé")
        return
    payload = build_payload(connect())
    if payload is None:
        print("rien à pousser vers TRMNL (aucun digest livré)")
        return
    body = json.dumps({"merge_variables": payload}, ensure_ascii=False).encode()
    with httpx.Client(timeout=30, transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1)) as c:
        r = c.post(f"https://trmnl.com/api/custom_plugins/{uuid}",
                   content=body, headers={"Content-Type": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"TRMNL a refusé le payload : {r.status_code} {r.text[:200]}")
    print(f"écran TRMNL poussé : {len(body)} octets, {len(payload['items'])} items"
          + (", ALERTE" if payload["alerte"] else ""))
