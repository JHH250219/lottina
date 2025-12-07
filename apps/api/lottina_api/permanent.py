from __future__ import annotations

import re
from datetime import datetime, timedelta, time, timezone
from typing import Dict, Iterable, Tuple, Optional, Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import OfferAvailability, OfferType, Offer

DAY_TOKENS = ["mo", "di", "mi", "do", "fr", "sa", "so"]
DAY_TO_INDEX = {token: index for index, token in enumerate(DAY_TOKENS)}
DEFAULT_SPAN = (time(0, 0), time(23, 59))
DAY_NORMALIZATION = {
    "montag": "mo",
    "mon": "mo",
    "dienstag": "di",
    "tuesday": "di",
    "mittwoch": "mi",
    "wednesday": "mi",
    "donnerstag": "do",
    "thursday": "do",
    "freitag": "fr",
    "friday": "fr",
    "samstag": "sa",
    "saturday": "sa",
    "sonntag": "so",
    "sunday": "so",
    "werktags": "mo-fr",
    "werktage": "mo-fr",
    "wochenende": "sa-so",
}
ALL_DAY_KEYWORDS = ("täglich", "daily", "jeden tag", "jedentag", "immer", "every day", "24/7")
CLOSED_KEYWORDS = ("geschlossen", "ruhetag", "ruhetage", "closed")
STRUCTURED_DAY_KEYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
DAY_RANGE_RX = re.compile(
    r"\b(mo|di|mi|do|fr|sa|so)\b(?:\s*(?:-|–|bis)\s*\b(mo|di|mi|do|fr|sa|so)\b)?"
)
TIME_RANGE_RX = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\s*(?:[-–]|bis)\s*(\d{1,2})(?::(\d{2}))?"
)
TIME_TOKEN_RX = re.compile(r"(\d{1,2})(?::(\d{2}))?")


def opening_hours_text(raw_value: Any) -> Optional[str]:
    """Return a cleaned text representation of opening hours."""
    if not raw_value:
        return None
    if isinstance(raw_value, str):
        cleaned = raw_value.strip()
        return cleaned or None
    if isinstance(raw_value, dict):
        for key in ("general", "raw", "text", "note", "value"):
            value = raw_value.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
        # Fall back to joined string
        parts: list[str] = []
        for key, value in raw_value.items():
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        if parts:
            return " · ".join(parts)
    return None


def sync_permanent_availability(
    session: Session,
    offer: Offer,
    *,
    horizon_days: int = 60,
) -> None:
    """Ensure permanent offers keep availability slots for the next horizon."""

    type_value = getattr(offer.type, "value", offer.type)
    if type_value != OfferType.permanent.value:
        _clear_availability(session, offer.id)
        return

    session.flush()
    if not offer.id:
        return

    schedule = _schedule_for_offer(offer)
    if not schedule:
        schedule = {day: DEFAULT_SPAN for day in range(7)}

    today = datetime.now(timezone.utc).date()
    horizon_end = today + timedelta(days=horizon_days)

    existing = (
        session.query(OfferAvailability)
        .filter(
            OfferAvailability.offer_id == offer.id,
            OfferAvailability.day >= today,
            OfferAvailability.day < horizon_end,
        )
        .all()
    )
    existing_map = {entry.day: entry for entry in existing}

    for offset in range(horizon_days):
        current_day = today + timedelta(days=offset)
        rule = schedule.get(current_day.weekday())
        entry = existing_map.get(current_day)
        if rule is None:
            if entry:
                session.delete(entry)
            continue
        opens_at, closes_at = rule
        if entry:
            entry.opens_at = opens_at
            entry.closes_at = closes_at
        else:
            session.add(
                OfferAvailability(
                    offer_id=offer.id,
                    day=current_day,
                    opens_at=opens_at,
                    closes_at=closes_at,
                )
            )

    session.query(OfferAvailability).filter(
        OfferAvailability.offer_id == offer.id,
        or_(OfferAvailability.day < today, OfferAvailability.day >= horizon_end),
    ).delete(synchronize_session=False)


def _clear_availability(session: Session, offer_id) -> None:
    if not offer_id:
        return
    session.query(OfferAvailability).filter(
        OfferAvailability.offer_id == offer_id
    ).delete(synchronize_session=False)


def _schedule_for_offer(offer: Offer) -> Dict[int, Tuple[time, time]] | None:
    data = offer.opening_hours
    structured = _schedule_from_structured(data)
    if structured:
        return structured
    text = opening_hours_text(data)
    if not text:
        return None
    parsed = _schedule_from_text(text)
    return parsed


