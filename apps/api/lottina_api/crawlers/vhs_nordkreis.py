from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseCrawler, EventPayload


class VhsNordkreisCrawler(BaseCrawler):
    """Crawler für das Kursangebot der VHS Nordkreis Aachen (Kategorie 52)."""

    source_slug = "vhs-nordkreis"
    source_name = "VHS Nordkreis Aachen"
    base_url = "https://www.vhs-nordkreis-aachen.de"
    start_path = "/kategorie/52?blkeep=1"
    default_category = "VHS Nordkreis Eltern-Kind"

    def fetch(self) -> Iterable[EventPayload]:
        next_url: Optional[str] = urljoin(self.base_url, self.start_path)
        visited: set[str] = set()
        while next_url and next_url not in visited:
            visited.add(next_url)
            soup = self._get_soup(next_url)
            panels = soup.select(".hauptseite_kurse .panel.kw_kurs_uebersicht")
            for panel in panels:
                payload = self._parse_panel(panel)
                if payload:
                    yield payload
            next_url = self._find_next_url(soup)

    # ------------------------------------------------------------------
    def _parse_panel(self, panel) -> Optional[EventPayload]:
        link = panel.select_one("a.kursdetaillink")
        if not link or not link.get("href"):
            return None
        detail_url = urljoin(self.base_url, link["href"])
        external_id = detail_url.rstrip("/").split("/")[-1]
        listing_location, listing_start, listing_end = self._extract_listing_meta(panel)
        detail = self._parse_detail_page(detail_url, listing_location, listing_start, listing_end)
        if not detail:
            return None

        description = detail.get("description") or self._panel_description(panel)
        location_name = detail.get("location_name") or detail.get("location_fallback")
        location_address = detail.get("location_address")
        location_city = detail.get("location_city")

        return EventPayload(
            external_id=external_id,
            title=self._text_or_none(link) or "VHS Nordkreis Kurs",
            description=description or "",
            summary=(description or "")[:400] if description else None,
            source_url=detail_url,
            dt_start=detail.get("dt_start"),
            dt_end=detail.get("dt_end"),
            location_name=location_name,
            location_address=location_address,
            location_city=location_city,
            categories=[self.default_category],
            price_text=detail.get("price_text"),
            is_free=self._is_free(detail.get("price_text")),
        )

    def _parse_detail_page(
        self,
        url: str,
        fallback_location: Optional[str],
        fallback_start: Optional[datetime],
        fallback_end: Optional[datetime],
    ) -> Optional[dict]:
        try:
            soup = self._get_soup(url)
        except Exception:
            return None

        info_tab = soup.select_one("#kurs")
        description_parts: list[str] = []
        price_text: Optional[str] = None
        if info_tab:
            for paragraph in info_tab.select("p"):
                text = paragraph.get_text(" ", strip=True)
                if not text:
                    continue
                label_el = paragraph.find("strong")
                label = label_el.get_text(" ", strip=True).strip(":").lower() if label_el else ""
                if label.startswith("kursnummer"):
                    continue
                if label.startswith("kosten"):
                    price_text = text.split(":", 1)[1].strip() if ":" in text else text
                    continue
                description_parts.append(text)

        description = "\n\n".join(description_parts).strip() if description_parts else None

        location_name = None
        location_address = None
        location_city = None
        location_tab = soup.select_one("#kursort li.list-group-item")
        if location_tab:
            location_lines = list(location_tab.stripped_strings)
            if location_lines:
                location_name = location_lines[0]
            if len(location_lines) > 1:
                location_address = location_lines[1]
            if len(location_lines) > 2:
                location_city = location_lines[2]

        start_dt, end_dt = self._extract_schedule(soup)

        return {
            "description": description,
            "price_text": price_text,
            "location_name": location_name or fallback_location,
            "location_address": location_address,
            "location_city": location_city,
            "location_fallback": fallback_location,
            "dt_start": start_dt or fallback_start,
            "dt_end": end_dt or fallback_end,
        }

    def _extract_listing_meta(
        self, panel
    ) -> Tuple[Optional[str], Optional[datetime], Optional[datetime]]:
        location_text = None
        dt_start = None
        dt_end = None
        paragraphs = panel.select(".panel-body p")
        for idx, paragraph in enumerate(paragraphs):
            text = paragraph.get_text(" ", strip=True)
            if not text:
                continue
            if idx == 0:
                continue
            if re.search(r"\d{2}\.\d{2}\.\d{4}", text):
                start, end = self._parse_date_time_line(text)
                dt_start = dt_start or start
                dt_end = dt_end or end or start
            elif not location_text:
                location_text = text
        return location_text, dt_start, dt_end

    def _extract_schedule(self, soup: BeautifulSoup) -> Tuple[Optional[datetime], Optional[datetime]]:
        rows = []
        for tbody in soup.select("#termine table tbody"):
            tr = tbody.find("tr")
            if tr:
                rows.append(tr)
        if not rows:
            rows = soup.select("#termine table tr")[1:]
        if not rows:
            return None, None
        first_start, first_end = self._parse_schedule_row(rows[0])
        last_start, last_end = self._parse_schedule_row(rows[-1])
        start_dt = first_start
        end_dt = last_end or last_start or first_end or first_start
        return start_dt, end_dt

    def _parse_schedule_row(self, row) -> Tuple[Optional[datetime], Optional[datetime]]:
        cells = row.find_all("td")
        if not cells:
            return None, None
        date_text = cells[0].get_text(" ", strip=True)
        time_text = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
        date_value = self._parse_date(date_text)
        if not date_value:
            return None, None
        start_time, end_time = self._parse_time_range(time_text)
        start_dt = date_value
        end_dt = date_value
        if start_time:
            start_dt = date_value.replace(hour=start_time[0], minute=start_time[1])
        if end_time:
            end_dt = date_value.replace(hour=end_time[0], minute=end_time[1])
        else:
            end_dt = start_dt
        return start_dt, end_dt

    def _parse_date_time_line(self, text: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        date_match = re.findall(r"(\d{2}\.\d{2}\.\d{4})", text)
        time_match = re.findall(r"(\d{1,2}:\d{2})", text)
        if not date_match:
            return None, None
        start_date = datetime.strptime(date_match[0], "%d.%m.%Y")
        start_dt = start_date
        end_dt = start_date
        if time_match:
            start_time = datetime.strptime(time_match[0], "%H:%M")
            start_dt = start_date.replace(hour=start_time.hour, minute=start_time.minute)
            end_time_value = time_match[1] if len(time_match) > 1 else time_match[0]
            end_time = datetime.strptime(end_time_value, "%H:%M")
            end_dt = start_date.replace(hour=end_time.hour, minute=end_time.minute)
        if len(date_match) > 1:
            end_date = datetime.strptime(date_match[-1], "%d.%m.%Y")
            end_dt = end_dt.replace(year=end_date.year, month=end_date.month, day=end_date.day)
        return start_dt, end_dt

    def _parse_date(self, value: str) -> Optional[datetime]:
        match = re.search(r"(\d{2}\.\d{2}\.\d{4})", value)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%d.%m.%Y")
        except ValueError:
            return None

    def _parse_time_range(self, value: str) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
        parts = re.findall(r"(\d{1,2}):(\d{2})", value)
        if not parts:
            return None, None
        start_time = (int(parts[0][0]), int(parts[0][1]))
        end_time = (int(parts[1][0]), int(parts[1][1])) if len(parts) > 1 else None
        return start_time, end_time

    def _panel_description(self, panel) -> Optional[str]:
        text_block = panel.select_one(".panel-body p")
        return self._text_or_none(text_block)

    def _text_or_none(self, node) -> Optional[str]:
        if not node:
            return None
        text = node.get_text(" ", strip=True)
        return text or None

    def _is_free(self, price_text: Optional[str]) -> Optional[bool]:
        if not price_text:
            return None
        normalized = price_text.lower()
        if "kostenlos" in normalized or "entgeltfrei" in normalized or "0,00" in normalized:
            return True
        return None

    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def _find_next_url(self, soup: BeautifulSoup) -> Optional[str]:
        link = soup.select_one(".hauptseite_kurse ul.pager.pull-right li a")
        if link and link.get("href"):
            return urljoin(self.base_url, link["href"])
        return None
