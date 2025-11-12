from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload


class RurEifelCrawler(BaseCrawler):
    """Crawler für den Veranstaltungskalender von rureifel-tourismus.de."""

    source_slug = "rur-eifel"
    source_name = "Rureifel Tourismus"
    listing_url = "https://www.rureifel-tourismus.de/veranstaltungskalender"

    def fetch(self) -> Iterable[EventPayload]:
        page = 1
        seen = set()
        while True:
            soup = self._get_soup(f"{self.listing_url}?tx_solr%5Bpage%5D={page}")
            cards = soup.select(".cardTeaser")
            if not cards:
                break
            new_items = 0
            for card in cards:
                link = card.select_one(".listItem__txtSection__link a")
                if not link or not link.get("href"):
                    continue
                detail_url = urljoin(self.listing_url, link["href"])
                if detail_url in seen:
                    continue
                seen.add(detail_url)
                new_items += 1

                summary = self._text_or_none(card.select_one(".listItem__txtSection__paragraph"))
                teaser_image = self._extract_teaser_image(card)
                payload = self._parse_detail(detail_url)
                if summary and not payload.summary:
                    payload.summary = summary[:400]
                if teaser_image and not payload.image_url:
                    payload.image_url = teaser_image

                date_span = self._text_or_none(card.select_one(".listItem__imgSection__date"))
                if date_span and not payload.dt_start:
                    start, end = self._parse_list_dates(date_span)
                    payload.dt_start = payload.dt_start or start
                    payload.dt_end = payload.dt_end or end

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
        title = self._text_or_none(soup.select_one("h1")) or "Event"
        description = self._extract_description(soup)
        image = self._hero_image(soup)
        dt_start, dt_end, dt_time = self._extract_header_dates(soup)
        location_name, location_address = self._extract_location(soup)

        payload = EventPayload(
            external_id=url.rstrip("/").split("/")[-1],
            title=title,
            description=description or "",
            summary=(description or "")[:400],
            source_url=url,
            image_url=image,
            dt_start=dt_start,
            dt_end=dt_end,
            location_name=location_name,
            location_address=location_address,
            categories=["Veranstaltungskalender"],
        )
        if payload.dt_start and dt_time:
            payload.dt_start = payload.dt_start.replace(hour=dt_time[0], minute=dt_time[1])
        return payload

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        paragraphs = []
        for section in soup.select(".baseArticle__bodycopy p"):
            text = section.get_text(" ", strip=True)
            if text:
                paragraphs.append(text)
        return "\n\n".join(paragraphs)

    def _extract_header_dates(self, soup: BeautifulSoup):
        date_text = self._text_or_none(soup.select_one(".eventHeader__date--data .text"))
        time_text = self._text_or_none(soup.select_one(".eventHeader__time .data"))
        dt = self._parse_date(date_text)
        tm = self._parse_time(time_text)
        return dt, None, tm

    def _extract_location(self, soup: BeautifulSoup):
        block = soup.select_one(".section--contact address .address__content")
        if not block:
            return None, None
        raw = block.decode_contents()
        parts = [seg.strip() for seg in raw.split("<br") if seg.strip()]
        cleaned = []
        for part in parts:
            cleaned.append(BeautifulSoup(part, "lxml").get_text(" ", strip=True))
        if not cleaned:
            return None, None
        name = cleaned[0]
        address = " ".join(cleaned[1:]) if len(cleaned) > 1 else None
        return name, address

    def _hero_image(self, soup: BeautifulSoup) -> Optional[str]:
        img = soup.select_one(".hero--medium img")
        if img and img.get("src"):
            return img["src"]
        return None

    def _extract_teaser_image(self, card) -> Optional[str]:
        img = card.select_one(".listItem__imgSection picture img")
        if img and img.get("src"):
            return img["src"]
        return None

    def _parse_list_dates(self, text: str):
        parts = [t.strip() for t in text.split("-")]
        start = self._parse_date(parts[0])
        end = self._parse_date(parts[1]) if len(parts) > 1 else None
        return start, end

    def _parse_date(self, text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None
        for fmt in ("%d.%m.%y", "%d.%m.%Y"):
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
        return None

    def _parse_time(self, text: Optional[str]):
        if not text:
            return None
        text = text.replace("Uhr", "").strip()
        try:
            hour, minute = text.split(":")
            return int(hour), int(minute)
        except ValueError:
            return None

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None
