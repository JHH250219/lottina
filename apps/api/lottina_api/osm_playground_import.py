"""Utilities to import OpenStreetMap playgrounds into the Lottina database.

The OpenStreetMap data is licensed under ODbL 1.0 and must be attributed as
'© OpenStreetMap-Mitwirkende' when surfaced in the product.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
DEFAULT_TIMEOUT = 60


@dataclass(frozen=True)
class OSMLocation:
    """Lightweight representation of a location originating from OSM."""

    lat: float
    lon: float
    name: Optional[str]
    address: Optional[str]
    city: Optional[str]
    fingerprint: str


@dataclass(frozen=True)
class OSMOffer:
    """Lightweight representation of an offer originating from OSM."""

    external_id: str
    source: str
    source_url: str
    title: str
    description: Optional[str]
    summary: Optional[str]
    image: Optional[str]
    type: str
    opening_hours_json: Optional[dict]
    maps_url: Optional[str]
    is_outdoor: bool
    is_indoor: bool
    is_free: bool
    location: OSMLocation


def fetch_osm_playgrounds(
    center_lat: float,
    center_lon: float,
    radius_m: int = 10_000,
    limit: int = 100,
) -> list[OSMOffer]:
    """Fetch playgrounds from the Overpass API around the given coordinates.

    Args:
        center_lat: Latitude of the search centre.
        center_lon: Longitude of the search centre.
        radius_m: Search radius in metres. Must be positive.
        limit: Maximum number of playgrounds to return. Must be positive.

    Returns:
        A list of playground offers parsed from the Overpass response.

    Raises:
        ValueError: If `radius_m` or `limit` are not positive.
        requests.HTTPError: For HTTP errors returned by the Overpass API.
        requests.RequestException: For network-related issues.
        json.JSONDecodeError: If the Overpass response is not valid JSON.
    """

    if radius_m <= 0:
        raise ValueError("radius_m must be a positive integer")
    if limit <= 0:
        raise ValueError("limit must be a positive integer")

    query = _build_overpass_query(center_lat, center_lon, radius_m)

    logger.debug("Fetching OSM playgrounds via Overpass: radius=%s, limit=%s", radius_m, limit)
    response = requests.post(OVERPASS_ENDPOINT, data=query, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()

    payload = response.json()
    elements = payload.get("elements", [])
    offers: list[OSMOffer] = []

    for element in elements:
        offer = _element_to_offer(element)
        if offer is None:
            continue
        offers.append(offer)
        if len(offers) >= limit:
            break

    logger.info("Fetched %s playground offers from Overpass", len(offers))
    return offers


def persist_offers(
    session,
    rows: Iterable[OSMOffer],
    Offer,
    Location,
    Category,
    OfferType,
    SourceType,
    default_category_slug: str = "playground",
    default_category_name: str = "Spielplatz",
) -> Tuple[int, int]:
    """Upsert the provided OSM offers into the SQLAlchemy models.

    Args:
        session: SQLAlchemy session to use for persistence.
        rows: Iterable of `OSMOffer` objects to persist.
        Offer: SQLAlchemy Offer model.
        Location: SQLAlchemy Location model.
        Category: SQLAlchemy Category model.
        OfferType: OfferType enum from the models.
        SourceType: SourceType enum from the models.
        default_category_slug: Slug of the category to associate with offers.
        default_category_name: Display name of the default category.

    Returns:
        A tuple with the counts of inserted and updated offers.
    """

    rows = list(rows)
    if not rows:
        return (0, 0)

    category = (
        session.query(Category)
        .filter(Category.slug == default_category_slug)
        .one_or_none()
    )
    if category is None:
        category = Category(slug=default_category_slug, name=default_category_name)
        session.add(category)
        session.flush()
        logger.info("Created category %s (%s)", default_category_slug, default_category_name)

    inserted = 0
    updated = 0

    for row in rows:
        location = _upsert_location(session, row.location, Location)
        offer = session.query(Offer).filter(Offer.external_id == row.external_id).one_or_none()

        if offer is None:
            offer = Offer(external_id=row.external_id)
            session.add(offer)
            inserted += 1
            _populate_new_offer(
                offer=offer,
                row=row,
                location=location,
                OfferType=OfferType,
                SourceType=SourceType,
            )
        else:
            changed = _update_existing_offer(
                offer=offer,
                row=row,
                location=location,
                OfferType=OfferType,
                SourceType=SourceType,
            )
            if changed:
                updated += 1

        if category not in offer.categories:
            offer.categories.append(category)

    return (inserted, updated)


def dump_to_json(rows: Iterable[OSMOffer], path: str) -> None:
    """Write the provided OSM offers into a JSON file for debugging."""

    serialized = []
    for row in rows:
        serialized.append(
            {
                "external_id": row.external_id,
                "source": row.source,
                "source_url": row.source_url,
                "title": row.title,
                "description": row.description,
                "summary": row.summary,
                "image": row.image,
                "type": row.type,
                "opening_hours": row.opening_hours_json,
                "maps_url": row.maps_url,
                "is_outdoor": row.is_outdoor,
                "is_indoor": row.is_indoor,
                "is_free": row.is_free,
                "location": {
                    "lat": row.location.lat,
                    "lon": row.location.lon,
                    "name": row.location.name,
                    "address": row.location.address,
                    "city": row.location.city,
                    "fingerprint": row.location.fingerprint,
                },
            }
        )

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(serialized, handle, indent=2, ensure_ascii=False)


def _build_overpass_query(center_lat: float, center_lon: float, radius_m: int) -> str:
    """Create the Overpass QL query for playgrounds."""

    return (
        "[out:json][timeout:{timeout}];"
        "("
        'node["leisure"="playground"](around:{radius},{lat},{lon});'
        'way["leisure"="playground"](around:{radius},{lat},{lon});'
        'relation["leisure"="playground"](around:{radius},{lat},{lon});'
        ");"
        "out tags center;"
    ).format(timeout=DEFAULT_TIMEOUT, radius=radius_m, lat=center_lat, lon=center_lon)


def _element_to_offer(element: dict) -> Optional[OSMOffer]:
    """Convert a single Overpass element into an `OSMOffer`, if possible."""

    tags = element.get("tags") or {}
    if not tags:
        return None

    coords = _extract_coordinates(element)
    if coords is None:
        return None
    lat, lon = coords

    name = _strip_or_none(tags.get("name"))
    city = (
        _strip_or_none(tags.get("addr:city"))
        or _strip_or_none(tags.get("addr:town"))
        or _strip_or_none(tags.get("addr:village"))
    )
    address = _build_address(tags)

    title = name or _generate_fallback_title(tags, city)
    if not title:
        return None

    location = OSMLocation(
        lat=lat,
        lon=lon,
        name=name,
        address=address,
        city=city,
        fingerprint=_make_location_fingerprint(lat, lon, address),
    )

    element_type = element.get("type")
    element_id = element.get("id")
    if not element_type or element_id is None:
        return None

    element_id_str = str(element_id)
    external_id = f"osm:{element_type}:{element_id_str}"
    source_url = f"https://www.openstreetmap.org/{element_type}/{element_id_str}"
    maps_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon}"

    opening_hours_tag = _strip_or_none(tags.get("opening_hours"))
    opening_hours_json = {"raw": opening_hours_tag} if opening_hours_tag else None

    return OSMOffer(
        external_id=external_id,
        source="OpenStreetMap",
        source_url=source_url,
        title=title,
        description=_strip_or_none(tags.get("description")),
        summary=_strip_or_none(tags.get("note")),
        image=_resolve_image(tags),
        type="permanent",
        opening_hours_json=opening_hours_json,
        maps_url=maps_url,
        is_outdoor=True,
        is_indoor=False,
        is_free=True,
        location=location,
    )


def _extract_coordinates(element: dict) -> Optional[Tuple[float, float]]:
    """Extract coordinates from an Overpass element."""

    if "center" in element:
        center = element["center"]
        lat = center.get("lat")
        lon = center.get("lon")
    else:
        lat = element.get("lat")
        lon = element.get("lon")

    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _build_address(tags: dict) -> Optional[str]:
    """Compose an address string from OSM tags."""

    street = _strip_or_none(tags.get("addr:street"))
    number = _strip_or_none(tags.get("addr:housenumber"))
    postcode = _strip_or_none(tags.get("addr:postcode"))
    city = (
        _strip_or_none(tags.get("addr:city"))
        or _strip_or_none(tags.get("addr:town"))
        or _strip_or_none(tags.get("addr:village"))
    )

    line1_parts = [part for part in (street, number) if part]
    line1 = " ".join(line1_parts) if line1_parts else None
    line2_parts = [part for part in (postcode, city) if part]
    line2 = " ".join(line2_parts) if line2_parts else None

    if line1 and line2:
        return f"{line1}, {line2}"
    return line1 or line2


def _generate_fallback_title(tags: dict, city: Optional[str]) -> Optional[str]:
    """Generate a fallback title if none is provided by OSM."""

    street = _strip_or_none(tags.get("addr:street"))
    if street:
        return f"Spielplatz {street}"
    if city:
        return f"Spielplatz {city}"
    return None


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    """Return a stripped string or None if the input is falsey."""

    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _make_location_fingerprint(lat: float, lon: float, address: Optional[str]) -> str:
    """Create a deterministic fingerprint for a location."""

    payload = f"{lat:.6f}|{lon:.6f}|{address or ''}"
    return sha256(payload.encode("utf-8")).hexdigest()


def _resolve_image(tags: dict) -> Optional[str]:
    """Resolve an image URL from OSM tags."""

    image = _strip_or_none(tags.get("image"))
    if image:
        return image

    commons = _strip_or_none(tags.get("wikimedia_commons"))
    if commons:
        # Strip commons prefixes like "File:" or "Category:" and build direct URL.
        filename = commons.replace("File:", "").replace("file:", "")
        filename = filename.replace(" ", "_")
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}"

    return None


def _upsert_location(session, location: OSMLocation, Location):
    """Find or create a location record based on the provided fingerprint."""

    instance = (
        session.query(Location)
        .filter(Location.fingerprint == location.fingerprint)
        .one_or_none()
    )

    if instance is None:
        instance = Location(
            fingerprint=location.fingerprint,
            lat=location.lat,
            lon=location.lon,
            name=location.name,
            address=location.address,
            city=location.city,
        )
        session.add(instance)
        session.flush()
        return instance

    updated = False
    if instance.lat is None:
        instance.lat = location.lat
        updated = True
    if instance.lon is None:
        instance.lon = location.lon
        updated = True
    if location.name and not instance.name:
        instance.name = location.name
        updated = True
    if location.address and not instance.address:
        instance.address = location.address
        updated = True
    if location.city and not instance.city:
        instance.city = location.city
        updated = True

    if updated:
        session.flush()

    return instance


def _populate_new_offer(offer, row: OSMOffer, location, OfferType, SourceType) -> None:
    """Populate a newly created offer with OSM data."""

    offer.title = row.title
    offer.description = row.description
    offer.summary = row.summary
    offer.image = row.image
    offer.type = OfferType.permanent
    offer.opening_hours = row.opening_hours_json
    offer.maps_url = row.maps_url
    offer.is_outdoor = row.is_outdoor
    offer.is_indoor = row.is_indoor
    offer.is_free = row.is_free
    offer.is_once = False
    offer.source = row.source
    offer.source_name = row.source
    offer.source_url = row.source_url
    offer.source_type = SourceType.crawler
    offer.location = location


def _update_existing_offer(offer, row: OSMOffer, location, OfferType, SourceType) -> bool:
    """Update an existing offer with selected OSM fields."""

    changed = False

    if offer.title != row.title:
        offer.title = row.title
        changed = True

    if offer.image != row.image:
        offer.image = row.image
        changed = True

    if offer.maps_url != row.maps_url:
        offer.maps_url = row.maps_url
        changed = True

    if offer.opening_hours in (None, {}) and row.opening_hours_json:
        offer.opening_hours = row.opening_hours_json
        changed = True

    if offer.location != location:
        offer.location = location
        changed = True

    if offer.type != OfferType.permanent:
        offer.type = OfferType.permanent
        changed = True

    if offer.source != row.source:
        offer.source = row.source
        changed = True

    if offer.source_url != row.source_url:
        offer.source_url = row.source_url
        changed = True

    if offer.source_name != row.source:
        offer.source_name = row.source
        changed = True

    if offer.source_type != SourceType.crawler:
        offer.source_type = SourceType.crawler
        changed = True

    if offer.is_outdoor is not True:
        offer.is_outdoor = True
        changed = True

    if offer.is_indoor is not False:
        offer.is_indoor = False
        changed = True

    if offer.is_free is not True:
        offer.is_free = True
        changed = True

    if offer.is_once is not False:
        offer.is_once = False
        changed = True

    return changed


if __name__ == "__main__":
    example_lat, example_lon = 50.7753, 6.0839  # Aachen, Germany
    sample_rows = fetch_osm_playgrounds(example_lat, example_lon, radius_m=5_000, limit=5)
    dump_to_json(sample_rows, "osm_playgrounds_sample.json")
    print(f"Fetched {len(sample_rows)} playground offers and wrote them to osm_playgrounds_sample.json")
