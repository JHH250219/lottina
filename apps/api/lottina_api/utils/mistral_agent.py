"""
Integration helpers for the Mistral chat API to post-process OCR results.

This module intentionally stays lightweight (requests only) so it can be
imported during app bootstrap without pulling in heavy OCR dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-large-latest"
DEFAULT_TIMEOUT = 15.0


_FIELD_ORDER: Iterable[str] = (
    "title",
    "summary",
    "description",
    "date",
    "time",
    "time_end",
    "location",
    "category",
    "categories",
    "age_group",
    "price",
    "price_info",
    "is_free",
    "is_outdoor",
    "registration",
    "opening_hours",
    "contact",
    "maps_url",
    "source_url",
    "source_name",
)

_SYSTEM_PROMPT = (
    "Du bist ein fachkundiger Assistent für Veranstaltungsdaten. "
    "Du erhältst rohen OCR-Text zusammen mit bereits erkannten Feldern. "
    "Analysiere die Informationen und liefere eine strukturierte JSON-Antwort, "
    "die nur die Felder enthält, die du mit hoher Sicherheit bestimmen kannst. "
    "Nutze ISO-Formate (Datum als YYYY-MM-DD, Zeit als HH:MM). "
    "Nutze `categories` als Liste von kurzen Schlagworten. "
    "Schreibe boolesche Werte als true/false. "
    "Wenn du ein Feld nicht sicher befüllen kannst, lasse es weg."
)


def _extract_json_from_response(content: str) -> Optional[Dict[str, Any]]:
    """Try to parse the first JSON object contained in the string."""
    if not content:
        return None

    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.debug("Failed to decode JSON from Mistral response chunk: %s", match.group(0))
        return None


def _call_mistral(messages: list[dict[str, str]]) -> Optional[Dict[str, Any]]:
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        logger.info("Mistral API key not configured; skipping LLM enrichment.")
        return None

    api_url = os.getenv("MISTRAL_API_URL", DEFAULT_API_URL)
    model = os.getenv("MISTRAL_MODEL", DEFAULT_MODEL)
    timeout_raw = os.getenv("MISTRAL_TIMEOUT")
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 500,
    }

    try:
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Mistral request failed: %s", exc)
        return None

    if resp.status_code >= 400:
        logger.warning("Mistral API returned %s: %s", resp.status_code, resp.text[:500])
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.warning("Mistral API response was not valid JSON: %s", resp.text[:200])
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Unexpected Mistral API shape: %s", data)
        return None

    return _extract_json_from_response(content)


def _clean_value(value: Any) -> Any:
    """Ensure the output values are JSON serialisable and trimmed."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, list):
        out = []
        for item in value:
            cleaned = _clean_value(item)
            if cleaned is not None:
                out.append(cleaned)
        return out
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            cleaned = _clean_value(v)
            if cleaned is not None:
                out[k] = cleaned
        return out
    cleaned = str(value).strip()
    return cleaned or None


def enrich_fields_with_mistral(text: str, base_fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Ask Mistral to map OCR text to structured fields.

    Returns only the fields that Mistral could confidently determine. The caller
    is responsible for merging them with existing values.
    """
    text = (text or "").strip()
    if not text:
        return {}

    truncated_text = text[:8000]  # keep payload size under control
    base_snapshot = base_fields or {}

    user_prompt = (
        "Hier ist der OCR-Text:\n"
        "<ocr>\n"
        f"{truncated_text}\n"
        "</ocr>\n\n"
        "Bereits erkannte Felder (können Lücken enthalten):\n"
        f"{json.dumps(base_snapshot, ensure_ascii=False)}\n\n"
        "Liefere eine JSON-Struktur mit den Feldern: "
        f"{', '.join(_FIELD_ORDER)}. "
        "Lasse Felder weg, wenn du sie nicht sicher ausfüllen kannst."
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    ai_result = _call_mistral(messages)
    if not ai_result:
        return {}

    cleaned: Dict[str, Any] = {}
    for key in _FIELD_ORDER:
        if key in ai_result:
            value = _clean_value(ai_result[key])
            if value not in (None, "", []):
                cleaned[key] = value

    # allow passthrough of additional keys if needed
    for key, value in ai_result.items():
        if key in cleaned:
            continue
        cleaned_value = _clean_value(value)
        if cleaned_value not in (None, "", []):
            cleaned[key] = cleaned_value

    return cleaned


def merge_fields(base: Dict[str, Any], enrichment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge enrichment values into the base dictionary without overwriting
    non-empty base values. Categories get merged uniquely.
    """
    if not enrichment:
        return base

    for key, value in enrichment.items():
        if value in (None, "", []):
            continue
        if key == "categories":
            base_list = base.get("categories") or []
            if not isinstance(base_list, list):
                base_list = [base_list] if base_list else []
            incoming = value if isinstance(value, list) else [value]
            for item in incoming:
                if item and item not in base_list:
                    base_list.append(item)
            base["categories"] = base_list
            continue
        if base.get(key) in (None, "", []):
            base[key] = value
    return base


__all__ = ["enrich_fields_with_mistral", "merge_fields"]
