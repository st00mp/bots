FROM python:3.12-slim

# ffmpeg : conversion WAV -> OGG/Opus (format voice note Telegram)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    python-telegram-bot==21.* \
    httpx \
    kokoro-onnx \
    soundfile

COPY bot.py phonemize_mixed.py .

# Les modèles Kokoro (360 Mo) sont montés en volume lecture seule — pas copiés
# dans l'image. Voir docker-compose.yml.
CMD ["python", "bot.py"]
