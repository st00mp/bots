# bots

Pipelines Telegram autonomes de `worker-ts`. **Pas des agents** : aucun LLM de raisonnement,
aucune mémoire, aucun outil — des programmes qui font une chose. Les agents (Tank, Link)
vivent dans Hermes et sont versionnés dans `st00mp/agents`.

```
echo/   @echo_verbatim_bot — voix ↔ texte, bidirectionnel (Docker, production)
          vocal reçu → transcription texte   (Groq Whisper)
          texte reçu → lecture à voix haute  (TTS local)
v0x/    ancien lecteur de notes Markdown → voice notes (Kokoro). Remplacé par le
          chemin texte→voix d'echo ; conservé comme source des modèles Kokoro,
          montés en volume par echo.
```

Historique : ces deux bots vivaient dans `~/.openclaw/workspace-forge/agents/`, le
workspace d'un agent OpenClaw — avec un compose de production que
`/opt/infra/update-stacks.conf` devait épargner par une règle `hold`. Sortis le
2026-08-06 pendant la migration OpenClaw → Hermes.

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
