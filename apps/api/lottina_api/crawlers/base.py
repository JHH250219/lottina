from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional

import requests
from flask import current_app

from ..models import (
    Category,
    Location,
    Offer,
    OfferType,
    SourceType,
    db,
)


def slugify(value: str) -> str:
    """Simple slugify helper."""
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "kategorie"


def _logger():
    return current_app.logger if current_app else logging.getLogger(__name__)


@dataclass
class EventPayload:
    external_id: str
    title: str
    description: str
    source_url: str
    summary: Optional[str] = None
    image_url: Optional[str] = None
    dt_start: Optional[datetime] = None
    dt_end: Optional[datetime] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location_city: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    price_text: Optional[str] = None
    is_free: Optional[bool] = None
    is_outdoor: Optional[bool] = None


class BaseCrawler:
    """Shared persistence helpers for crawlers."""

    source_slug: str = "external"
    source_name: str = "external"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "lottina-crawler (+https://www.lottina.de)",
        )

    def fetch(self) -> Iterable[EventPayload]:
        """Subclasses must yield EventPayload items."""
        raise NotImplementedError

    def run(self) -> dict:
        created = 0
        updated = 0
        for payload in self.fetch():
            status = self._persist_event(payload)
            if status == "created":
                created += 1
            elif status == "updated":
                updated += 1
        db.session.commit()
        _logger().info("Crawler %s finished: %s created, %s updated", self.source_slug, created, updated)
        return {"created": created, "updated": updated}

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _persist_event(self, payload: EventPayload) -> str:
        external_id = f"{self.source_slug}:{payload.external_id}"
        offer = Offer.query.filter_by(external_id=external_id).one_or_none()

        created = False
        if offer is None:
            offer = Offer(
                external_id=external_id,
                source=self.source_slug,
                source_name=self.source_name,
                source_type=SourceType.crawler,
                source_url=payload.source_url,
                title=payload.title,
                type=OfferType.event,
            )
            db.session.add(offer)
            created = True

        offer.title = payload.title
        offer.description = payload.description
        offer.summary = (payload.summary or (payload.description or "")[:380])[:400]
        offer.source_url = payload.source_url
        offer.image = payload.image_url or offer.image
        offer.dt_start = payload.dt_start
        offer.dt_end = payload.dt_end
        offer.is_free = bool(payload.is_free) if payload.is_free is not None else offer.is_free
        offer.is_outdoor = payload.is_outdoor if payload.is_outdoor is not None else offer.is_outdoor
        if payload.price_text and not offer.summary:
            offer.summary = payload.price_text[:400]

        location = self._upsert_location(payload)
        if location:
            offer.location = location

        if payload.categories:
            offer.categories = [self._get_or_create_category(name) for name in payload.categories if name]

        db.session.flush()
        return "created" if created else "updated"

    def _upsert_location(self, payload: EventPayload) -> Optional[Location]:
        if not (payload.location_name or payload.location_address or payload.location_city):
            return None

        fingerprint_source = "|".join(
            (payload.location_name or "", payload.location_address or "", payload.location_city or "")
        ).lower()
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()

        location = Location.query.filter_by(fingerprint=fingerprint).one_or_none()
        if location is None:
            location = Location(
                fingerprint=fingerprint,
                name=payload.location_name,
                address=payload.location_address,
                city=payload.location_city,
            )
            db.session.add(location)
            db.session.flush()
            return location

        updated = False
        if payload.location_name and not location.name:
            location.name = payload.location_name
            updated = True
        if payload.location_address and not location.address:
            location.address = payload.location_address
            updated = True
        if payload.location_city and not location.city:
            location.city = payload.location_city
            updated = True
        if updated:
            db.session.flush()
        return location

    def _get_or_create_category(self, name: str) -> Category:
        slug = slugify(name)
        category = Category.query.filter_by(slug=slug).one_or_none()
        if category is None:
            category = Category(slug=slug, name=name.strip())
            db.session.add(category)
            db.session.flush()
        return category
