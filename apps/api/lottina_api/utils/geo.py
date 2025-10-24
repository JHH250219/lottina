import math
from lottina_api.models import Offer, Location

def haversine(lat1, lon1, lat2, lon2):
    """Distanz in km zwischen zwei Punkten berechnen (lat/lon in Grad)."""
    R = 6371  # Erd-Radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

def find_offers_nearby(session, lat, lon, radius_km=5):
    """Alle Offers im Umkreis von radius_km zurückgeben."""
    offers = (
        session.query(Offer)
        .join(Location)
        .filter(Location.lat.isnot(None), Location.lon.isnot(None))
        .all()
    )

    return [
        offer for offer in offers
        if haversine(lat, lon, offer.location.lat, offer.location.lon) <= radius_km
    ]
