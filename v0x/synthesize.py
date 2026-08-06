#!/usr/bin/env python3
"""
synthesize.py — Pipeline complet v0x (Kokoro-82M ONNX) :
  1. Lit une note Markdown (fichier ou stdin)
  2. Nettoie pour l'oreille
  3. Découpe par section H2
  4. Synthétise chaque section avec Kokoro-ONNX (voix fr_FF-siwis ou ff_siwis)
  5. Écrit les fichiers audio WAV + le texte nettoyé dans un répertoire de sortie

Usage :
  python3 synthesize.py <input.md> [--output-dir <dir>]
  echo "## Section\nContenu" | python3 synthesize.py --output-dir /tmp/v0x_out

Options :
  --output-dir DIR   Répertoire de sortie (défaut : /tmp/v0x_<timestamp>)
  --model PATH       Chemin vers kokoro-v1.0.onnx
  --voices PATH      Chemin vers voices-v1.0.bin
  --voice NAME       Nom de la voix (défaut : ff_siwis)
  --speed FLOAT      Vitesse de lecture (défaut : 1.0)
  --no-clean         Passer le texte brut sans nettoyage
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_MODEL = SCRIPT_DIR / 'kokoro_models' / 'kokoro-v1.0.onnx'
DEFAULT_VOICES = SCRIPT_DIR / 'kokoro_models' / 'voices-v1.0.bin'
DEFAULT_VOICE = 'ff_siwis'  # seule voix française disponible
DEFAULT_LANG = 'fr-fr'     # code langue espeak pour le français

sys.path.insert(0, str(SCRIPT_DIR))
from clean_md import clean_markdown, split_by_h2


def slugify(text: str) -> str:
    text = text.lower()
    for src, dst in [('àáâãäå', 'a'), ('èéêë', 'e'), ('ìíîï', 'i'),
                     ('òóôõö', 'o'), ('ùúûü', 'u'), ('ç', 'c')]:
        for c in src:
            text = text.replace(c, dst)
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')[:50]


def chunk_text(text: str, max_chars: int = 400) -> list[str]:
    """
    Découpe le texte en morceaux de max_chars pour éviter le rushing de Kokoro.
    Coupe aux fins de phrases ou aux retours à la ligne.
    """
    chunks = []
    current = ''
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            if current:
                chunks.append(current.strip())
                current = ''
            continue
        # Si la ligne seule dépasse max_chars, la couper aux points
        if len(line) > max_chars:
            sentences = re.split(r'(?<=[.!?])\s+', line)
            for s in sentences:
                if len(current) + len(s) + 1 > max_chars and current:
                    chunks.append(current.strip())
                    current = s
                else:
                    current = (current + ' ' + s).strip() if current else s
        else:
            if len(current) + len(line) + 1 > max_chars and current:
                chunks.append(current.strip())
                current = line
            else:
                current = (current + ' ' + line).strip() if current else line

    if current:
        chunks.append(current.strip())
    return [c for c in chunks if c]


def synthesize_section(text: str, output_path: Path, kokoro, voice: str, speed: float, lang: str = 'fr-fr') -> bool:
    """Synthétise un texte (potentiellement long) et écrit le WAV."""
    import numpy as np
    import soundfile as sf

    chunks = chunk_text(text)
    all_audio = []

    for chunk in chunks:
        try:
            samples, sample_rate = kokoro.create(chunk, voice=voice, speed=speed, lang=lang)
            all_audio.append(samples)
        except Exception as e:
            print(f'[kokoro error] chunk={repr(chunk[:50])} : {e}', file=sys.stderr)
            continue

    if not all_audio:
        return False

    # Petit silence entre les chunks (0.3s)
    silence = np.zeros(int(0.3 * 24000), dtype=np.float32)
    combined = []
    for i, audio in enumerate(all_audio):
        combined.append(audio)
        if i < len(all_audio) - 1:
            combined.append(silence)

    final = np.concatenate(combined)
    sf.write(str(output_path), final, 24000)
    return output_path.exists() and output_path.stat().st_size > 0


def main():
    parser = argparse.ArgumentParser(description='v0x : Markdown → Audio (Kokoro-82M ONNX)')
    parser.add_argument('input', nargs='?', help='Fichier Markdown (ou stdin)')
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--model', default=str(DEFAULT_MODEL))
    parser.add_argument('--voices', default=str(DEFAULT_VOICES))
    parser.add_argument('--voice', default=DEFAULT_VOICE)
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--lang', default=DEFAULT_LANG, help='Code langue espeak (défaut: fr-fr)')
    parser.add_argument('--send-to', default=None, help='Chat ID Telegram destinataire')
    parser.add_argument('--tg-token', default=None, help='Token bot Telegram (ou env V0X_TG_TOKEN)')
    parser.add_argument('--no-clean', action='store_true')
    args = parser.parse_args()

    # Lecture entrée
    if args.input:
        with open(args.input, 'r', encoding='utf-8') as f:
            raw = f.read()
        base_name = Path(args.input).stem
    else:
        raw = sys.stdin.read()
        base_name = 'note'

    if not raw.strip():
        print('[v0x] Entrée vide.', file=sys.stderr)
        sys.exit(1)

    # Découpage H2 sur le texte brut, nettoyage par section
    raw_sections = split_by_h2(raw)
    sections = []
    cleaned_parts = []
    for title, content in raw_sections:
        clean_content = content if args.no_clean else clean_markdown(content)
        sections.append((title, clean_content))
        cleaned_parts.append(f'## {title}\n\n{clean_content}')

    cleaned = '\n\n'.join(cleaned_parts)

    tg_token = args.tg_token or os.environ.get('V0X_TG_TOKEN')
    chat_id = args.send_to

    print(f'[v0x] {len(sections)} section(s) détectée(s)', file=sys.stderr)

    # Répertoire de sortie
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = int(time.time())
        out_dir = Path(f'/tmp/v0x_{base_name}_{ts}')
    out_dir.mkdir(parents=True, exist_ok=True)

    # Texte nettoyé
    cleaned_path = out_dir / f'{base_name}_cleaned.txt'
    with open(cleaned_path, 'w', encoding='utf-8') as f:
        f.write(cleaned)
    print(f'[v0x] Texte nettoyé → {cleaned_path}', file=sys.stderr)

    # Init Kokoro
    model_path = Path(args.model)
    voices_path = Path(args.voices)

    if not model_path.exists():
        print(f'[v0x] Modèle introuvable : {model_path}', file=sys.stderr)
        sys.exit(1)
    if not voices_path.exists():
        print(f'[v0x] Voices introuvables : {voices_path}', file=sys.stderr)
        sys.exit(1)

    print(f'[v0x] Chargement du modèle Kokoro...', file=sys.stderr)
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(str(model_path), str(voices_path))

    # Synthèse par section
    outputs = []
    for idx, (title, content) in enumerate(sections, 1):
        slug = slugify(title) or f'section_{idx}'
        audio_path = out_dir / f'{idx:02d}_{slug}.wav'
        print(f'[v0x] [{idx}/{len(sections)}] Synthèse : "{title}"...', file=sys.stderr)

        section_text = f'{title}.\n\n{content}'
        ok = synthesize_section(section_text, audio_path, kokoro, args.voice, args.speed, lang=args.lang)

        if ok:
            print(f'[v0x]   → {audio_path} ({audio_path.stat().st_size // 1024} Ko)', file=sys.stderr)
            outputs.append({
                'index': idx,
                'title': title,
                'audio': str(audio_path),
                'text': content,
            })
        else:
            print(f'[v0x]   ✗ Échec pour "{title}"', file=sys.stderr)

    summary = {
        'output_dir': str(out_dir),
        'cleaned_text': str(cleaned_path),
        'sections': outputs,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
