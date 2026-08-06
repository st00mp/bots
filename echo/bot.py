#!/usr/bin/env python3
"""
Echo — @echo_verbatim_bot
Bot bidirectionnel voix ↔ texte. Aucun LLM.

  vocal reçu   →  transcription texte     (Groq Whisper, whisper-large-v3-turbo)
  texte reçu   →  lecture à voix haute    (Kokoro-82M ONNX local, voix ff_siwis)

La synthèse passe par une phonémisation PAR LANGUE (phonemize_mixed) : les
anglicismes techniques reçoivent leur phonétique anglaise au lieu d'être lus
à la française, et les marqueurs de bascule d'espeak — que Kokoro prononçait
comme des syllabes — sont supprimés. Voir phonemize_mixed.py pour le détail.
"""

import asyncio
import logging
import os
import re
import tempfile
import time

import httpx
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
# httpx loggue chaque URL au niveau INFO — y compris l'URL Telegram qui
# contient le TOKEN. Sans cette ligne, le token part dans docker logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3-turbo"

KOKORO_MODEL = os.environ.get("KOKORO_MODEL", "/models/kokoro-v1.0.onnx")
KOKORO_VOICES = os.environ.get("KOKORO_VOICES", "/models/voices-v1.0.bin")
TTS_VOICE = os.environ.get("TTS_VOICE", "ff_siwis")
TTS_SPEED = float(os.environ.get("TTS_SPEED", "1.0"))
# Telegram limite un voice à ~1 min de confort d'écoute ; on borne l'entrée.
TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "3000"))

START_TIME = time.time()
last_quota: dict = {}

# ── Moteur TTS : chargé paresseusement, une seule fois ─────────────────────────
_tts_lock = asyncio.Lock()
_tts = None  # (kokoro, tokenizer, detect)


def _load_tts():
    global _tts
    if _tts is None:
        from kokoro_onnx import Kokoro
        from kokoro_onnx.tokenizer import Tokenizer
        from phonemize_mixed import make_language_detector

        logger.info("Chargement de Kokoro (%s)…", KOKORO_MODEL)
        kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
        tokenizer = Tokenizer()
        detect = make_language_detector(tokenizer)
        _tts = (kokoro, tokenizer, detect)
        logger.info("Kokoro prêt — voix %s", TTS_VOICE)
    return _tts


def _chunk_text(text: str, max_chars: int = 400) -> list[str]:
    """Découpe aux fins de phrases pour éviter le rushing de Kokoro."""
    chunks, current = [], ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if current:
                chunks.append(current)
                current = ""
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", line) if len(line) > max_chars else [line]
        for s in sentences:
            if current and len(current) + len(s) + 1 > max_chars:
                chunks.append(current)
                current = s
            else:
                current = f"{current} {s}".strip()
    if current:
        chunks.append(current)
    return chunks


def _synthesize_to_ogg(text: str) -> str:
    """Texte → fichier OGG/Opus (chemin retourné). Bloquant, à passer à to_thread."""
    import subprocess

    import numpy as np
    import soundfile as sf
    from phonemize_mixed import phonemize_mixed

    kokoro, tokenizer, detect = _load_tts()

    pieces = []
    for chunk in _chunk_text(text):
        phonemes = phonemize_mixed(chunk, tokenizer, detect=detect)
        if not phonemes:
            continue
        samples, sample_rate = kokoro.create(
            phonemes, voice=TTS_VOICE, speed=TTS_SPEED, is_phonemes=True
        )
        pieces.append(samples)

    if not pieces:
        raise ValueError("rien à synthétiser")

    silence = np.zeros(int(0.3 * 24000), dtype=np.float32)
    audio = pieces[0]
    for p in pieces[1:]:
        audio = np.concatenate([audio, silence, p])

    wav_path = tempfile.mktemp(suffix=".wav")
    ogg_path = tempfile.mktemp(suffix=".ogg")
    sf.write(wav_path, audio, 24000)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
             "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", ogg_path],
            check=True,
        )
    finally:
        os.unlink(wav_path)
    return ogg_path


# ── Quota Groq (inchangé) ─────────────────────────────────────────────────────

def parse_quota_headers(headers: httpx.Headers) -> dict:
    return {k: v for k, v in headers.items() if k.startswith("x-ratelimit")}


