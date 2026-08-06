#!/usr/bin/env python3
"""
Echo — @echo_verbatim_bot
Bot bidirectionnel voix ↔ texte. Aucun LLM.

  vocal reçu   →  transcription texte    (Groq Whisper, whisper-large-v3-turbo)
  texte reçu   →  lecture à voix haute   (XTTS-v2 local, voix Damien Black ;
                                          secours : Kokoro-82M + phonémisation mixte)

Prononciation des anglicismes : réécriture phonétique déterministe AVANT synthèse
(respell_fr.py) — « vault » → « vôlte », « timeout » → « taïmaoute ». XTTS applique
son paramètre de langue à toute la phrase et n'accepte pas de phonèmes : le
dictionnaire est la technique standard, sans LLM, sans latence, sans réseau.
Enrichissement par la commande /dico (overrides persistés dans /data/dico.json).
"""

import asyncio
import json
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
# contient le TOKEN. Sans ces lignes, le token part dans docker logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3-turbo"

TTS_ENGINE = os.environ.get("TTS_ENGINE", "xtts")          # xtts | kokoro
XTTS_SPEAKER = os.environ.get("XTTS_SPEAKER", "Damien Black")
KOKORO_MODEL = os.environ.get("KOKORO_MODEL", "/models/kokoro-v1.0.onnx")
KOKORO_VOICES = os.environ.get("KOKORO_VOICES", "/models/voices-v1.0.bin")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "ff_siwis")
TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "3000"))
DICO_PATH = os.environ.get("DICO_PATH", "/data/dico.json")

os.environ.setdefault("COQUI_TOS_AGREED", "1")

START_TIME = time.time()
last_quota: dict = {}

from respell_fr import RESPELL, respell  # noqa: E402

# ── Overrides du dictionnaire, persistés et éditables par /dico ───────────────

def _load_dico() -> dict:
    try:
        with open(DICO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_dico(d: dict) -> None:
    os.makedirs(os.path.dirname(DICO_PATH), exist_ok=True)
    with open(DICO_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)


_overrides = _load_dico()
RESPELL.update(_overrides)
logger.info("Dictionnaire : %d entrées de base + %d overrides", len(RESPELL) - len(_overrides), len(_overrides))

# ── Moteurs TTS : chargés paresseusement, une synthèse à la fois ──────────────

_tts_lock = asyncio.Lock()
_xtts = None
_kokoro = None


def _load_xtts():
    global _xtts
    if _xtts is None:
        from TTS.api import TTS as CoquiTTS

        logger.info("Chargement de XTTS-v2…")
        t0 = time.time()
        _xtts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")
        logger.info("XTTS prêt en %.0f s — voix %s", time.time() - t0, XTTS_SPEAKER)
    return _xtts


def _load_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        from kokoro_onnx.tokenizer import Tokenizer
        from phonemize_mixed import make_language_detector

        logger.info("Chargement de Kokoro (secours)…")
        kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
        tokenizer = Tokenizer()
        detect = make_language_detector(tokenizer)
        _kokoro = (kokoro, tokenizer, detect)
    return _kokoro


def _wav_to_ogg(wav_path: str) -> str:
    import subprocess

    ogg_path = tempfile.mktemp(suffix=".ogg")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
         "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", ogg_path],
        check=True,
    )
    return ogg_path


def _synthesize_xtts(text: str) -> str:
    prepared = respell(text)
    tts = _load_xtts()
    wav_path = tempfile.mktemp(suffix=".wav")
    try:
        tts.tts_to_file(text=prepared, speaker=XTTS_SPEAKER, language="fr",
                        file_path=wav_path)
        return _wav_to_ogg(wav_path)
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def _synthesize_kokoro(text: str) -> str:
    import numpy as np
    import soundfile as sf
    from phonemize_mixed import phonemize_mixed

    kokoro, tokenizer, detect = _load_kokoro()
    chunks, current = [], ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for s in re.split(r"(?<=[.!?…])\s+", line):
            if current and len(current) + len(s) + 1 > 400:
                chunks.append(current)
                current = s
            else:
                current = f"{current} {s}".strip()
    if current:
        chunks.append(current)

    pieces = []
    for chunk in chunks:
        phonemes = phonemize_mixed(chunk, tokenizer, detect=detect)
        if phonemes:
            samples, _ = kokoro.create(phonemes, voice=KOKORO_VOICE,
                                       speed=1.0, is_phonemes=True)
            pieces.append(samples)
    if not pieces:
        raise ValueError("rien à synthétiser")

    silence = np.zeros(int(0.3 * 24000), dtype=np.float32)
    audio = pieces[0]
    for p in pieces[1:]:
        audio = np.concatenate([audio, silence, p])
    wav_path = tempfile.mktemp(suffix=".wav")
    try:
        sf.write(wav_path, audio, 24000)
        return _wav_to_ogg(wav_path)
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def _synthesize(text: str) -> tuple[str, str]:
    """Retourne (chemin_ogg, moteur_utilisé)."""
    if TTS_ENGINE == "kokoro":
        return _synthesize_kokoro(text), "kokoro"
    try:
        return _synthesize_xtts(text), "xtts"
    except Exception:
        logger.exception("XTTS a échoué — bascule sur Kokoro")
        return _synthesize_kokoro(text), "kokoro (secours)"


