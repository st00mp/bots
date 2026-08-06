#!/usr/bin/env python3
"""
respell_fr.py — réécriture phonétique des anglicismes pour un TTS francophone.

XTTS applique son paramètre de langue à toute la phrase : un mot anglais dans
une phrase française est lu à la française (« vault » → « veau », « timeout » →
« timote »). Il n'accepte ni bascule par mot ni phonèmes en entrée.

Ce module remplace donc, AVANT synthèse, chaque anglicisme connu par une graphie
française qui se lit approximativement comme l'anglais. C'est l'équivalent d'un
dictionnaire de prononciation — la technique standard quand le moteur n'expose
pas d'entrée phonémique.

Règle d'or du lexique : n'inclure QUE des mots dont la lecture française
spontanée est fautive. En cas de doute, ne pas réécrire — une réécriture ratée
est pire que l'accent français sur un mot anglais.
"""

from __future__ import annotations

import re

# anglicisme (minuscules) → graphie française approximante
RESPELL: dict[str, str] = {
    # git & forge
    "commit": "commite",
    "commits": "commites",
    "merge": "meurdje",
    "rebase": "ribéïze",
    "branch": "brantche",
    "push": "pouche",
    "pull": "poule",
    "request": "riquouest",
    "fork": "forque",
    "checkout": "tchèkaoute",
    "diff": "dif",
    "staging": "stéïdjigne",
    # infra & runtime
    "worker": "ouorkeur",
    "workers": "ouorkeurs",
    "workflow": "ouorkflo",
    "workflows": "ouorkflos",
    "runtime": "reunetaïme",
    "backend": "baquènde",
    "frontend": "fronntènde",
    "framework": "fréïmeouorque",
    "gateway": "guéïteoué",
    "timeout": "taïmaoute",
    "firewall": "faïreouôle",
    "healthcheck": "hèlstchèque",
    "deploy": "diploï",
    "build": "bilde",
    "rollback": "rôlbaque",
    "uptime": "euptaïme",
    "shell": "chèle",
    "script": "scripte",
    "scripts": "scriptes",
    "daemon": "dîmone",
    "queue": "quiou",
    "stack": "staque",
    "cluster": "cleusteur",
    "endpoint": "èndepoïnte",
    "sandbox": "sandboxe",
    "socket": "soquette",
    # agents & LLM
    "prompt": "prommpte",
    "prompts": "prommptes",
    "token": "tôkeune",
    "tokens": "tôkeunes",
    "embedding": "èmbèdigne",
    "embeddings": "èmbèdignes",
    "chunk": "tcheunque",
    "chunks": "tcheunques",
    "skill": "skil",
    "skills": "skils",
    "streaming": "strimigne",
    "inference": "inn'fœrennce",
    # données & vault
    "vault": "vôlte",
    "pipeline": "païpelaïne",
    "pipelines": "païpelaïnes",
    "dashboard": "dachebôrde",
    "hook": "houque",
    "hooks": "houques",
    "issue": "ichiou",
    "issues": "ichious",
    "backlog": "baklogue",
    "tag": "tague",
    "tags": "tagues",
    "mapping": "mapigne",
    "parsing": "parsigne",
    "polling": "pôligne",
    "logging": "loguigne",
    "output": "aoutepoute",
    "input": "innepoute",
    "patch": "patche",
    "thread": "srède",
    "threads": "srèdes",
    "review": "riviou",
    "feature": "fitcheur",
    "features": "fitcheurs",
    "update": "eupdéïte",
    "upgrade": "eupgréïde",
    "release": "rilize",
    "fallback": "fôlbaque",
    "wildcard": "ouaïldecarde",
}

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*")


def respell(text: str) -> str:
    """Remplace les anglicismes connus par leur graphie française approximante.

    La casse initiale est préservée en tête de mot (Commit → Commite).
    """

    def _sub(m: re.Match) -> str:
        word = m.group(0)
        rep = RESPELL.get(word.lower())
        if rep is None:
            return word
        if word[0].isupper():
            rep = rep[0].upper() + rep[1:]
        return rep

    return _WORD_RE.sub(_sub, text)


if __name__ == "__main__":
    import sys

    sample = " ".join(sys.argv[1:]) or (
        "Le workflow du worker a échoué : le commit n'est pas passé, "
        "et le prompt du vault renvoie un timeout. "
        "Je relance le pipeline après le merge de la pull request."
    )
    print("AVANT :", sample)
    print("APRÈS :", respell(sample))
