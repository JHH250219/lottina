FROM python:3.12-slim

WORKDIR /app

# Dependencies installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code & Assets kopieren
COPY lottina_api ./lottina_api

# Gunicorn als Entrypoint
CMD ["gunicorn", "-b", "0.0.0.0:8000", "lottina_api.app:app"]