# ── Quota Groq ────────────────────────────────────────────────────────────────

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
        lines.append(f"*{label}*{f' — reset dans {rst}' if rst else ''}\n{body}")

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
    await update.effective_message.reply_text(
        "🎙️ *Echo* — voix ↔ texte, sans LLM\n\n"
        "*Envoie un vocal* → transcription texte (Groq Whisper)\n"
        "*Envoie du texte* → lecture à voix haute (XTTS-v2 local)\n\n"
        "*Commandes*\n"
        "/help — ce message\n"
        "/dico mot graphie — corriger la prononciation d'un mot\n"
        "/dico mot — voir la graphie d'un mot\n"
        "/quota — limites API Groq en cours\n"
        "/model — modèles actifs\n"
        "/ping — statut + uptime",
        parse_mode="Markdown",
    )


async def cmd_dico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestion du dictionnaire de prononciation, sans LLM : le jugement, c'est toi."""
    msg = update.effective_message
    args = context.args or []

    if not args:
        n_over = len(_overrides)
        await msg.reply_text(
            f"📖 {len(RESPELL)} entrées ({n_over} ajoutée(s) via /dico)\n"
            "Usage : `/dico mot graphie` pour ajouter ou corriger, `/dico mot` pour consulter.",
            parse_mode="Markdown",
        )
        return

    word = args[0].lower()
    if len(args) == 1:
        current = RESPELL.get(word)
        await msg.reply_text(
            f"`{word}` → `{current}`" if current else f"`{word}` : pas d'entrée — lu tel quel.",
            parse_mode="Markdown",
        )
        return

    graphie = " ".join(args[1:]).lower()
    RESPELL[word] = graphie
    _overrides[word] = graphie
    _save_dico(_overrides)
    await msg.reply_text(f"✅ `{word}` → `{graphie}` — effet immédiat.", parse_mode="Markdown")


async def cmd_quota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📊 *Quota Groq — dernière transcription*\n\n" + fmt_quota(last_quota),
        parse_mode="Markdown",
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"🔊 STT : `{STT_MODEL}` (Groq)\n"
        f"🗣️ TTS : `XTTS-v2` voix `{XTTS_SPEAKER}` (local, CPU)\n"
        f"🛟 Secours : `Kokoro-82M` voix `{KOKORO_VOICE}`",
        parse_mode="Markdown",
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    h, r = divmod(int(time.time() - START_TIME), 3600)
    m, s = divmod(r, 60)
    await update.effective_message.reply_text(f"✅ Echo opérationnel\nUptime : {h}h {m}m {s}s")


async def transcribe_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            if transcript:
                await msg.reply_text(transcript)
            else:
                await msg.reply_text("_(rien transcrit)_", parse_mode="Markdown")
        else:
            logger.error("Groq error %d", response.status_code)
            await msg.reply_text(f"❌ Groq error {response.status_code}")
    except Exception as e:
        logger.exception("Transcription échouée")
        await msg.reply_text(f"❌ Erreur : {e}")


async def speak_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (msg.text or "").strip()
    if not text:
        return
    if len(text) > TTS_MAX_CHARS:
        await msg.reply_text(
            f"✂️ Texte trop long ({len(text)} caractères, max {TTS_MAX_CHARS})."
        )
        return
    try:
        await msg.reply_text("🗣️")
    except Exception:
        pass
    ogg_path = None
    try:
        async with _tts_lock:
            ogg_path, engine = await asyncio.to_thread(_synthesize, text)
        with open(ogg_path, "rb") as f:
            await msg.reply_voice(voice=f)
        if engine != "xtts":
            await msg.reply_text(f"_(moteur : {engine})_", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Synthèse échouée")
        await msg.reply_text(f"❌ Erreur de synthèse : {e}")
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.unlink(ogg_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Précharge XTTS au démarrage : ~10 s modèle en cache, et la première
    # demande de l'utilisateur n'attend pas le chargement.
    if TTS_ENGINE == "xtts":
        try:
            _load_xtts()
        except Exception:
            logger.exception("Préchargement XTTS échoué — le secours Kokoro prendra le relais")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).request(HTTPXRequest(
        connect_timeout=15.0, read_timeout=30.0, write_timeout=60.0, pool_timeout=10.0,
    )).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("dico", cmd_dico))
    app.add_handler(CommandHandler("quota", cmd_quota))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, transcribe_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, speak_text))

    logger.info("Echo démarré — STT %s | TTS %s (%s)", STT_MODEL, TTS_ENGINE, XTTS_SPEAKER)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
