# v0x — Agent TTS local (Markdown → Audio Telegram)

## Rôle
Reçoit une note Markdown (chemin ou contenu collé) et produit :
1. Le texte nettoyé pour l'oreille (audio-friendly)
2. Un fichier audio OGG Opus par section H2, via Kokoro-82M ONNX (voix française)
3. Envoi direct vers Telegram en voice notes

## Usage rapide
```bash
cd agents/v0x
source venv/bin/activate

# Synthèse seule → JSON de sortie
python synthesize.py ma_note.md --output-dir /tmp/v0x_out > /tmp/v0x_out/summary.json

# Synthèse + envoi Telegram
python synthesize.py ma_note.md --output-dir /tmp/v0x_out > /tmp/v0x_out/summary.json
V0X_TG_TOKEN="<token>" python send_telegram.py --chat-id <chat_id> --json /tmp/v0x_out/summary.json

# Envoi avec texte nettoyé
V0X_TG_TOKEN="<token>" python send_telegram.py --chat-id <chat_id> --json summary.json --send-text
```

## Stack
- **TTS** : Kokoro-82M ONNX v1.0 (local, aucune API payante)
- **Voix** : `ff_siwis` (voix française, qualité correcte)
- **Lang** : `fr-fr` (code espeak requis)
- **Conversion** : WAV → OGG Opus via ffmpeg (imageio-ffmpeg bundlé)
- **Node** : worker UM690
- **Python** : venv isolé (3.13 + kokoro-onnx + soundfile + onnxruntime)

## Configuration
Token Telegram : variable d'env `V0X_TG_TOKEN` uniquement.
Ne JAMAIS stocker le token dans un fichier ou le passer en argument CLI.

## Structure des fichiers
```
agents/v0x/
├── AGENT.md              # ce fichier
├── clean_md.py           # nettoyage Markdown → texte audio
├── synthesize.py         # pipeline TTS (Kokoro)
├── send_telegram.py      # livraison Telegram (WAV→OGG + Bot API)
├── venv/                 # Python venv (kokoro-onnx, soundfile, onnxruntime, imageio-ffmpeg)
├── bin/                  # binaire Piper (conservé en fallback)
└── kokoro_models/
    ├── kokoro-v1.0.onnx    # modèle principal (225 Mo)
    └── voices-v1.0.bin     # pack de voix (25 Mo)
```

## Règles de nettoyage (invariantes)
- Titres, gras, italique → texte nu
- Code inline → texte nu
- Blocs de code → "[bloc de code X omis]"
- Tableaux → phrases "header : valeur"
- Liens → texte nu (URL supprimée)
- Schémas ASCII, HR, HTML → supprimés
- **Invariant** : le sens n'est jamais modifié

## Découpage audio
- Un audio par section `##` — navigation facile à l'écoute fatiguée
- Nommage : `01_introduction.ogg`, `02_titre_section.ogg`...
- Titre lu en intro de chaque section
- Texte nettoyé sauvegardé à côté (`*_cleaned.txt`)

## Sorties synthesize.py
```json
{
  "output_dir": "/tmp/v0x_xxx",
  "cleaned_text": "/tmp/v0x_xxx/note_cleaned.txt",
  "sections": [
    { "index": 1, "title": "...", "audio": "path/to/01_xxx.wav", "text": "..." }
  ]
}
```

## Limitations connues
- Blocs de code → omis sans résumé intelligent
- Voix ff_siwis : qualité correcte, prononciation anglicismes imperfaite
- Premier chargement Kokoro : ~3s (ONNX init)
