from __future__ import annotations
from typing import Optional, Dict, Any
import time
import random

# Wir nutzen deinen bestehenden HTTP-Wrapper (setzt UA, Retries etc.)
from .http import get
from ..config.settings import Settings


def _nominatim(query: str) -> Optional[Dict[str, Any]]:
    """
    Geocoding via Nominatim (OpenStreetMap).
    Rückgabe: dict mit lat, lon, display_name + einfachen Address-Komponenten.
    """
    base = Settings.__dict__.get("GEOCODER_NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
    params = {
        "q": query,
        "format": "json",
        "addressdetails": 1,
        "limit": 1,
    }
    # Nominatim bittet um moderate Raten. Mini-Jitter:
    time.sleep(0.3 + random.random() * 0.4)
    r = get(base, params=params)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    hit = data[0]
    addr = hit.get("address", {}) or {}
    return {
        "lat": float(hit.get("lat")),
        "lon": float(hit.get("lon")),
        "display_name": hit.get("display_name"),
        # nützliche Felder, falls vorhanden
        "house_number": addr.get("house_number"),
        "road": addr.get("road"),
        "postcode": addr.get("postcode"),
        "city": addr.get("city") or addr.get("town") or addr.get("village"),
        "state": addr.get("state"),
        "country": addr.get("country"),
    }


def _photon(query: str) -> Optional[Dict[str, Any]]:
    """
    Geocoding via Photon (Komoot).
    Rückgabe: dict mit lat, lon, display_name + einfachen Properties.
    """
    base = Settings.__dict__.get("GEOCODER_PHOTON_URL", "https://photon.komoot.io/api")
    params = {
        "q": query,
        "limit": 1,
        "lang": "de",
    }
    time.sleep(0.2 + random.random() * 0.3)
    r = get(base, params=params)
    r.raise_for_status()
    data = r.json()
    feats = (data or {}).get("features") or []
    if not feats:
        return None
    f = feats[0]
    coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
    props = f.get("properties") or {}
    lon, lat = coords[0], coords[1]
    if lat is None or lon is None:
        return None
    # display_name basteln
    label_parts = [props.get(k) for k in ("name", "housenumber", "street", "postcode", "city", "country")]
    display_name = ", ".join([p for p in label_parts if p])
    return {
        "lat": float(lat),
        "lon": float(lon),
        "display_name": display_name or props.get("name"),
        "house_number": props.get("housenumber"),
        "road": props.get("street"),
        "postcode": props.get("postcode"),
        "city": props.get("city"),
        "state": props.get("state"),
        "country": props.get("country"),
    }


def geocode_address(query: str) -> Optional[Dict[str, Any]]:
    """
    Öffentliche API, die dein enrich.py aufruft.
    Versucht zuerst Nominatim, dann Photon (falls erlaubt/konfiguriert).
    """
    if not query or not Settings.__dict__.get("ENRICH_GEOCODE", True):
        return None

    preferred = (Settings.__dict__.get("GEOCODER") or "nominatim").lower()
    use_photon = Settings.__dict__.get("GEOCODER_ALLOW_FALLBACK", True)

    try_order = []
    if preferred == "nominatim":
        try_order = [_nominatim, _photon] if use_photon else [_nominatim]
    elif preferred == "photon":
        try_order = [_photon, _nominatim] if use_photon else [_photon]
    else:
        try_order = [_nominatim, _photon]

    for fn in try_order:
        try:
            res = fn(query)
            if res:
                return res
        except Exception:
            # still, try the next one
            continue
    return None


# Alias für mögliche ältere Aufrufer
def geocode(query: str) -> Optional[Dict[str, Any]]:
    return geocode_address(query)
