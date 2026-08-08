# bots

Pipelines Telegram autonomes de `worker-ts`. **Pas des agents** : aucun LLM de raisonnement,
aucune mémoire, aucun outil — des programmes qui font une chose. Les agents (Tank, Link)
vivent dans Hermes et sont versionnés dans `st00mp/agents`.

```
echo/     @echo_verbatim_bot — voix ↔ texte, bidirectionnel (Docker, production)
            vocal reçu → transcription texte   (Groq Whisper)
            texte reçu → lecture à voix haute  (XTTS-v2, secours Kokoro)
models/   poids des modèles montés en volume par echo — hors git, reproductibles
```

Historique : ces bots vivaient dans `~/.openclaw/workspace-forge/agents/`, le
workspace d'un agent OpenClaw — avec un compose de production que
`/opt/infra/update-stacks.conf` devait épargner par une règle `hold`. Sortis le
2026-08-06 pendant la migration OpenClaw → Hermes.

`v0x` lisait des notes Markdown en voice notes. Sa fonction a été absorbée par le
chemin texte→voix d'`echo` le 2026-08-06 ; il a été supprimé le 08, avec ses
artefacts reproductibles (venv, modèles). Son code reste dans l'historique git.
Les poids Kokoro ont migré vers `models/` : un bot vivant n'a pas à dépendre du
répertoire d'un bot mort.

## Prononciation française : le correctif `phonemize_mixed`

Kokoro phonémise via espeak. En `fr-fr`, deux défauts :

1. les anglicismes reconnus par espeak déclenchent une bascule signalée par des
   marqueurs `(en)…(fr)` — dont tous les caractères sont dans le vocabulaire de
   Kokoro, qui les **prononce** ;
2. les anglicismes non reconnus (`commit` → `kɔmˈi`, `vault` → `vˈo`) reçoivent la
   phonétique française.

`echo/phonemize_mixed.py` segmente le texte par langue (lexique technique + sondage
espeak mot à mot, mémoïsé), phonémise chaque segment dans sa langue, supprime les
marqueurs, et passe le résultat à `Kokoro.create(is_phonemes=True)`. Une seule voix
(`ff_siwis`) d'un bout à l'autre.

## Secrets

`echo/.env` (mode 600, jamais versionné) : `TELEGRAM_TOKEN`, `GROQ_API_KEY`.
Le logger `httpx` est forcé à WARNING dans `bot.py` : au niveau INFO il journalise
l'URL Telegram complète, token compris, dans `docker logs`.
