"""Filtre grossier : embeddings nomic-embed-text (Ollama local), cosinus contre les ancres."""
import os
import re
import sys

import httpx
import numpy as np
import yaml

from .db import BASE, connect

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def embed(texts: list[str], model: str) -> np.ndarray:
    r = httpx.post(f"{OLLAMA}/api/embed", json={"model": model, "input": texts}, timeout=300)
    r.raise_for_status()
    return np.asarray(r.json()["embeddings"], dtype=np.float32)


def cosine(mat: np.ndarray, vec: np.ndarray) -> np.ndarray:
    return (mat @ vec) / (np.linalg.norm(mat, axis=1) * np.linalg.norm(vec) + 1e-9)


def load_config() -> dict:
    return yaml.safe_load((BASE / "themes.yaml").read_text())


def run(apply: bool = True) -> None:
    cfg = load_config()
    model = cfg["embedding"]["model"]
    max_chars = cfg["embedding"]["max_chars"]
    themes = cfg["themes"]
    kw_re = re.compile(r"\b(" + "|".join(map(re.escape, cfg["tripwire_keywords"])) + r")\b", re.I)

    # sur le site d'une marque, tout la mentionne : son propre nom n'y vaut pas tripwire
    own_brand = {s["name"]: s.get("brand", "").lower()
                 for s in yaml.safe_load((BASE / "sources.yaml").read_text())["sources"]}

    con = connect()
    rows = con.execute("select id, source, title, content from articles where status='new'").fetchall()
    if not rows:
        print("rien à filtrer (aucun article en statut 'new')")
        return

    # nomic-embed-text : préfixes de tâche recommandés (query pour les ancres, document pour les textes)
    anchors = [f"search_query: {t['positive']}" for t in themes] \
        + [f"search_query: {t['negative']}" for t in themes] \
        + [f"search_query: {cfg['global_negative']}"]
    a = embed(anchors, model)
    n = len(themes)
    pos, neg, gneg = a[:n], a[n:2 * n], a[2 * n]

    kept, dropped = [], []
    for i in range(0, len(rows), 16):
        batch = rows[i:i + 16]
        docs = [f"search_document: {r['title'] or ''}\n{(r['content'] or '')[:max_chars]}" for r in batch]
        vecs = embed(docs, model)
        for r, v in zip(batch, vecs):
            margins = {
                t["key"]: float(cosine(pos[j:j + 1], v)[0] - max(cosine(neg[j:j + 1], v)[0], cosine(gneg[None], v)[0]))
                for j, t in enumerate(themes)
            }
            best_key, best_margin = max(margins.items(), key=lambda kv: kv[1])
            hits = {m.lower() for m in kw_re.findall(f"{r['title']}\n{r['content'] or ''}")}
            hit = bool(hits - {own_brand.get(r["source"], "")})
            passing = {k: m for k, m in margins.items()
                       if m >= next(t["threshold"] for t in themes if t["key"] == k)}
            if passing:
                theme, score = max(passing.items(), key=lambda kv: kv[1])
                kept.append((r, theme, score, bool(hit)))
            elif hit:  # garde-fou : mot-clé tripwire → tri fin quoi qu'il arrive
                kept.append((r, "t5-tripwires", best_margin, True))
            else:
                dropped.append((r, best_key, best_margin))

    if apply:
        for r, theme, score, hit in kept:
            con.execute("update articles set status='kept', theme=?, theme_score=?, tripwire=? where id=?",
                        (theme, score, int(hit), r["id"]))
        for r, *_ in dropped:
            con.execute("update articles set status='filtered_out' where id=?", (r["id"],))
        con.commit()

    print(f"{len(rows)} articles filtrés : {len(kept)} gardés, {len(dropped)} écartés\n")
    print("— gardés —")
    for r, theme, score, hit in sorted(kept, key=lambda x: -x[2]):
        flag = " [TRIPWIRE?]" if hit else ""
        print(f"  {theme:14} {score:+.3f}{flag}  {(r['title'] or '')[:70]}")
    print("\n— écartés les plus proches (pour régler les seuils) —")
    for r, key, margin in sorted(dropped, key=lambda x: -x[2])[:12]:
        print(f"  {key:14} {margin:+.3f}  {(r['title'] or '')[:70]}")
