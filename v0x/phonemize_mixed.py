#!/usr/bin/env python3
"""
phonemize_mixed.py — phonémisation par langue pour v0x.

PROBLÈME RÉSOLU
───────────────
Kokoro synthétise depuis des phonèmes. v0x passait tout le texte à espeak avec
`lang="fr-fr"`, ce qui produit deux défauts distincts :

1. **Marqueurs prononcés.** espeak-fr bascule tout seul en anglais sur les mots
   qu'il reconnaît, et le signale par des marqueurs :

       "le workflow du worker"  →  "lə (en)wˈɜːkfləʊ(fr) dy (en)wˈɜːkə(fr)"

   Or `(`, `e`, `n`, `)`, `f`, `r` sont TOUS dans le vocabulaire de Kokoro. Les
   marqueurs survivent donc à la tokenisation et le modèle les prononce comme des
   syllabes — un « en » et un « fr » parasites autour de chaque mot anglais.

2. **Anglicismes non détectés.** Ceux qu'espeak-fr ne reconnaît pas reçoivent la
   phonétique française :

       commit  →  kɔmˈi      (au lieu de kəmˈɪt)
       prompt  →  pʁˈɔ̃       (au lieu de pɹˈɑːmpt)
       vault   →  vˈo        (au lieu de vˈɔlt)

STRATÉGIE
─────────
Découper le texte en segments par langue, phonémiser chaque segment avec le code
langue qui lui convient, retirer tout marqueur résiduel, puis concaténer. La voix
reste `ff_siwis` d'un bout à l'autre : aucun changement de timbre en milieu de
phrase, contrairement à une bascule de voix par mot.

Le résultat se passe à `Kokoro.create(..., is_phonemes=True)`.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Marqueurs de bascule de langue émis par espeak : (en), (fr), (en-us)…
_LANG_MARKER_RE = re.compile(r"\([a-z]{2}(?:-[a-z]{2})?\)")

# Découpage en mots en conservant la ponctuation et les espaces comme séparateurs.
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*")

# ─────────────────────────────────────────────────────────────────────────────
# Lexique des anglicismes techniques de st00mp.
#
# Règle d'inclusion STRICTE : uniquement des mots dont la prononciation attendue
# est anglaise dans une phrase française. Tout mot qui existe aussi en français
# avec une prononciation française légitime est EXCLU — mieux vaut sous-inclure
# que faire lire « agent » à l'anglaise.
#
# Volontairement absents pour cette raison : agent, cache, format, machine, note,
# service, session, simple, table, terminal, version, image, message, page,
# instance, migration, application.
# ─────────────────────────────────────────────────────────────────────────────
EN_LEXICON: set[str] = {
    # git & forge
    "commit", "commits", "merge", "rebase", "branch", "branches", "push", "pull",
    "request", "requests", "fork", "checkout", "staging", "diff", "repo", "repos",
    # infra & runtime
    "worker", "workers", "runtime", "backend", "frontend", "framework", "gateway",
    "container", "containers", "cluster", "endpoint", "endpoints", "stack", "build",
    "deploy", "deployment", "rollback", "uptime", "downtime", "timeout", "healthcheck",
    "firewall", "proxy", "load", "balancer", "sandbox", "wrapper", "shell", "script",
    "scripts", "daemon", "cron", "job", "jobs", "queue", "socket", "sockets",
    # agents & LLM
    "prompt", "prompts", "prompting", "token", "tokens", "embedding", "embeddings",
    "chunk", "chunks", "chunking", "skill", "skills", "tool", "tools", "toolset",
    "streaming", "inference", "fine", "tuning", "grounding", "hallucination",
    # données & vault
    "vault", "workflow", "workflows", "pipeline", "pipelines", "dashboard", "hook",
    "hooks", "issue", "issues", "backlog", "board", "tag", "tags", "tagging",
    "mapping", "parsing", "binding", "bindings", "polling", "logging", "tracking",
    "scoring", "output", "outputs", "input", "inputs", "patch", "layer", "layers",
    "thread", "threads", "review", "insight", "insights", "feature", "features",
    "update", "upgrade", "release", "changelog", "wildcard", "fallback",
}


def strip_lang_markers(phonemes: str) -> str:
    """Retire les marqueurs (xx) laissés par espeak, qui seraient prononcés."""
    return _LANG_MARKER_RE.sub("", phonemes)


def _normalize(word: str) -> str:
    return word.lower().strip("'’-")


def make_language_detector(tokenizer, base_lang: str = "fr-fr", probe_lang: str = "en-us"):
    """
    Retourne une fonction mot → 'en' | 'fr'.

    Deux signaux, dans cet ordre :
      1. le mot est dans EN_LEXICON ;
      2. espeak, en base_lang, émet un marqueur de bascule pour ce mot seul —
         c'est-à-dire qu'il l'a lui-même reconnu comme étranger.

    Le résultat est mémoïsé : espeak est appelé au plus une fois par mot distinct.
    """

    @lru_cache(maxsize=4096)
    def detect(word: str) -> str:
        w = _normalize(word)
        if not w:
            return "fr"
        if w in EN_LEXICON:
            return "en"
        try:
            probed = tokenizer.phonemize(w, lang=base_lang)
        except Exception:
            return "fr"
        return "en" if _LANG_MARKER_RE.search(probed) else "fr"

    return detect


def split_language_spans(text: str, detect) -> list[tuple[str, str]]:
    """
    Découpe le texte en segments (langue, portion_de_texte).

    Les mots consécutifs de même langue sont regroupés, ponctuation et espaces
    inclus, pour que la prosodie de chaque segment reste naturelle. Phonémiser
    mot par mot casserait les liaisons françaises.
    """
    spans: list[tuple[str, str]] = []
    cursor = 0
    current_lang: str | None = None
    span_start = 0

    for m in _WORD_RE.finditer(text):
        lang = detect(m.group(0))
        if current_lang is None:
            current_lang = lang
            span_start = 0
        elif lang != current_lang:
            # le segment court jusqu'au début de ce mot : la ponctuation
            # intercalaire reste avec le segment précédent
            spans.append((current_lang, text[span_start:m.start()]))
            span_start = m.start()
            current_lang = lang
        cursor = m.end()

    if current_lang is None:
        return [("fr", text)] if text.strip() else []

    spans.append((current_lang, text[span_start:]))
    return [(l, s) for l, s in spans if s.strip()]


def phonemize_mixed(
    text: str,
    tokenizer,
    base_lang: str = "fr-fr",
    foreign_lang: str = "en-us",
    detect=None,
) -> str:
    """
    Phonémise un texte français contenant des anglicismes.

    Retourne une chaîne de phonèmes prête pour Kokoro.create(is_phonemes=True),
    sans aucun marqueur de bascule.
    """
    if detect is None:
        detect = make_language_detector(tokenizer, base_lang, foreign_lang)

    parts: list[str] = []
    for lang, chunk in split_language_spans(text, detect):
        lang_code = foreign_lang if lang == "en" else base_lang
        try:
            ph = tokenizer.phonemize(chunk, lang=lang_code)
        except Exception:
            # en cas d'échec sur un segment, on retombe sur la langue de base
            ph = tokenizer.phonemize(chunk, lang=base_lang)
        parts.append(strip_lang_markers(ph).strip())

    return " ".join(p for p in parts if p)


if __name__ == "__main__":
    import sys
    from kokoro_onnx.tokenizer import Tokenizer

    tok = Tokenizer()
    detect = make_language_detector(tok)
    sample = " ".join(sys.argv[1:]) or (
        "Le workflow du worker a échoué : le commit n'est pas passé, "
        "et le prompt du vault renvoie un timeout."
    )

    print("Texte      :", sample)
    print()
    print("Segments   :")
    for lang, chunk in split_language_spans(sample, detect):
        print("   [%s] %r" % (lang, chunk))
    print()
    avant = tok.phonemize(sample, lang="fr-fr")
    apres = phonemize_mixed(sample, tok, detect=detect)
    print("AVANT      :", avant)
    print("APRÈS      :", apres)
    print()
    n = len(_LANG_MARKER_RE.findall(avant))
    print("Marqueurs prononcés supprimés :", n)
