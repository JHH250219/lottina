from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload


class AachenFamilyCrawler(BaseCrawler):
    source_slug = "aachen-family"
    source_name = "aachen tourist service"

    listing_url = "https://www.aachen-tourismus.de/aachen-entdecken/fuer-familien/"

    def fetch(self) -> Iterable[EventPayload]:
        page = 1
        seen = set()
        while True:
            paged_url = f"{self.listing_url}?page={page}"
            soup = self._get_soup(paged_url)
            items = soup.select("#tab-familienevents .destination1-slider__item")
            if not items:
                break
            new_items = 0
            for item in items:
                link = item.select_one("a")
                if not link or not link.get("href"):
                    continue
                detail_url = urljoin(self.listing_url, link["href"])
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                new_items += 1
                teaser_img = item.select_one(".destination1-slider__item-image--img")
                payload = self._parse_detail_page(detail_url)
                if teaser_img and not payload.image_url:
                    payload.image_url = teaser_img.get("src")
                yield payload
            if new_items == 0:
                break
            page += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _make_external_id(self, url: str) -> str:
        parsed = urlparse(url)
        slug = parsed.path.rstrip("/").split("/")[-1]
        return slug or parsed.path

    def _parse_detail_page(self, url: str) -> EventPayload:
        soup = self._get_soup(url)
        title = self._text_or_none(soup.select_one(".poi-detail__header--headline")) or "Unbenanntes Event"
        description = self._extract_description(soup)
        summary = description[:400] if description else None
        image = self._src_or_none(soup.select_one(".poi-detail__image--img"))
        categories = self._extract_categories(soup)
        location_name, location_address, location_city = self._extract_location(soup)
        dt_start, dt_end = self._extract_dates(soup)
        price_text, is_free = self._extract_price_info(soup)

        payload = EventPayload(
            external_id=self._make_external_id(url),
            title=title,
            description=description,
            summary=summary,
            source_url=url,
            image_url=image,
            dt_start=dt_start,
            dt_end=dt_end,
            location_name=location_name,
            location_address=location_address,
            location_city=location_city,
            categories=categories,
            price_text=price_text,
            is_free=is_free,
        )
        return payload

    def _extract_description(self, soup: BeautifulSoup) -> str:
        parts: List[str] = []
        for block in soup.select(".poi-detail__content--text p"):
            text = block.get_text(" ", strip=True)
            if text:
                parts.append(text)
        if not parts:
            hero = soup.select_one(".poi-detail__content--text")
            if hero:
                parts.append(hero.get_text(" ", strip=True))
        return "\n\n".join(parts)

    def _extract_categories(self, soup: BeautifulSoup) -> List[str]:
        text = self._text_or_none(soup.select_one(".poi-detail__meta-container--categories"))
        if not text:
            return []
        return [part.strip() for part in text.split(",") if part.strip()]

    def _extract_location(self, soup: BeautifulSoup):
        name = self._text_or_none(soup.select_one(".poi-detail__contact--address-name"))
        address_html = soup.select_one(".poi-detail__contact--address-info")
        address = None
        city = None
        if address_html:
            lines = [line.strip() for line in address_html.decode_contents().split("<br>")]
            lines = [BeautifulSoup(line, "lxml").get_text(strip=True) for line in lines if line]
            if lines:
                address = lines[0]
            if len(lines) > 1:
                postal_city = lines[1]
                city = postal_city.split(" ", 1)[1] if " " in postal_city else postal_city
        meta_city = self._text_or_none(soup.select_one(".poi-detail__meta-container--location"))
        if meta_city:
            city = meta_city
        return name, address, city

    def _extract_dates(self, soup: BeautifulSoup):
        item = soup.select_one(".event-detail__dates-slider__item")
        if not item:
            return None, None
        date_el = item.select_one(".event-detail__dates-slider__item--date")
        time_el = item.select_one(".event-detail__dates-slider__item--time")
        if not date_el:
            return None, None
        year = int(date_el.get("data-year") or 0)
        month = int(date_el.get("data-month") or 0)
        day = int(date_el.get("data-day") or 0)
        if not all((year, month, day)):
            return None, None
        start_time, end_time = self._parse_time_range(time_el.get_text(" ", strip=True) if time_el else "")
        start = datetime(year, month, day, start_time[0], start_time[1]) if start_time else datetime(year, month, day)
        end = datetime(year, month, day, end_time[0], end_time[1]) if end_time else None
        return start, end

    def _parse_time_range(self, text: str):
        if not text:
            return None, None
        matches = re.findall(r"(\d{1,2}):(\d{2})", text)
        if not matches:
            return None, None
        start = tuple(int(v) for v in matches[0])
        end = tuple(int(v) for v in matches[1]) if len(matches) > 1 else None
        return start, end

    def _extract_price_info(self, soup: BeautifulSoup):
        accordion = soup.select_one(".poi-detail__general-information__accordion-tab__content")
        if not accordion:
            return None, None
        text = accordion.get_text(" ", strip=True)
        is_free = "frei" in text.lower()
        return text[:400], is_free

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None

    def _src_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        return node.get("src")
