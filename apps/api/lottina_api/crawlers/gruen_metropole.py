from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload


class GruenMetropoleCrawler(BaseCrawler):
    """Crawler für gruenmetropole.eu Veranstaltungen."""

    source_slug = "gruenmetropole"
    source_name = "Grünmetropole e.V."
    listing_url = "https://www.gruenmetropole.eu/veranstaltungen/index.php"

    def fetch(self) -> Iterable[EventPayload]:
        page = 1
        seen = set()
        while True:
            soup = self._get_soup(f"{self.listing_url}?page={page}")
            cards = soup.select(".event-entry-new-1")
            if not cards:
                break
            new_items = 0
            for card in cards:
                link = card.select_one("a.event-entry-new-1-image-link") or card.select_one(".event-entry-new-1-headline a")
                if not link or not link.get("href"):
                    continue
                detail_url = urljoin(self.listing_url, link["href"])
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                new_items += 1

                teaser = self._extract_teaser_image(card)
                payload = self._parse_detail(detail_url)
                if teaser and not payload.image_url:
                    payload.image_url = teaser
                if not payload.dt_start:
                    payload.dt_start, payload.dt_end = self._extract_dates_from_card(card)
                yield payload
            if new_items == 0:
                break
            page += 1

    # ------------------------------------------------------------------
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _parse_detail(self, url: str) -> EventPayload:
        soup = self._get_soup(url)
        data = self._extract_event_json(soup)

        title = data.get("name") if data else self._text_or_none(soup.select_one("h1")) or "Event"
        description = data.get("description") if data else self._extract_description(soup)
        image = self._first_from(data.get("image")) if data else None
        start_dt = self._parse_iso_date(data.get("startDate")) if data else None
        end_dt = self._parse_iso_date(data.get("endDate")) if data else None
        location_name, location_address = self._extract_location(soup, data)

        payload = EventPayload(
            external_id=self._extract_external_id(url),
            title=title.strip(),
            description=description or "",
            summary=(description or "")[:400],
            source_url=url,
            image_url=image,
            dt_start=start_dt,
            dt_end=end_dt,
            location_name=location_name,
            location_address=location_address,
            location_city=self._extract_location_city(data),
        )
        return payload

    def _extract_event_json(self, soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "null")
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type", "").lower() == "event":
                return data
        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        texts = [p.get_text(" ", strip=True) for p in soup.select(".tiny_p") if p.get_text(strip=True)]
        return "\n\n".join(texts) if texts else None

    def _extract_location(self, soup: BeautifulSoup, data: Optional[dict]) -> Tuple[Optional[str], Optional[str]]:
        name = None
        address = None
        if data and data.get("location"):
            name = data["location"].get("name")
        heading = soup.find("h3", string=lambda s: s and "Veranstaltungsort" in s)
        if heading:
            h5 = heading.find_next("h5")
            if h5 and h5.string:
                name = name or h5.get_text(strip=True)
            tiny = heading.find_next("p", class_="tiny_p")
            if tiny:
                address = tiny.get_text(" ", strip=True)
        return name, address

    def _extract_location_city(self, data: Optional[dict]) -> Optional[str]:
        if not data:
            return None
        location = data.get("location") or {}
        addr = location.get("address")
        if isinstance(addr, dict):
            return addr.get("addressLocality")
        return location.get("name")

    def _extract_teaser_image(self, card) -> Optional[str]:
        image_div = card.select_one(".event-entry-new-1-image")
        if not image_div:
            return None
        style = image_div.get("style", "")
        match = re.search(r"url\(([^)]+)\)", style)
        return match.group(1) if match else None

    def _extract_external_id(self, url: str) -> str:
        return url.rstrip("/").split("/")[-1]

    def _extract_dates_from_card(self, card) -> Tuple[Optional[datetime], Optional[datetime]]:
        times = card.select(".event-entry-new-1-time time")
        if not times:
            return (None, None)
        dates = [self._parse_iso_date(node.get("datetime")) for node in times if node.get("datetime")]
        if len(dates) == 1:
            return dates[0], None
        if len(dates) >= 2:
            return dates[0], dates[-1]
        return (None, None)

    def _parse_iso_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None

    def _first_from(self, value):
        if isinstance(value, list):
            return value[0]
        return value
