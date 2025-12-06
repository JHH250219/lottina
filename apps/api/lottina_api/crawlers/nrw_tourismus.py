from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload


class NrwTourismusCrawler(BaseCrawler):
    """Crawler für die Weihnachtsmarkt-Übersicht auf nrw-tourismus.de."""

    source_slug = "nrw-tourismus"
    source_name = "NRW Tourismus"
    listing_url = "https://www.nrw-tourismus.de/weihnachtsmaerkte/uebersicht"

    def fetch(self) -> Iterable[EventPayload]:
        page = 1
        seen: set[str] = set()
        total_items = None

        while True:
            page_url = self._build_page_url(page)
            soup = self._get_soup(page_url)
            container = self._get_listing_container(soup)
            if not container:
                break
            if total_items is None:
                total_attr = container.get("data-items")
                total_items = int(total_attr) if total_attr and total_attr.isdigit() else None

            listing = container.select_one("ul.list__list")
            if not listing:
                break
            items = listing.select("li.list__list__item")
            if not items:
                break

            new_payloads = 0
            for item in items:
                link = item.select_one(".listItem__text__link[href]")
                if not link:
                    continue
                detail_url = urljoin(self.listing_url, link["href"])
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                new_payloads += 1

                payload = self._parse_detail(detail_url)

                summary = self._text_or_none(item.select_one(".listItem__text"))
                if summary and not payload.summary:
                    payload.summary = summary[:400]

                teaser_img = self._src_or_none(item.select_one(".listItem__image img"))
                if teaser_img and not payload.image_url:
                    payload.image_url = teaser_img

                date_text = self._text_or_none(item.select_one(".listItem__date"))
                if date_text and not payload.dt_start:
                    start, end = self._parse_listing_dates(date_text)
                    payload.dt_start = start
                    payload.dt_end = payload.dt_end or end

                poi_text = self._text_or_none(item.select_one(".listItem__poi"))
                name, address, city = self._parse_poi_text(poi_text)
                if name and not payload.location_name:
                    payload.location_name = name
                if address and not payload.location_address:
                    payload.location_address = address
                if city and not payload.location_city:
                    payload.location_city = city

                yield payload

            if total_items and len(seen) >= total_items:
                break
            if new_payloads == 0:
                break
            page += 1

    # ------------------------------------------------------------------
    def _build_page_url(self, page: int) -> str:
        if page <= 1:
            return self.listing_url
        return f"{self.listing_url}?page={page}&uid=3927#c3927"

    def _get_listing_container(self, soup: BeautifulSoup):
        containers = soup.select("[data-browsable-list][data-items]")
        for container in containers:
            if container.select_one(".listItem__title"):
                return container
        return containers[0] if containers else None

    def _parse_detail(self, url: str) -> EventPayload:
        soup = self._get_soup(url)
        data = self._extract_event_data(soup)

        description = self._clean_text(data.get("description")) if data else None
        if not description:
            body = soup.select_one(".baseArticle") or soup.select_one(".detail__content")
            description = self._text_or_none(body)
        title = (data.get("name") if data else None) or self._text_or_none(soup.select_one("h1")) or "Weihnachtsmarkt"
        image = self._first(data.get("image")) if data else None
        start_dt = self._parse_iso_datetime(data.get("startDate")) if data else None
        end_dt = self._parse_iso_datetime(data.get("endDate")) if data else None
        location_name, location_address, location_city = self._extract_location(data)

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
            categories=["Weihnachtsmarkt"],
        )
        return payload

    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _extract_event_data(self, soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            text = script.string or ""
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Event":
                return data
            if isinstance(data, dict) and data.get("@type") == "WebPage":
                main = data.get("mainEntity")
                if isinstance(main, list):
                    for entry in main:
                        if isinstance(entry, dict) and entry.get("@type") == "Event":
                            return entry
        return None

    def _extract_location(self, data: Optional[dict]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if not data:
            return None, None, None
        location = data.get("location") or {}
        name = location.get("name") if isinstance(location, dict) else None
        address = None
        city = None
        addr = location.get("address") if isinstance(location, dict) else None
        if isinstance(addr, dict):
            street = addr.get("streetAddress")
            postal = addr.get("postalCode")
            city = addr.get("addressLocality")
            address_parts = [part for part in (street, postal, city) if part]
            if address_parts:
                address = " ".join(address_parts)
        return name, address, city

    def _parse_listing_dates(self, text: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        parts = [t.strip() for t in (text or "").replace("Datum :", "").split("-")]
        if not parts:
            return None, None
        start = self._parse_german_date(parts[0])
        end = self._parse_german_date(parts[1]) if len(parts) > 1 else None
        return start, end

    def _parse_german_date(self, text: str) -> Optional[datetime]:
        months = {
            "jan": 1,
            "feb": 2,
            "mae": 3,
            "apr": 4,
            "mai": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "okt": 10,
            "nov": 11,
            "dez": 12,
        }
        clean = (text or "").strip().lower().replace(".", "")
        clean = clean.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        if not clean:
            return None
        parts = clean.split()
        if len(parts) < 3:
            return None
        day = int(parts[0])
        month_key = parts[1][:3]
        month = months.get(month_key)
        year = int(parts[2])
        if not month:
            return None
        return datetime(year, month, day)

    def _parse_poi_text(self, text: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if not text:
            return None, None, None
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            return None, None, None
        if len(parts) == 1:
            name = parts[0]
            address = parts[0]
            city = None
        else:
            name = parts[0]
            address = ", ".join(parts[:-1])
            city = parts[-1]
        return name, address, city

    def _make_external_id(self, url: str) -> str:
        parsed = urlparse(url)
        slug = parsed.path.rstrip("/").split("/")[-1]
        return slug or parsed.path

    def _parse_iso_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _clean_text(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return BeautifulSoup(value, "lxml").get_text(" ", strip=True)

    def _first(self, value):
        if isinstance(value, list):
            value = value[0]
        if isinstance(value, dict):
            return value.get("url") or value.get("contentUrl") or value.get("thumbnail")
        return value

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None

    def _src_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        src = node.get("src") or node.get("data-src")
        return src or None
