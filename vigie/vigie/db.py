"""Accès SQLite : schéma unique, connexion à la demande."""
import os
import pathlib
import sqlite3

BASE = pathlib.Path(os.environ.get("VIGIE_HOME", pathlib.Path(__file__).resolve().parent.parent))
DB_PATH = BASE / "data" / "vigie.db"

SCHEMA = """
create table if not exists articles(
  id          integer primary key,
  url_hash    text unique not null,
  url         text not null,
  source      text not null,
  title       text,
  published   text,
  fetched_at  text not null,
  content     text,
  status      text not null default 'new',  -- new | filtered_out | kept | synthesized | delivered
  theme       text,
  theme_score real,
  tripwire    integer not null default 0,
  impact      integer,
  summary     text,
  why         text,
  parking     integer not null default 0,
  delivered_at text
);
create table if not exists pages(
  source       text primary key,
  url          text not null,
  content_hash text,
  content      text,
  checked_at   text
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
