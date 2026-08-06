FROM python:3.11-slim

# L'IPv6 sortant de l'hôte est cassé et deb.debian.org résout en AAAA d'abord :
# sans ce forçage, apt-get échoue (exit 100). Même motif que network.force_ipv4
# côté Hermes.
RUN echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Versions épinglées sur celles validées dans tts-lab le 2026-08-06 :
# torch 2.13.0+cpu / coqui-tts 0.27.5 / transformers 4.57.6.
# La fenêtre transformers est étroite : >=4.55 (is_torchcodec_available)
# et <5 (isin_mps_friendly supprimé en 5.x).
RUN pip install --no-cache-dir torch==2.13.0 torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir \
        "coqui-tts[codec]==0.27.5" \
        "transformers==4.57.6" \
        python-telegram-bot==21.* \
        httpx \
        kokoro-onnx \
        soundfile

COPY bot.py phonemize_mixed.py respell_fr.py .

# Modèles montés en volume, jamais copiés dans l'image :
#   /models          → kokoro (secours)
#   /tmp/.local/...  → XTTS (HOME=/tmp, voir compose)
# /data → dico.json (overrides de prononciation, rw)
CMD ["python", "bot.py"]
