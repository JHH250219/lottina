FROM python:3.12-slim

ARG APP_ENV=production

ENV PYTHONUNBUFFERED=1 \
    ENV=${APP_ENV} \
    FLASK_ENV=${APP_ENV} \
    FLASK_APP=lottina_api.app:app

WORKDIR /app

# Dependencies installieren
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code & Assets kopieren
COPY apps/api/lottina_api ./lottina_api

# Gunicorn als Entrypoint
CMD ["gunicorn", "-b", "0.0.0.0:8000", "lottina_api.app:app"]
