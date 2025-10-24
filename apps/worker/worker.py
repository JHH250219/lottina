# apps/worker/worker.py
from __future__ import annotations

import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

# Pfad für absolute Imports (Projektwurzel)
sys.path.append(str(Path(__file__).resolve().parents[2]))

from celery import Celery

# Settings robust importieren (je nach Projektstruktur)
try:
    from apps.worker.config.settings import Settings
except ImportError:
    from config.settings import Settings  # Fallback, falls top-level import gewünscht ist

# Pipeline-Bausteine
from apps.worker.tasks.fetch import fetch_listing
from apps.worker.tasks.extract import extract_details
from apps.worker.tasks.normalize import normalize_rows
from apps.worker.tasks.enrich import enrich_rows
from apps.worker.tasks.upsert import upsert_rows

# Monitoring (JSON-Report auf Platte)
try:
    from apps.worker.tasks.monitor import write_report
except Exception:
    # Minimaler Fallback, falls monitor.py (noch) fehlt
    def write_report(slug: str, report: dict, directory: Optional[str] = None) -> str:
        directory = directory or "/tmp/worker_reports"
        Path(directory).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        p = Path(directory) / f"{slug}-{ts}.json"
        p.write_text(json.dumps(report, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)

# ---------------------------------------------------------------------------
# Celery Setup
# ---------------------------------------------------------------------------

celery_app = Celery(
    "lottina_worker",
    broker=Settings.CELERY_BROKER_URL,
    backend=Settings.CELERY_BACKEND_URL,
)

celery_app.conf.update(
    task_default_queue=getattr(Settings, "CELERY_DEFAULT_QUEUE", "crawler"),
    task_acks_late=True,
    task_time_limit=getattr(Settings, "CELERY_TASK_TIME_LIMIT", 600),
    worker_max_tasks_per_child=getattr(Settings, "CELERY_MAX_TASKS_PER_CHILD", 50),
    worker_prefetch_multiplier=getattr(Settings, "CELERY_PREFETCH_MULTIPLIER", 1),
)

# Logger
logger = logging.getLogger("lottina.worker")
if not logger.handlers:
    logging.basicConfig(
        level=getattr(Settings, "LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

# ---------------------------------------------------------------------------
# Orchestrator-Funktionen
# ---------------------------------------------------------------------------

def run_slug(slug: str, limit: Optional[int] = None, *, write_monitor_file: bool = True) -> Dict[str, Any]:
    """
    Führt die komplette Pipeline für einen Slug aus.
    """
    logger.info(f"[{slug}] start pipeline (limit={limit})")

    listing = fetch_listing(slug)
    urls = [r["url"] for r in listing]
    if limit:
        urls = urls[:limit]

    logger.info(f"[{slug}] listing found={len(listing)} to_process={len(urls)}")

    extracted = extract_details(slug, urls, limit=None)
    normalized = normalize_rows(slug, extracted)
    enriched = enrich_rows(normalized)
    stats = upsert_rows(enriched)
    report = {
        "slug": slug,
        "found": len(listing),
        "processed": len(urls),
        "inserted": stats.get("inserted", 0),
        "updated": stats.get("updated", 0),
        "skipped": stats.get("skipped", 0),
        "errors": stats.get("errors", 0),
        "error_samples": stats.get("error_samples", [])[:3],  # erste 3 Beispiele
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


    # --- Im Orchestrator nach run_slug: Monitoring-Report schreiben ----------
    try:
        p = write_report(slug, report)
        logger.info(f"[{slug}] monitor report written -> {p}")
        report["report_path"] = p
    except Exception as e:
        logger.warning(f"[{slug}] failed to write monitor report: {e}")

    logger.info(f"[{slug}] done: {report}")
    return report


def run_all(slugs: List[str], limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Führt die Pipeline für mehrere Slugs aus und aggregiert die Ergebnisse.
    """
    summary = {
        "total_found": 0,
        "total_processed": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "by_slug": [],
        "timestamp": datetime.now().isoformat(),
    }

    for s in slugs:
        try:
            res = run_slug(s, limit=limit, write_monitor_file=True)
            summary["by_slug"].append(res)
            summary["total_found"] += res.get("found", 0)
            summary["total_processed"] += res.get("processed", 0)
            for k in ("inserted", "updated", "skipped", "errors"):
                summary[k] += res.get(k, 0)
        except Exception as e:
            logger.exception(f"[{s}] pipeline failed: {e}")
            summary["by_slug"].append({"slug": s, "error": str(e)})
            summary["errors"] += 1

    # Sammelreport schreiben
    try:
        p = write_report("batch", summary)
        logger.info(f"[batch] monitor report written -> {p}")
        summary["report_path"] = p
    except Exception as e:
        logger.warning(f"[batch] failed to write batch report: {e}")

    return summary

# ---------------------------------------------------------------------------
# Celery Tasks
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="crawler.run_slug",
    autoretry_for=(Exception,),
    retry_backoff=2,          # 2s, 4s, 8s …
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def crawl_slug(self, slug: str, limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Celery-Task: einen Slug crawlen.
    """
    return run_slug(slug, limit=limit, write_monitor_file=True)


@celery_app.task(
    bind=True,
    name="crawler.run_all",
    autoretry_for=(Exception,),
    retry_backoff=2,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def crawl_many(self, slugs: List[str], limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Celery-Task: mehrere Slugs sequenziell verarbeiten und aggregieren.
    (Einfach & robust. Wenn du parallelisieren willst, kannst du ein group() bauen.)
    """
    return run_all(slugs, limit=limit)

# ---------------------------------------------------------------------------
# Optional: CLI-Modus (lokal direkt starten)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lottina worker orchestrator")
    parser.add_argument("slugs", nargs="*", help="Source slugs, e.g. 'kingkalli-events kaenguru-online'")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of detail URLs per slug")
    args = parser.parse_args()

    slugs = args.slugs or ["kingkalli-events", "kaenguru-online"]
    summary = run_all(slugs, limit=args.limit)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
