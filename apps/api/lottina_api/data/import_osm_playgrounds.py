#!/usr/bin/env python3
"""Import script for OpenStreetMap playgrounds exported via Overpass Turbo."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

try:
    from .app import app, db
    from .models import (
        Category,
        Location,
        Offer,
        OfferStatus,
        OfferType,
        SourceType,
    )
except ImportError:
    import sys

    BASE_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = BASE_DIR.parent.parent.parent
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from apps.api.lottina_api.app import app, db
    from apps.api.lottina_api.models import (
        Category,
        Location,
        Offer,
        OfferStatus,
        OfferType,
        SourceType,
    )


PLAYGROUND_CATEGORY_SLUG = "spielplatz"
PLAYGROUND_CATEGORY_NAME = "Spielplatz"
DEFAULT_DESCRIPTION = "Oeffentlicher Spielplatz."
DEFAULT_SUMMARY = "Spielplatz."
DEFAULT_SOURCE = "osm"

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Importiert Spielplaetze aus einer Overpass-Turbo JSON-Datei."
    )
    parser.add_argument(
        "json_file",
        help="Pfad zur Overpass JSON-Datei (z. B. aachen_playgrounds.json)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Detailgrad des Konsolen-Logs.",
    )
    return parser.parse_args()


def load_elements(json_path: Path) -> list[dict[str, Any]]:
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Ungueltige JSON-Datei: 'elements' fehlt oder ist kein Array.")
    return elements


def ensure_category() -> Category:
    category = (
        Category.query.filter(Category.slug == PLAYGROUND_CATEGORY_SLUG).one_or_none()
    )
    if category is None:
        category = Category(slug=PLAYGROUND_CATEGORY_SLUG, name=PLAYGROUND_CATEGORY_NAME)
        db.session.add(category)
        db.session.flush()
        logger.info(
            "Kategorie '%s' neu erstellt.",
            PLAYGROUND_CATEGORY_SLUG,
        )
    return category


def import_elements(elements: Iterable[dict[str, Any]]) -> Tuple[int, int]:
    new_locations = 0
    new_offers = 0
    category = ensure_category()

    for element in elements:
        if not isinstance(element, dict):
            logger.debug("Ueberspringe ungueltiges Element: %r", element)
            continue
        tags = element.get("tags") or {}
        if not isinstance(tags, dict) or tags.get("leisure") != "playground":
            continue

        element_type = element.get("type")
        element_id = element.get("id")
        if element_type not in {"node", "way", "relation"} or element_id is None:
            logger.debug("Ueberspringe Element ohne gueltige type/id: %s", element)
            continue

        fingerprint = f"osm-{element_type}-{element_id}"
        lat, lon = extract_coordinates(element)
        name = clean_string(tags.get("name"))
        address, city = extract_address_and_city(tags)

        location, created_loc = upsert_location(
            fingerprint=fingerprint,
            name=name,
            address=address,
            city=city,
            lat=lat,
            lon=lon,
        )
        if created_loc:
            new_locations += 1

        offer, created_offer = upsert_offer(
            element_type=element_type,
            element_id=element_id,
            location=location,
            name=name,
            category=category,
        )
        if created_offer:
            new_offers += 1

    return new_locations, new_offers


def upsert_location(
    *,
    fingerprint: str,
    name: str | None,
    address: str | None,
    city: str | None,
    lat: float | None,
    lon: float | None,
) -> tuple[Location, bool]:
    location = Location.query.filter(Location.fingerprint == fingerprint).one_or_none()
    created = False

    if location is None:
        location = Location(fingerprint=fingerprint)
        db.session.add(location)
        created = True

    if name:
        location.name = name
    if address:
        location.address = address
    if city:
        location.city = city
    if lat is not None:
        location.lat = lat
    if lon is not None:
        location.lon = lon

    return location, created


def upsert_offer(
    *,
    element_type: str,
    element_id: Any,
    location: Location,
    name: str | None,
    category: Category,
) -> tuple[Offer, bool]:
    external_id = f"osm-{element_type}-{element_id}"
    source_url = f"https://www.openstreetmap.org/{element_type}/{element_id}"

    offer = Offer.query.filter(Offer.external_id == external_id).one_or_none()
    created = False

    if offer is None:
        offer = Offer(
            external_id=external_id,
        )
        db.session.add(offer)
        created = True

    offer.title = name or PLAYGROUND_CATEGORY_NAME
    offer.description = DEFAULT_DESCRIPTION
    offer.summary = DEFAULT_SUMMARY
    offer.source = DEFAULT_SOURCE
    offer.source_url = source_url
    offer.maps_url = source_url
    offer.type = OfferType.permanent
    offer.is_free = True
    offer.is_outdoor = True
    offer.is_indoor = False
    offer.source_type = SourceType.crawler
    offer.status = OfferStatus.published
    offer.is_once = False
    offer.location = location

    if category not in offer.categories:
        offer.categories.append(category)

    return offer, created


def extract_coordinates(element: Dict[str, Any]) -> tuple[float | None, float | None]:
    if element.get("type") == "node":
        return coerce_float(element.get("lat")), coerce_float(element.get("lon"))

    center = element.get("center") or {}
    if isinstance(center, dict):
        return coerce_float(center.get("lat")), coerce_float(center.get("lon"))
    return None, None


def extract_address_and_city(tags: Dict[str, Any]) -> tuple[str | None, str | None]:
    street = clean_string(tags.get("addr:street"))
    house_number = clean_string(tags.get("addr:housenumber"))
    postcode = clean_string(tags.get("addr:postcode"))

    city = (
        clean_string(tags.get("addr:city"))
        or clean_string(tags.get("addr:town"))
        or clean_string(tags.get("addr:village"))
        or clean_string(tags.get("is_in:city"))
    )

    line1 = None
    if street and house_number:
        line1 = f"{street} {house_number}".strip()
    elif street:
        line1 = street
    elif house_number:
        line1 = house_number

    line2_parts = [part for part in (postcode, city) if part]
    line2 = " ".join(line2_parts) if line2_parts else None

    address_parts = [part for part in (line1, line2) if part]
    address = ", ".join(address_parts) if address_parts else None

    return address, city


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.debug("Konnte Wert nicht in float umwandeln: %r", value)
        return None


def clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")

    json_path = Path(args.json_file).expanduser().resolve()
    if not json_path.is_file():
        raise SystemExit(f"Datei nicht gefunden: {json_path}")

    with app.app_context():
        try:
            elements = load_elements(json_path)
            new_locations, new_offers = import_elements(elements)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception("Import fehlgeschlagen: %s", exc)
            raise SystemExit(1)

    print(f"Import fertig: {new_locations} neue Locations, {new_offers} neue Offers")


if __name__ == "__main__":
    main()
