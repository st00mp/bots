#!/usr/bin/env python3
"""
send_telegram.py — Envoie des fichiers audio WAV vers Telegram via Bot API.

- Convertit WAV → OGG Opus (voice note Telegram)
- Envoie chaque section comme voice note avec le titre en légende
- Envoie le texte nettoyé comme message texte final (optionnel)

Usage :
  python3 send_telegram.py --chat-id <id> --json <synthesize_output.json>
  python3 send_telegram.py --chat-id <id> --wav <file.wav> [--caption "Titre"]

Token : env var V0X_TG_TOKEN (ne JAMAIS passer en argument sur CLI — historique shell)

Options :
  --chat-id      Chat ID Telegram (requis)
  --json         Fichier JSON produit par synthesize.py (mode full)
  --wav          Fichier WAV unique (mode simple)
  --caption      Légende pour mode --wav
  --send-text    Envoyer aussi le texte nettoyé (depuis JSON)
  --no-ogg       Envoyer en WAV brut (debug)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    # Fallback système
    import shutil
    ff = shutil.which('ffmpeg')
    if ff:
        return ff
    raise RuntimeError('ffmpeg introuvable. Installe imageio-ffmpeg dans le venv.')


def wav_to_ogg(wav_path: Path, ffmpeg_bin: str) -> Path:
    """Convertit WAV en OGG Opus, retourne le chemin du fichier temporaire."""
    ogg_path = wav_path.with_suffix('.ogg')
    cmd = [
        ffmpeg_bin, '-y',
        '-i', str(wav_path),
        '-c:a', 'libopus',
        '-b:a', '64k',
        '-vbr', 'on',
        '-application', 'voip',
        str(ogg_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'Conversion OGG échouée : {result.stderr[-500:]}')
    return ogg_path


def tg_request(token: str, method: str, data: dict = None, files: dict = None) -> dict:
    """Appel API Telegram Bot."""
    import urllib.request
    import urllib.parse

    url = f'https://api.telegram.org/bot{token}/{method}'

    if files:
        # Multipart form data
        import email.mime.multipart
        import email.mime.base
        import email.encoders

        boundary = b'----v0xboundary'
        body_parts = []

        for key, value in (data or {}).items():
            body_parts.append(
                b'--' + boundary + b'\r\n' +
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode() +
                str(value).encode() + b'\r\n'
            )

        for field, (filename, content, mime) in files.items():
            body_parts.append(
                b'--' + boundary + b'\r\n' +
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode() +
                f'Content-Type: {mime}\r\n\r\n'.encode() +
                content + b'\r\n'
            )

        body_parts.append(b'--' + boundary + b'--\r\n')
        body = b''.join(body_parts)
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary.decode()}'}

    else:
        body = json.dumps(data or {}).encode()
        headers = {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        raise RuntimeError(f'TG API {method} → {e.code}: {err_body}')


def send_voice(token: str, chat_id: str, audio_path: Path, caption: str = '', ffmpeg: str = None, use_ogg: bool = True):
    """Envoie un fichier audio comme voice note Telegram."""
    if use_ogg and ffmpeg:
        print(f'  → Conversion OGG...', file=sys.stderr)
        ogg_path = wav_to_ogg(audio_path, ffmpeg)
        send_path = ogg_path
        mime = 'audio/ogg'
    else:
        send_path = audio_path
        mime = 'audio/wav'

    size_kb = send_path.stat().st_size // 1024
    print(f'  → Envoi {send_path.name} ({size_kb} Ko)...', file=sys.stderr)

    data = {'chat_id': chat_id}
    if caption:
        data['caption'] = caption

    with open(send_path, 'rb') as f:
        content = f.read()

    result = tg_request(
        token, 'sendVoice',
        data=data,
        files={'voice': (send_path.name, content, mime)}
    )

    if not result.get('ok'):
        raise RuntimeError(f'sendVoice failed: {result}')

    return result


def send_text(token: str, chat_id: str, text: str):
    """Envoie un message texte Telegram (max 4096 chars, découpage auto)."""
    max_len = 4096
    parts = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for part in parts:
        result = tg_request(token, 'sendMessage', {'chat_id': chat_id, 'text': part})
        if not result.get('ok'):
            raise RuntimeError(f'sendMessage failed: {result}')


def main():
    parser = argparse.ArgumentParser(description='v0x : Envoi audio Telegram')
    parser.add_argument('--chat-id', required=True, help='Chat ID Telegram')
    parser.add_argument('--json', default=None, help='JSON produit par synthesize.py')
    parser.add_argument('--wav', default=None, help='Fichier WAV unique')
    parser.add_argument('--caption', default='', help='Légende (mode --wav)')
    parser.add_argument('--send-text', action='store_true', help='Envoyer le texte nettoyé')
    parser.add_argument('--no-ogg', action='store_true', help='Envoyer WAV brut (debug)')
    args = parser.parse_args()

    token = os.environ.get('V0X_TG_TOKEN')
    if not token:
        print('[v0x] Erreur : V0X_TG_TOKEN non défini.', file=sys.stderr)
        sys.exit(1)

    ffmpeg_bin = None
    if not args.no_ogg:
        try:
            ffmpeg_bin = get_ffmpeg()
            print(f'[v0x] ffmpeg : {ffmpeg_bin}', file=sys.stderr)
        except RuntimeError as e:
            print(f'[v0x] Warning : {e} — envoi WAV brut', file=sys.stderr)

    chat_id = args.chat_id

    if args.wav:
        # Mode simple
        wav_path = Path(args.wav)
        if not wav_path.exists():
            print(f'[v0x] Fichier introuvable : {wav_path}', file=sys.stderr)
            sys.exit(1)
        send_voice(token, chat_id, wav_path, args.caption, ffmpeg_bin, not args.no_ogg)
        print('[v0x] ✅ Audio envoyé.')

    elif args.json:
        # Mode full (résultat synthesize.py)
        with open(args.json, 'r', encoding='utf-8') as f:
            summary = json.load(f)

        sections = summary.get('sections', [])
        cleaned_text_path = summary.get('cleaned_text')

        if not sections:
            print('[v0x] Aucune section dans le JSON.', file=sys.stderr)
            sys.exit(1)

        print(f'[v0x] {len(sections)} section(s) à envoyer...', file=sys.stderr)

        for sec in sections:
            title = sec.get('title', f"Section {sec['index']}")
            wav_path = Path(sec['audio'])
            caption = f'🎧 {title}'
            print(f'[v0x] [{sec["index"]}/{len(sections)}] "{title}"', file=sys.stderr)
            send_voice(token, chat_id, wav_path, caption, ffmpeg_bin, not args.no_ogg)

        if args.send_text and cleaned_text_path:
            cpath = Path(cleaned_text_path)
            if cpath.exists():
                print('[v0x] Envoi du texte nettoyé...', file=sys.stderr)
                text = cpath.read_text(encoding='utf-8')
                send_text(token, chat_id, f'📝 Texte nettoyé :\n\n{text}')

        print(f'[v0x] ✅ {len(sections)} audio(s) envoyé(s).')

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
