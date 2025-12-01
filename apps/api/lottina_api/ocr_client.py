"""HTTP client for delegating OCR work to the external Windows service."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Final

import requests


MAX_RETRIES: Final[int] = 3
REQUEST_TIMEOUT_SECONDS: Final[int] = 20


class OCRClientError(RuntimeError):
    """Raised when the remote OCR service cannot be reached or fails."""


def _require_settings() -> tuple[str, str]:
    url = os.getenv("OCR_URL")
    api_key = os.getenv("OCR_API_KEY")
    if not url or not api_key:
        raise OCRClientError("OCR_URL and OCR_API_KEY must be configured")
    return url, api_key


def run_ocr(image_path: str) -> str:
    """Send the image at ``image_path`` to the remote OCR server and return text."""

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"OCR source not found: {image_path}")

    url, api_key = _require_settings()
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with path.open("rb") as handle:
                response = requests.post(
                    url,
                    headers={"X-OCR-Key": api_key},
                    files={"image": (path.name, handle, "application/octet-stream")},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            response.raise_for_status()
            payload = response.json()
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str):
                raise OCRClientError("Remote OCR response did not include 'text'")
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= MAX_RETRIES:
                break
            time.sleep(attempt)  # simple linear backoff

    raise OCRClientError("Remote OCR request failed") from last_error


__all__ = ["run_ocr", "OCRClientError"]
