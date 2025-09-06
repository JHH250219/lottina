import os
from celery import Celery
broker = os.environ.get("REDIS_URL","redis://localhost:6379/0")
celery = Celery("lottina", broker=broker, backend=broker)
# Minimalstart: kein Task nötig; Worker startet trotzdem.
if __name__ == "__main__":
    celery.start()
