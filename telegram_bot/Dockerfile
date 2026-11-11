# Image du bot Telegram MobiDesk. Contexte de construction : un dossier qui
# contient app/ (modules de MobiDesk, sans l'interface) et telegram_bot/.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Africa/Algiers
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY telegram_bot/requirements.txt telegram_bot/requirements.txt
RUN pip install --no-cache-dir -r telegram_bot/requirements.txt

COPY app app
COPY telegram_bot telegram_bot
RUN useradd --uid 1000 --create-home bot && mkdir -p /data /srv/data && chown bot /data /srv/data
USER bot

CMD ["python", "-m", "telegram_bot.bot"]
