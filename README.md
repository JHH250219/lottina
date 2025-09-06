# 🌐 Lottina

Lottina ist eine Plattform für Freizeitangebote.  
Angebote können automatisch (Crawler), manuell oder per OCR (Plakat-Scan) erstellt werden.  
Die Anwendung besteht aus **API, Worker und Datenbank** und läuft komplett auf europäischer Infrastruktur.

---

## 🚀 Architektur

- **Frontend (Templates)**: Jinja2 + TailwindCSS
- **Backend (API)**: Flask, ausgeliefert über Gunicorn
- **Worker**: Celery (Crawler, OCR, Tagging, Geocoding)
- **Database**: PostgreSQL (IONOS), optional PostGIS
- **Search**: Meilisearch (optional)
- **Cache**: Redis (optional)
- **Storage**: IONOS S3
- **Hosting**: Sliplane (EU Cloud, Docker-basiert)
- **Registry**: GitHub Container Registry (GHCR)
- **CI/CD**: GitHub Actions → Build & Push Docker Images

---

## 📂 Projektstruktur

# 🌐 Lottina

Lottina ist eine Plattform für Freizeitangebote.  
Angebote können automatisch (Crawler), manuell oder per OCR (Plakat-Scan) erstellt werden.  
Die Anwendung besteht aus **API, Worker und Datenbank** und läuft komplett auf europäischer Infrastruktur.

---

## 🚀 Architektur

- **Frontend (Templates)**: Jinja2 + TailwindCSS
- **Backend (API)**: Flask, ausgeliefert über Gunicorn
- **Worker**: Celery (Crawler, OCR, Tagging, Geocoding)
- **Database**: PostgreSQL (IONOS), optional PostGIS
- **Search**: Meilisearch (optional)
- **Cache**: Redis (optional)
- **Storage**: IONOS S3
- **Hosting**: Sliplane (EU Cloud, Docker-basiert)
- **Registry**: GitHub Container Registry (GHCR)
- **CI/CD**: GitHub Actions → Build & Push Docker Images

---

## 📂 Projektstruktur


---

## 🐳 Docker Images

- `ghcr.io/<user>/lottina-api:dev` → API (Flask)
- `ghcr.io/<user>/lottina-worker:dev` → Worker (Celery)

Build lokal:
```bash
docker buildx build --platform linux/amd64 -t ghcr.io/<user>/lottina-api:dev ./apps/api --push
docker buildx build --platform linux/amd64 -t ghcr.io/<user>/lottina-worker:dev ./apps/worker --push
