#!/usr/bin/env python3
"""
bot.py — v0x Telegram Bot
Reçoit une note Markdown, synthétise, répond en voice notes OGG.

Commandes :
  /start    — message de bienvenue
  /help     — aide complète
  /voix     — synthétiser le texte qui suit la commande
  /vitesse  — changer la vitesse de lecture (0.5 - 2.0)
  /status   — état du bot + chemin des modèles

Usage de base :
  Envoyer un message texte contenant du Markdown → synthèse automatique
  Envoyer un fichier .md ou .txt → synthèse automatique
  /voix Bonjour tout le monde → synthèse rapide d'un court texte

Token : fichier .env ou variable V0X_TG_TOKEN
"""

import json
import os
import re
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

# ─── Config ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
ENV_FILE = SCRIPT_DIR / '.env'

def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

TOKEN = os.environ.get('V0X_TG_TOKEN')
if not TOKEN:
    print('[v0x-bot] ERREUR : V0X_TG_TOKEN manquant (.env ou env var)', file=sys.stderr)
    sys.exit(1)

MODEL_PATH   = SCRIPT_DIR / 'kokoro_models' / 'kokoro-v1.0.onnx'
VOICES_PATH  = SCRIPT_DIR / 'kokoro_models' / 'voices-v1.0.bin'
DEFAULT_VOICE = 'ff_siwis'
DEFAULT_LANG  = 'fr-fr'
DEFAULT_SPEED = 1.0

sys.path.insert(0, str(SCRIPT_DIR))
from clean_md import clean_markdown, split_by_h2
from synthesize import synthesize_section, slugify, chunk_text
from send_telegram import wav_to_ogg, tg_request, get_ffmpeg

# ─── State ─────────────────────────────────────────────────────────────────
user_speed: dict[int, float] = {}   # chat_id → vitesse

# ─── Telegram polling ──────────────────────────────────────────────────────
def tg(method: str, data: dict = None, files: dict = None) -> dict:
    return tg_request(TOKEN, method, data, files)

def get_updates(offset: int, timeout: int = 30) -> list:
    """Long-poll Telegram. urllib timeout = timeout + 10s pour éviter read timeout."""
    import socket
    url = f'https://api.telegram.org/bot{TOKEN}/getUpdates'
    params = urllib.parse.urlencode({
        'offset': offset,
        'timeout': timeout,
        'allowed_updates': '["message"]'
    })
    req = urllib.request.Request(f'{url}?{params}')
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            return json.loads(resp.read()).get('result', [])
    except (socket.timeout, urllib.error.URLError):
        return []  # timeout normal, pas de messages — silencieux
    except Exception as e:
        print(f'[poll] Erreur getUpdates : {e}', file=sys.stderr)
        return []

def send_msg(chat_id: int, text: str):
    try:
        tg('sendMessage', {'chat_id': chat_id, 'text': text})
    except Exception as e:
        print(f'[send_msg] {e}', file=sys.stderr)

def send_typing(chat_id: int):
    try:
        tg('sendChatAction', {'chat_id': chat_id, 'action': 'record_voice'})
    except Exception:
        pass

def download_file(file_id: str) -> bytes:
    r = tg('getFile', {'file_id': file_id})
    file_path = r['result']['file_path']
    url = f'https://api.telegram.org/file/bot{TOKEN}/{file_path}'
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()

# ─── Kokoro (init lazy) ────────────────────────────────────────────────────
_kokoro = None

def get_kokoro():
    global _kokoro
    if _kokoro is None:
        print('[v0x] Chargement Kokoro...', file=sys.stderr)
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        print('[v0x] Kokoro prêt.', file=sys.stderr)
    return _kokoro

# ─── Pipeline ──────────────────────────────────────────────────────────────
def process_markdown(chat_id: int, raw: str, source_name: str = 'note'):
    speed = user_speed.get(chat_id, DEFAULT_SPEED)
    kokoro = get_kokoro()
    ffmpeg_bin = get_ffmpeg()

    raw_sections = split_by_h2(raw)
    sections = []
    for title, content in raw_sections:
        clean = clean_markdown(content)
        sections.append((title, clean))

    count = len(sections)
    send_msg(chat_id, f'🎙 {count} section(s) détectée(s), synthèse en cours...')

    with tempfile.TemporaryDirectory(prefix='v0x_') as tmp:
        tmp_path = Path(tmp)
        errors = 0

        for idx, (title, content) in enumerate(sections, 1):
            print(f'[bot] [{idx}/{count}] synthèse : "{title}"...', file=sys.stderr)
            send_typing(chat_id)
            slug = slugify(title) or f'section_{idx}'
            wav_path = tmp_path / f'{idx:02d}_{slug}.wav'

            section_text = f'{title}.\n\n{content}'
            ok = synthesize_section(section_text, wav_path, kokoro, DEFAULT_VOICE, speed, lang=DEFAULT_LANG)

            if not ok:
                print(f'[bot] ✗ section {idx} échouée', file=sys.stderr)
                send_msg(chat_id, f'⚠️ Échec section {idx}/{count} : "{title}"')
                errors += 1
                continue

            print(f'[bot]   WAV ok ({wav_path.stat().st_size//1024} Ko), conversion OGG...', file=sys.stderr)
            ogg_path = wav_to_ogg(wav_path, ffmpeg_bin)
            print(f'[bot]   OGG ok ({ogg_path.stat().st_size//1024} Ko), envoi TG...', file=sys.stderr)
            caption = f'🎧 {idx}/{count} — {title}'

            with open(ogg_path, 'rb') as f:
                ogg_bytes = f.read()

            tg_request(
                TOKEN, 'sendVoice',
                data={'chat_id': chat_id, 'caption': caption},
                files={'voice': (ogg_path.name, ogg_bytes, 'audio/ogg')}
            )

        if errors == 0:
            send_msg(chat_id, f'✅ Terminé — {count} audio(s) envoyé(s)')
        else:
            send_msg(chat_id, f'⚠️ Terminé avec {errors} erreur(s) sur {count}')

