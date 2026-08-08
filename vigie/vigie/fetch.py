"""Collecte : RSS, scraping de pages de listing, surveillance page-diff."""
import datetime
import difflib
import hashlib
import re
import sqlite3
import sys
from urllib.parse import urlparse

import feedparser
import httpx
import trafilatura
import yaml
from lxml import html as lhtml

from .db import BASE, connect

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# local_address force la pile IPv4 : l'IPv6 sortant du worker est cassé
client = httpx.Client(
    headers={"User-Agent": UA, "Accept-Language": "fr-BE,fr;q=0.9,nl;q=0.7,en;q=0.5"},
    timeout=20,
    follow_redirects=True,
    transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1),
)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def known(con: sqlite3.Connection, url: str) -> bool:
    return con.execute("select 1 from articles where url_hash=?", (url_hash(url),)).fetchone() is not None


def insert_article(con, source, url, title, published, content) -> bool:
    try:
        con.execute(
            "insert into articles(url_hash,url,source,title,published,fetched_at,content) values(?,?,?,?,?,?,?)",
            (url_hash(url), url, source, title, published, now(), content),
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get(url: str) -> str | None:
    try:
        r = client.get(url)
        r.raise_for_status()
        return r.text
    except httpx.HTTPError as e:
        print(f"    ! {url} : {e}", file=sys.stderr)
        return None


def extract_page(html_text: str) -> tuple[str | None, str | None]:
    """(titre, texte) extraits du HTML."""
    text = trafilatura.extract(html_text, include_comments=False, favor_recall=True)
    meta = trafilatura.extract_metadata(html_text)
    return (meta.title if meta else None), text


def strip_html(s: str) -> str:
    try:
        return lhtml.fromstring(s).text_content().strip()
    except Exception:
        return s


# ---------------------------------------------------------------- RSS

def fetch_rss(con, src) -> int:
    r = client.get(src["url"])
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    added = 0
    for e in feed.entries[: src.get("max_items", 15)]:
        url = e.get("link")
        if not url or known(con, url):
            continue
        html_text = get(url)
        _, text = extract_page(html_text) if html_text else (None, None)
        content = text or strip_html(e.get("summary", ""))
        if content and insert_article(con, src["name"], url, e.get("title", ""),
                                      e.get("published", "") or e.get("updated", ""), content):
            added += 1
    return added


# ---------------------------------------------------------------- listing

def candidate_links(base_url: str, html_text: str) -> list[str]:
    """Liens sortants du listing qui ressemblent à des articles (même domaine, slug long ou chemin profond)."""
    tree = lhtml.fromstring(html_text)
    tree.make_links_absolute(base_url)
    base = urlparse(base_url)
    base_path = base.path.rstrip("/")
    seen, out = set(), []
    for el, attr, link, _ in tree.iterlinks():
        if getattr(el, "tag", None) != "a" or attr != "href":
            continue
        u = link.split("#")[0].split("?")[0].rstrip("/")
        p = urlparse(u)
        if p.scheme not in ("http", "https") or p.netloc != base.netloc:
            continue
        if not p.path or p.path.rstrip("/") == base_path:
            continue
        if p.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".zip", ".xml", ".css", ".js")):
            continue
        segs = [s for s in p.path.split("/") if s]
        if not segs or (len(segs[-1]) < 12 and len(segs) < 3):
            continue  # slugs courts type /contact, /fr/equipe
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_listing(con, src) -> int:
    listing = get(src["url"])
    if listing is None:
        return 0
    include = re.compile(src["include"]) if src.get("include") else None
    added = 0
    for url in candidate_links(src["url"], listing):
        if include and not include.search(url):
            continue
        if added >= src.get("max_items", 10):
            break
        if known(con, url):
            continue
        html_text = get(url)
        if html_text is None:
            continue
        title, text = extract_page(html_text)
        # un vrai article a du corps ; les pages de nav tombent ici
        if text and len(text) > 400 and insert_article(con, src["name"], url, title or url, "", text):
            added += 1
    return added


# ---------------------------------------------------------------- page-diff

def fetch_pagediff(con, src) -> int:
    html_text = get(src["url"])
    if html_text is None:
        return 0
    _, text = extract_page(html_text)
    if not text:
        return 0
    h = hashlib.sha256(text.encode()).hexdigest()
    row = con.execute("select content_hash, content from pages where source=?", (src["name"],)).fetchone()
    con.execute(
        "insert into pages(source,url,content_hash,content,checked_at) values(?,?,?,?,?) "
        "on conflict(source) do update set content_hash=excluded.content_hash, "
        "content=excluded.content, checked_at=excluded.checked_at",
        (src["name"], src["url"], h, text, now()),
    )
    con.commit()
    if row is None or row["content_hash"] == h:
        return 0  # première visite ou rien de neuf
    diff = "\n".join(difflib.unified_diff(row["content"].splitlines(), text.splitlines(), lineterm=""))
    content = f"Changement détecté sur la page {src['url']} :\n\n{diff[:3000]}\n\n--- Page actuelle ---\n{text[:4000]}"
    # l'URL est suffixée par le hash : chaque changement est un article distinct
    if insert_article(con, src["name"], f"{src['url']}#diff-{h[:12]}",
                      f"[Changement de page] {src['name']}", "", content):
        return 1
    return 0


# ---------------------------------------------------------------- entrée

HANDLERS = {"rss": fetch_rss, "listing": fetch_listing, "page-diff": fetch_pagediff}


def run() -> None:
    cfg = yaml.safe_load((BASE / "sources.yaml").read_text())
    con = connect()
    total = 0
    for src in cfg["sources"]:
        try:
            added = HANDLERS[src["type"]](con, src)
        except Exception as e:
            print(f"  {src['name']:24} ERREUR : {e}", file=sys.stderr)
            continue
        total += added
        print(f"  {src['name']:24} +{added}")
    print(f"{total} nouveaux articles collectés")
