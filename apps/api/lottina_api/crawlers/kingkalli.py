from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload, _logger


class KingKalliCrawler(BaseCrawler):
    source_slug = "kingkalli"
    source_name = "KingKalli"

    listing_url = "https://kingkalli.de/events/liste/"
    max_pages = 6

    def fetch(self) -> Iterable[EventPayload]:
        seen: set[str] = set()
        page_url = self.listing_url
        page_count = 0

        while page_url and page_count < self.max_pages:
            page_count += 1
            soup = self._get_soup(page_url)
            events = soup.select(".tribe-events-calendar-list__event")
            if not events:
                break

            for node in events:
                link = node.select_one(".tribe-events-calendar-list__event-title-link")
                if not link or not link.get("href"):
                    continue
                detail_url = link["href"]
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                try:
                    payload = self._parse_event(detail_url)
                except Exception as exc:  # noqa: BLE001
                    _logger().warning("kingkalli: failed to parse %s (%s)", detail_url, exc)
                    continue
                if payload:
                    yield payload

            page_url = self._next_page_url(soup)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _next_page_url(self, soup: BeautifulSoup) -> Optional[str]:
        link = soup.select_one(".tribe-events-c-nav__list-item--next a")
        if not link:
            return None
        href = link.get("href")
        if not href or "eventDisplay=past" in href:
            return None
        return href

    def _parse_event(self, url: str) -> Optional[EventPayload]:
        soup = self._get_soup(url)
        ld_event = self._extract_event_json(soup)

        dt_start = self._parse_iso(ld_event.get("startDate") if ld_event else None) or self._parse_date_from_url(url)
        dt_end = self._parse_iso(ld_event.get("endDate") if ld_event else None)

        location_name = None
        location_address = None
        location_city = None
        if ld_event and isinstance(ld_event.get("location"), dict):
            loc = ld_event["location"]
            location_name = loc.get("name")
            address = loc.get("address") or {}
            if isinstance(address, dict):
                street = address.get("streetAddress")
                postal = address.get("postalCode")
                city = address.get("addressLocality") or address.get("addressRegion")
                parts = [part for part in (street, postal) if part]
                location_address = ", ".join(parts) if parts else None
                location_city = city

        description_html = soup.select_one(".tribe-events-single-event-description")
        description = description_html.get_text("\n", strip=True) if description_html else None
        if not description and ld_event:
            description = ld_event.get("description")

        categories = [
            link.get_text(strip=True)
            for link in soup.select(".event-categories a")
            if link.get_text(strip=True)
        ]

        sched_start, sched_end = self._extract_schedule_times(soup, dt_start)
        if sched_start:
            dt_start = sched_start
        if sched_end and not dt_end:
            dt_end = sched_end

        image_url = self._extract_image(ld_event)
        if not image_url:
            hero_img = soup.select_one(".tribe-events-event-image img")
            if hero_img and hero_img.get("src"):
                image_url = hero_img["src"]

        payload = EventPayload(
            external_id=self._event_id_from_url(url),
            title=self._text_or("Unbenanntes Event", soup.select_one(".tribe-events-single-event-title")),
            description=description or "Details folgen in Kürze.",
            summary=(description or "")[:400] or None,
            source_url=url,
            image_url=image_url,
            dt_start=dt_start,
            dt_end=dt_end,
            location_name=location_name,
            location_address=location_address,
            location_city=location_city,
            categories=categories,
        )
        return payload

    def _extract_event_json(self, soup: BeautifulSoup) -> Optional[dict]:
        scripts = soup.find_all("script", {"type": "application/ld+json"})
        for script in scripts:
            try:
                data = json.loads(script.string or "")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                if data.get("@type") == "Event":
                    return data
                graph = data.get("@graph")
                if isinstance(graph, list):
                    for node in graph:
                        if isinstance(node, dict) and node.get("@type") == "Event":
                            return node
        return None

    def _parse_iso(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
            except Exception:  # noqa: BLE001
                return None

    def _parse_date_from_url(self, url: str) -> Optional[datetime]:
        match = re.search(r"/(20\d{2}-\d{2}-\d{2})/?$", url)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            return None

    def _event_id_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.path.rstrip("/") or url

    def _text_or(self, default: str, node) -> str:
        if not node:
            return default
        text = node.get_text(" ", strip=True)
        return text or default

    def _extract_image(self, ld_event: Optional[dict]) -> Optional[str]:
        if not ld_event:
            return None
        image = ld_event.get("image")
        if isinstance(image, str):
            return image
        if isinstance(image, dict):
            return image.get("url") or image.get("@id")
        if isinstance(image, list) and image:
            first = image[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url") or first.get("@id")
        return None

    def _extract_schedule_times(self, soup: BeautifulSoup, base_date: Optional[datetime]):
        schedule = soup.select_one(".tribe-events-schedule")
        if not schedule:
            return None, None
        text = schedule.get_text(" ", strip=True)
        date_text, times = self._split_schedule(text)
        date_value = self._parse_flexible_date(date_text, base_date)
        if not date_value:
            return None, None
        if not times:
            return date_value, None
        start_dt = date_value.replace(hour=times[0][0], minute=times[0][1])
        end_dt = None
        if len(times) > 1:
            end_dt = date_value.replace(hour=times[1][0], minute=times[1][1])
        return start_dt, end_dt

    def _split_schedule(self, text: str):
        parts = text.split("|", 1)
        date_part = parts[0].strip()
        times = re.findall(r"(\d{1,2})[:.](\d{2})", text)
        parsed = [(int(h), int(m)) for h, m in times]
        return date_part, parsed

    def _parse_flexible_date(self, text: str, fallback: Optional[datetime]):
        if not text:
            return fallback
        match = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)(?:\s+(\d{4}))?", text)
        if match:
            day = int(match.group(1))
            month = self._month_from_name(match.group(2))
            year = int(match.group(3)) if match.group(3) else (fallback.year if fallback else datetime.utcnow().year)
            if month:
                try:
                    return datetime(year, month, day)
                except ValueError:
                    return fallback
        return fallback

    def _month_from_name(self, value: str) -> Optional[int]:
        normalized = (
            value.lower()
            .replace("ä", "ae")
            .replace("ö", "oe")
            .replace("ü", "ue")
            .strip()
        )
        mapping = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "mae": 3,
            "apr": 4,
            "mai": 5,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "okt": 10,
            "oct": 10,
            "nov": 11,
            "dez": 12,
            "dec": 12,
        }
        return mapping.get(normalized[:3])
