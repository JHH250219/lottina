from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload


class AachenerKinderCrawler(BaseCrawler):
    source_slug = "aachenerkinder"
    source_name = "aachenerkinder.de"
    listing_url = "https://aachenerkinder.de/veranstaltungen/liste/"

    def fetch(self) -> Iterable[EventPayload]:
        page = 1
        seen: set[str] = set()

        while True:
            soup = self._get_listing_page(page)
            cards = soup.select(".tribe-events-calendar-list__event")
            if not cards:
                break

            new_items = 0
            for card in cards:
                link = card.select_one(".tribe-events-calendar-list__event-title-link[href]")
                if not link:
                    continue
                detail_url = urljoin(self.listing_url, link["href"])
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                new_items += 1

                payload = self._parse_detail(detail_url)

                summary = self._text_or_none(card.select_one(".tribe-events-calendar-list__event-description"))
                if summary and not payload.summary:
                    payload.summary = summary[:400]

                fallback_image = self._src_or_none(card.select_one(".tribe-events-calendar-list__event-featured-image"))
                if fallback_image and not payload.image_url:
                    payload.image_url = fallback_image

                start, end = self._parse_listing_date_range(card)
                if start and not payload.dt_start:
                    payload.dt_start = start
                if end and not payload.dt_end:
                    payload.dt_end = end

                venue_text = self._text_or_none(card.select_one(".tribe-events-calendar-list__event-venue"))
                name, address, city = self._parse_listing_venue(venue_text)
                if name and not payload.location_name:
                    payload.location_name = name
                if address and not payload.location_address:
                    payload.location_address = address
                if city and not payload.location_city:
                    payload.location_city = city

                yield payload

            if new_items == 0:
                break
            page += 1

    # ------------------------------------------------------------------
    def _get_listing_page(self, page: int) -> BeautifulSoup:
        url = self.listing_url.rstrip("/")
        if page > 1:
            url = f"{url}/seite/{page}/"
        return self._get_soup(url)

    def _parse_detail(self, url: str) -> EventPayload:
        soup = self._get_soup(url)
        data = self._extract_event_json(soup)

        description = self._extract_description(soup)
        if not description and data:
            description = self._clean_text(data.get("description"))

        title = (data.get("name") if data else None) or self._text_or_none(soup.select_one("h1")) or "Event"
        image = self._first_from(data.get("image")) if data else None
        start_dt = self._parse_iso_datetime(data.get("startDate")) if data else None
        end_dt = self._parse_iso_datetime(data.get("endDate")) if data else None
        schedule_text = self._text_or_none(soup.select_one(".tribe-events-schedule"))
        if not start_dt or not end_dt:
            sched_start, sched_end = self._parse_schedule_text(schedule_text)
            start_dt = start_dt or sched_start
            end_dt = end_dt or sched_end

        location_name, location_address, location_city = self._extract_location_data(soup, data)
        categories = self._extract_categories(soup)

        payload = EventPayload(
            external_id=self._make_external_id(url),
            title=title,
            description=description or "",
            summary=(description or "")[:400],
            source_url=url,
            image_url=image,
            dt_start=start_dt,
            dt_end=end_dt,
            location_name=location_name,
            location_address=location_address,
            location_city=location_city,
            categories=categories,
        )
        return payload

    def _extract_event_json(self, soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            text = (script.string or "").strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Event":
                return data
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict) and entry.get("@type") == "Event":
                        return entry
        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        body = soup.select_one(".tribe-events-single-event-description")
        return self._text_or_none(body)

    def _extract_categories(self, soup: BeautifulSoup) -> list[str]:
        names = []
        for link in soup.select(".tribe-events-meta-group-categories a"):
            text = link.get_text(strip=True)
            if text:
                names.append(text)
        return names

    def _extract_location_data(self, soup: BeautifulSoup, data: Optional[dict]):
        name = address = city = None
        if data:
            loc = data.get("location")
            if isinstance(loc, dict):
                name = loc.get("name")
                addr = loc.get("address")
                if isinstance(addr, dict):
                    street = addr.get("streetAddress")
                    postal = addr.get("postalCode")
                    city = addr.get("addressLocality")
                    parts = [part for part in (street, postal, city) if part]
                    if parts:
                        address = " ".join(parts)

        venue_block = soup.select_one(".tribe-events-meta-group-venue")
        if venue_block:
            block_name = self._text_or_none(venue_block.select_one(".tribe-venue"))
            street = self._text_or_none(venue_block.select_one(".tribe-street-address"))
            postal = self._text_or_none(venue_block.select_one(".tribe-postal-code"))
            locality = self._text_or_none(venue_block.select_one(".tribe-locality"))
            if block_name:
                name = name or block_name
            block_address_parts = [part for part in (street, postal, locality) if part]
            if block_address_parts:
                address = address or " ".join(block_address_parts)
            if locality:
                city = city or locality

        return name, address, city

    def _parse_listing_date_range(self, card) -> Tuple[Optional[datetime], Optional[datetime]]:
        start_text = self._text_or_none(card.select_one(".tribe-event-date-start"))
        end_text = self._text_or_none(card.select_one(".tribe-event-date-end"))
        start = self._parse_german_date(start_text)
        end = self._parse_german_date(end_text)
        return start, end

    def _parse_listing_venue(self, text: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if not text:
            return None, None, None
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            return None, None, None
        name = parts[0]
        address = ", ".join(parts[:-1]) if len(parts) > 1 else parts[0]
        city = parts[-1] if len(parts) > 1 else None
        return name, address, city

    def _parse_schedule_text(self, text: Optional[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not text:
            return None, None
        segments = [segment.strip() for segment in text.split("-") if segment.strip()]
        if not segments:
            return None, None
        start = self._parse_german_date(segments[0])
        end = self._parse_german_date(segments[1]) if len(segments) > 1 else None
        return start, end

    def _parse_german_date(self, text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None
        try:
            return datetime.strptime(text.strip(), "%d.%m.%Y")
        except ValueError:
            return None

    def _parse_iso_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _make_external_id(self, url: str) -> str:
        parsed = urlparse(url)
        slug = parsed.path.rstrip("/").split("/")[-1]
        return slug or parsed.path

    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _first_from(self, value):
        if isinstance(value, list):
            return value[0]
        return value

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None

    def _src_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        return node.get("src") or node.get("data-src")

    def _clean_text(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return BeautifulSoup(value, "lxml").get_text(" ", strip=True)
