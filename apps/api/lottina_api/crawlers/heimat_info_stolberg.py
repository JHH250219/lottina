from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload, _logger


class HeimatInfoStolbergCrawler(BaseCrawler):
    """Crawler that ingests published events from heimat-info.de for Stolberg."""

    source_slug = "heimat-info-stolberg"
    source_name = "Heimat-Info Stolberg"

    commune_id = "8aa5380a-2b07-421b-9433-bb987af96ab8"
    api_url = f"https://heimatinfo-api-platform.azurewebsites.net/communes/{commune_id}/events"
    detail_url_template = "https://www.heimat-info.de/gemeinden/stolberg?eventId={event_id}"

    CATEGORY_IDS: Dict[str, str] = {
        "62040ef0-f629-4463-acdb-41887aeeaac6": "Schule/Kita",
        "e01472d2-40be-497a-8603-182962267283": "Jugend",
        "7b9625ee-69c5-4dfd-895c-b74560dcbb38": "Öffentliches",
    }

    def fetch(self) -> Iterable[EventPayload]:
        aggregated: Dict[str, Dict[str, object]] = {}

        for category_id, category_name in self.CATEGORY_IDS.items():
            for item in self._iterate_category(category_id):
                external_id = (item.get("id") or "").strip()
                if not external_id:
                    continue
                entry = aggregated.get(external_id)
                if entry is None:
                    entry = {"data": item, "categories": set()}
                    aggregated[external_id] = entry
                categories: Set[str] = entry["categories"]
                categories.add(category_name)

        for external_id, entry in sorted(
            aggregated.items(), key=lambda pair: self._sort_key(pair[1]["data"])
        ):
            payload = self._build_payload(entry["data"], sorted(entry["categories"]))
            if payload:
                yield payload

    # ------------------------------------------------------------------
    def _iterate_category(self, category_id: str) -> Iterable[dict]:
        page = 1
        while True:
            items = self._fetch_page(category_id, page)
            if not items:
                break
            for item in items:
                yield item
            page += 1

    def _fetch_page(self, category_id: str, page: int) -> List[dict]:
        params = {
            "tab": "All",
            "categoryid": category_id,
            "page": page,
        }
        resp = self.session.get(self.api_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        _logger().warning(
            "heimat-info-stolberg: unexpected payload for %s page %s", category_id, page
        )
        return []

    def _build_payload(self, data: dict, categories: List[str]) -> Optional[EventPayload]:
        external_id = (data.get("id") or "").strip()
        if not external_id:
            return None

        title = (data.get("title") or "Event in Stolberg").strip()
        description_html = data.get("content") or ""
        description = self._clean_html(description_html)
        preview = (data.get("contentPreview") or "").strip()
        if not description and preview:
            description = preview
        summary = (preview or description or "")[:400] or None

        dt_start = self._parse_datetime(data.get("startDate"))
        dt_end = self._parse_datetime(data.get("endDate"))

        loc_name, loc_address, loc_city = self._split_location(data.get("location"))
        image_url = self._extract_image_url(data.get("attachments") or [])

        payload = EventPayload(
            external_id=external_id,
            title=title,
            description=description or "Weitere Informationen folgen in Kürze.",
            summary=summary,
            source_url=self.detail_url_template.format(event_id=external_id),
            image_url=image_url,
            dt_start=dt_start,
            dt_end=dt_end,
            location_name=loc_name,
            location_address=loc_address,
            location_city=loc_city,
            categories=categories,
        )
        return payload

    def _clean_html(self, value: str | None) -> Optional[str]:
        if not value:
            return None
        soup = BeautifulSoup(value, "lxml")
        text = soup.get_text("\n", strip=True)
        return text or None

    def _extract_image_url(self, attachments: List[dict]) -> Optional[str]:
        for attachment in attachments:
            if (attachment.get("type") or "").lower() == "picture":
                url = attachment.get("url")
                if url:
                    return url
        for attachment in attachments:
            url = attachment.get("url")
            if url:
                return url
        return None

    def _split_location(self, raw: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if not raw:
            return None, None, None
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if not parts:
            return raw.strip(), None, None
        name = parts[0]
        address_parts = parts[1:]
        city = None
        if address_parts:
            last = address_parts[-1]
            if re.match(r"\d{4,5}\s+.+", last):
                city = last
                address_parts = address_parts[:-1]
        address = ", ".join(address_parts) if address_parts else None
        return name, address or None, city

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        if dt.tzinfo:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    def _sort_key(self, data: dict) -> datetime:
        dt = self._parse_datetime(data.get("startDate"))
        return dt or datetime.max