def usage_bar(used: int, limit: int, width: int = 10) -> str:
    if limit == 0:
        return "[" + "?" * width + "]"
    filled = max(0, min(round(used / limit * width), width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def fmt_quota(q: dict) -> str:
    if not q:
        return "_(Aucune donnée — envoie un vocal d'abord)_"
    lines = []

    def row(label, limit_key, remaining_key, reset_key, unit=""):
        lim_s, rem_s = q.get(limit_key), q.get(remaining_key)
        rst = q.get(reset_key, "")
        if lim_s is None and rem_s is None:
            return
        try:
            lim, rem = int(lim_s), int(rem_s)
            used = lim - rem
            body = f"`{usage_bar(used, lim)}` {used}/{lim}{unit} utilisés ({used/lim*100:.1f}%)"
        except Exception:
            body = f"{rem_s} / {lim_s}{unit} restants"
        rst_str = f" — reset dans {rst}" if rst else ""
        lines.append(f"*{label}*{rst_str}\n{body}")

    row("Requêtes /min", "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests")
    row("Audio (sec) /heure", "x-ratelimit-limit-audio-seconds",
        "x-ratelimit-remaining-audio-seconds", "x-ratelimit-reset-audio-seconds", "s")
    if not lines:
        return "_(Aucune limite exposée par Groq pour ce plan)_"
    return "\n\n".join(lines) + "\n\n_Reset = fenêtre glissante, pas quota mensuel._"


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_help(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎙️ *Echo* — voix ↔ texte, sans LLM\n\n"
        "*Envoie un vocal* → transcription texte (Groq Whisper)\n"
        "*Envoie du texte* → lecture à voix haute (Kokoro, local)\n\n"
        "La lecture gère les anglicismes : commit, workflow, prompt…\n"
        "sont prononcés à l'anglaise dans une phrase française.\n\n"
        "*Commandes*\n"
        "/help — ce message\n"
        "/quota — limites API Groq en cours\n"
        "/model — modèles actifs\n"
        "/ping — statut + uptime"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def cmd_quota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📊 *Quota Groq — dernière transcription*\n\n" + fmt_quota(last_quota),
        parse_mode="Markdown",
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"🔊 STT : `{STT_MODEL}` (Groq)\n🗣️ TTS : `Kokoro-82M` voix `{TTS_VOICE}` (local)",
        parse_mode="Markdown",
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    h, r = divmod(int(time.time() - START_TIME), 3600)
    m, s = divmod(r, 60)
    await update.effective_message.reply_text(f"✅ Echo opérationnel\nUptime : {h}h {m}m {s}s")


async def transcribe_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vocal → texte."""
    global last_quota
    msg = update.effective_message
    file_obj = msg.voice or msg.audio
    if not file_obj:
        return

    try:
        await msg.reply_text("⏳")
    except Exception:
        pass

    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        suffix = ".ogg" if msg.voice else ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        with open(tmp_path, "rb") as audio_file:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    data={"model": STT_MODEL, "response_format": "text"},
                    files={"file": (os.path.basename(tmp_path), audio_file)},
                )
        os.unlink(tmp_path)

        quota = parse_quota_headers(response.headers)
        if quota:
            last_quota = quota

        if response.status_code == 200:
            transcript = response.text.strip()
            await msg.reply_text(transcript if transcript else "_(rien transcrit)_",
                                 parse_mode=None if transcript else "Markdown")
        else:
            logger.error("Groq error %d", response.status_code)
            await msg.reply_text(f"❌ Groq error {response.status_code}")
    except Exception as e:
        logger.exception("Transcription échouée")
        await msg.reply_text(f"❌ Erreur : {e}")


async def speak_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texte → vocal."""
    msg = update.effective_message
    text = (msg.text or "").strip()
    if not text:
        return
    if len(text) > TTS_MAX_CHARS:
        await msg.reply_text(
            f"✂️ Texte trop long ({len(text)} caractères, max {TTS_MAX_CHARS}). "
            f"Envoie-le en plusieurs messages."
        )
        return

    try:
        await msg.reply_text("🗣️")
    except Exception:
        pass

    ogg_path = None
    try:
        async with _tts_lock:  # Kokoro n'est pas thread-safe, une synthèse à la fois
            ogg_path = await asyncio.to_thread(_synthesize_to_ogg, text)
        with open(ogg_path, "rb") as f:
            await msg.reply_voice(voice=f)
    except Exception as e:
        logger.exception("Synthèse échouée")
        await msg.reply_text(f"❌ Erreur de synthèse : {e}")
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.unlink(ogg_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    httpx_request = HTTPXRequest(
        connect_timeout=15.0, read_timeout=30.0,
        write_timeout=60.0, pool_timeout=10.0,
    )
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).request(httpx_request).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("quota", cmd_quota))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, transcribe_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, speak_text))

    logger.info("Echo démarré — STT %s | TTS Kokoro voix %s", STT_MODEL, TTS_VOICE)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