def _schedule_from_structured(data: Any) -> Optional[Dict[int, Tuple[time, time]]]:
    if not isinstance(data, dict):
        return None
    schedule: Dict[int, Tuple[time, time]] = {}
    for key, day_index in STRUCTURED_DAY_KEYS.items():
        if key not in data:
            continue
        parsed = _coerce_hours(data.get(key))
        if parsed:
            schedule[day_index] = parsed
    return schedule or None


def _coerce_hours(value: Any) -> Optional[Tuple[time, time]]:
    if value is None:
        return None
    if isinstance(value, str):
        return _parse_time_window(value)
    if isinstance(value, dict):
        start = value.get("from") or value.get("start") or value.get("open")
        end = value.get("to") or value.get("end") or value.get("close")
        if start and end:
            start_time = _parse_time_token(str(start))
            end_time = _parse_time_token(str(end))
            if start_time and end_time:
                return (start_time, end_time)
        if "value" in value and isinstance(value["value"], str):
            return _parse_time_window(value["value"])
    if isinstance(value, list):
        for entry in value:
            parsed = _coerce_hours(entry)
            if parsed:
                return parsed
    return None


def _schedule_from_text(text: str) -> Dict[int, Tuple[time, time]]:
    normalized = text.lower()
    normalized = _normalize_days(normalized)

    rules: Dict[int, Tuple[time, time]] = {}
    segments = re.split(r"[;\n]+", normalized)
    for segment in segments:
        segment = segment.strip(",. ")
        if not segment or any(keyword in segment for keyword in CLOSED_KEYWORDS):
            continue

        days = _extract_days(segment)
        if not days:
            if any(keyword in segment for keyword in ALL_DAY_KEYWORDS):
                days = list(range(7))

        times = _parse_time_window(segment)
        if not times and days:
            # If the segment names days but no times, consider them full-day.
            times = DEFAULT_SPAN
        if not days and times:
            days = list(range(7))

        if not days or not times:
            continue

        for day in days:
            if day not in rules:
                rules[day] = times

    return rules


def _normalize_days(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).replace("–", "-")
    for source, target in DAY_NORMALIZATION.items():
        normalized = re.sub(rf"\b{source}\b", target, normalized)
    normalized = normalized.replace("&", ",").replace("+", ",").replace(" / ", ",")
    return normalized


def _extract_days(segment: str) -> list[int]:
    matches = DAY_RANGE_RX.findall(segment)
    days: list[int] = []
    for start_token, end_token in matches:
        days.extend(_expand_day_range(start_token, end_token or start_token))
    return sorted(set(days))


def _expand_day_range(start_token: str, end_token: str) -> Iterable[int]:
    start_index = DAY_TO_INDEX.get(start_token)
    end_index = DAY_TO_INDEX.get(end_token)
    if start_index is None:
        return []
    if end_index is None:
        end_index = start_index
    if start_index <= end_index:
        return list(range(start_index, end_index + 1))
    return list(range(start_index, 7)) + list(range(0, end_index + 1))


def _parse_time_window(text: str) -> Optional[Tuple[time, time]]:
    match = TIME_RANGE_RX.search(text)
    if match:
        start_time = _normalize_time(int(match.group(1)), match.group(2))
        end_time = _normalize_time(int(match.group(3)), match.group(4))
        return (start_time, end_time)

    tokens = TIME_TOKEN_RX.findall(text)
    if len(tokens) >= 2:
        start_time = _normalize_time(int(tokens[0][0]), tokens[0][1])
        end_time = _normalize_time(int(tokens[1][0]), tokens[1][1])
        return (start_time, end_time)

    if any(keyword in text for keyword in ("24/7", "24h", "ganztägig", "durchgehend")):
        return DEFAULT_SPAN

    return None


def _parse_time_token(value: str) -> Optional[time]:
    match = TIME_TOKEN_RX.search(value)
    if not match:
        return None
    return _normalize_time(int(match.group(1)), match.group(2))


def _normalize_time(hour: int, minute_text: Optional[str]) -> time:
    minutes = int(minute_text) if minute_text is not None else 0
    if hour >= 24:
        hour = 23
        minutes = 59
    hour = max(0, min(hour, 23))
    minutes = max(0, min(minutes, 59))
    return time(hour, minutes)