# ─── Handlers commandes ────────────────────────────────────────────────────
def handle_start(chat_id: int):
    send_msg(chat_id,
        "🎙 v0x\n"
        "Tes notes Markdown, lues à voix haute.\n\n"
        "→ Colle du texte ou envoie un fichier .md\n"
        "Je m'occupe du reste.\n\n"
        "/help pour les commandes."
    )

def handle_help(chat_id: int):
    send_msg(chat_id,
        "🎙 v0x\n"
        "───────────────\n"
        "📋 Texte collé\n"
        "Envoie ta note directement — front matter ignoré,\n"
        "Markdown nettoyé, sections lues dans l'ordre.\n\n"
        "📎 Fichier .md ou .txt\n"
        "Même pipeline, sans copier-coller.\n\n"
        "───────────────\n"
        "/voix <texte>   — synthèse rapide\n"
        "/status         — état du bot\n"
        "/help           — ce message"
    )

def handle_vitesse(chat_id: int, args: str):
    args = args.strip()
    try:
        v = float(args)
        if not (0.5 <= v <= 2.0):
            raise ValueError()
        user_speed[chat_id] = v
        send_msg(chat_id, f'✅ Vitesse réglée à {v}')
    except ValueError:
        send_msg(chat_id, '⚠️ Vitesse invalide. Plage : 0.5 à 2.0\nEx : /vitesse 1.2')

def handle_status(chat_id: int):
    model_ok = '✅' if MODEL_PATH.exists() else '❌'
    voices_ok = '✅' if VOICES_PATH.exists() else '❌'
    send_msg(chat_id,
        f"📊 v0x — état\n\n"
        f"Modèle kokoro : {model_ok}\n"
        f"Voices : {voices_ok}\n"
        f"Vitesse : {user_speed.get(chat_id, DEFAULT_SPEED)}\n"
        f"Voix : {DEFAULT_VOICE}\n"
        f"Langue : {DEFAULT_LANG}"
    )

def handle_voix(chat_id: int, text: str):
    if not text.strip():
        send_msg(chat_id, '⚠️ Usage : /voix <texte à synthétiser>')
        return
    send_typing(chat_id)
    process_markdown(chat_id, text, source_name='commande')

# ─── Dispatcher messages ───────────────────────────────────────────────────
def dispatch(message: dict):
    chat_id = message['chat']['id']
    sender = message.get('from', {}).get('username', '?')
    print(f'[bot] message from @{sender} ({chat_id})', file=sys.stderr)
    text = message.get('text', '')
    doc = message.get('document')

    # Fichier .md ou .txt
    if doc:
        fname = doc.get('file_name', '')
        if fname.endswith(('.md', '.txt')):
            send_typing(chat_id)
            send_msg(chat_id, f'📥 Fichier reçu : {fname}')
            try:
                raw = download_file(doc['file_id']).decode('utf-8', errors='replace')
                process_markdown(chat_id, raw, source_name=fname)
            except Exception as e:
                send_msg(chat_id, f'❌ Erreur : {e}')
                traceback.print_exc()
        else:
            send_msg(chat_id, '⚠️ Format non supporté. Envoie un fichier .md ou .txt')
        return

    if not text:
        return

    # Commandes
    if text.startswith('/start'):
        handle_start(chat_id)
    elif text.startswith('/help'):
        handle_help(chat_id)
    elif text.startswith('/status'):
        handle_status(chat_id)
    elif text.startswith('/vitesse'):
        handle_vitesse(chat_id, text[len('/vitesse'):])
    elif text.startswith('/voix'):
        handle_voix(chat_id, text[len('/voix'):].strip())

    # Texte libre → synthèse si contient du Markdown ou est assez long
    elif len(text) >= 20:
        send_typing(chat_id)
        process_markdown(chat_id, text, source_name='message')

    else:
        send_msg(chat_id, '💬 Envoie du Markdown (texte ou fichier .md) pour synthétiser.')

# ─── Main loop ─────────────────────────────────────────────────────────────
def main():
    print('[v0x-bot] Démarrage...', file=sys.stderr)

    if not MODEL_PATH.exists():
        print(f'[v0x-bot] ERREUR : modèle Kokoro manquant → {MODEL_PATH}', file=sys.stderr)
        sys.exit(1)

    # Vérification connexion
    try:
        me = tg('getMe')
        bot_name = me['result']['username']
        print(f'[v0x-bot] Connecté : @{bot_name}', file=sys.stderr)
    except Exception as e:
        print(f'[v0x-bot] ERREUR connexion TG : {e}', file=sys.stderr)
        sys.exit(1)

    offset = 0
    print('[v0x-bot] En écoute...', file=sys.stderr)

    while True:
        try:
            updates = get_updates(offset, timeout=30)
            for upd in updates:
                offset = upd['update_id'] + 1
                msg = upd.get('message')
                if msg:
                    try:
                        dispatch(msg)
                    except Exception as e:
                        chat_id = msg.get('chat', {}).get('id')
                        traceback.print_exc()
                        if chat_id:
                            try:
                                send_msg(chat_id, f'❌ Erreur inattendue : {e}')
                            except Exception:
                                pass
        except KeyboardInterrupt:
            print('\n[v0x-bot] Arrêt.', file=sys.stderr)
            break
        except Exception as e:
            print(f'[v0x-bot] Erreur boucle principale : {e}', file=sys.stderr)
            time.sleep(5)


if __name__ == '__main__':
    main()
