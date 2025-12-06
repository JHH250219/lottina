from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Tuple

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload, slugify


class RoetgenEventCrawler(BaseCrawler):
    """Crawler für den Veranstaltungskalender des Ortskartells Roetgen."""

    source_slug = "roetgen-event"
    source_name = "Ortskartell Roetgen"
    listing_url = "https://roetgen-event.de/veranstaltungen"

    def fetch(self) -> Iterable[EventPayload]:
        soup = self._get_soup(self.listing_url)
        blocks = soup.select(".shedule-block")
        for block in blocks:
            payload = self._parse_block(block)
            if payload:
                yield payload

    # ------------------------------------------------------------------
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _parse_block(self, block) -> Optional[EventPayload]:
        external_id = block.get("id") or self._make_fallback_id(block)
        title = self._text_or_none(block.select_one("h3")) or "Event in Roetgen"
        description = self._extract_description(block)
        summary = description[:400] if description else None
        image = self._extract_image(block)
        dt_start, dt_end = self._extract_dates(block.select_one(".date"))
        location_name, price_text = self._extract_meta(block)

        return EventPayload(
            external_id=external_id,
            title=title,
            description=description or "",
            summary=summary,
            source_url=f"{self.listing_url}#{external_id}" if external_id else self.listing_url,
            image_url=image,
            dt_start=dt_start,
            dt_end=dt_end,
            location_name=location_name,
            location_city="Roetgen",
            categories=["Roetgen"],
            price_text=price_text,
            is_free=self._is_free(price_text),
        )

    def _make_fallback_id(self, block) -> str:
        title = self._text_or_none(block.select_one("h3")) or ""
        date_box = block.select_one(".date")
        start_date, _ = self._extract_dates(date_box)
        date_part = start_date.strftime("%Y%m%d") if start_date else "nodate"
        return f"roetgen-{slugify(title)}-{date_part}"

    def _extract_description(self, block) -> Optional[str]:
        text_block = block.select_one(".text")
        if not text_block:
            return None
        text = text_block.get_text(" ", strip=True)
        return text or None

    def _extract_image(self, block) -> Optional[str]:
        img = block.select_one("figure img")
        if img and img.get("src"):
            return img["src"]
        return None

    def _extract_dates(self, date_box) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not date_box:
            return None, None
        date_values: list[datetime] = []
        time_values: list[Tuple[int, int]] = []
        for node in date_box.find_all("time"):
            raw = (node.get("datetime") or "").strip()
            text_value = node.get_text(strip=True)
            if ":" in raw or ":" in text_value:
                parsed_time = self._parse_time(text_value or raw)
                if parsed_time:
                    time_values.append(parsed_time)
            else:
                parsed_date = self._parse_date(text_value or raw)
                if parsed_date:
                    date_values.append(parsed_date)

        start_date = date_values[0] if date_values else None
        end_date = date_values[1] if len(date_values) > 1 else start_date
        start_dt = start_date
        end_dt = end_date

        if start_date and time_values:
            start_dt = start_date.replace(hour=time_values[0][0], minute=time_values[0][1])
        if end_date and len(time_values) > 1:
            end_dt = end_date.replace(hour=time_values[1][0], minute=time_values[1][1])
        return start_dt, end_dt

    def _extract_meta(self, block) -> Tuple[Optional[str], Optional[str]]:
        location_name = None
        price_text = None
        for item in block.select(".shedule-info li"):
            label_el = item.select_one("span")
            label = label_el.get_text(strip=True).strip(":").lower() if label_el else ""
            value = item.get_text(" ", strip=True)
            if label_el:
                prefix = label_el.get_text(" ", strip=True)
                value = value.replace(prefix, "", 1).strip()
            if label == "ort":
                if value:
                    if value.strip().lower() == "roetgen":
                        location_name = None
                    else:
                        location_name = value
            elif label in {"eintritt", "preis"}:
                price_text = value or price_text
        return location_name, price_text

    def _parse_date(self, text: str) -> Optional[datetime]:
        text = (text or "").strip()
        for fmt in ("%d.%m.%Y", "%d.%m.%y"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _parse_time(self, text: str) -> Optional[Tuple[int, int]]:
        text = (text or "").replace("Uhr", "").strip()
        try:
            hour, minute = text.split(":")
            return int(hour), int(minute)
        except ValueError:
            return None

    def _is_free(self, price_text: Optional[str]) -> Optional[bool]:
        if not price_text:
            return None
        lowered = price_text.lower()
        if "frei" in lowered or "kostenlos" in lowered:
            return True
        return None

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None
