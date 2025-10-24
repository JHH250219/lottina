from __future__ import annotations
import re
from datetime import date
from typing import Tuple, Optional
import difflib

# ---------- Kürzen ----------
def shorten(s: Optional[str], n: int) -> Optional[str]:
    if not s: return None
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]

# ---------- Adresse / Stadt ----------
_ADDR_RE = re.compile(
    r"([A-ZÄÖÜa-zäöüß][^,\n]{2,}?\s\d+[a-zA-Z]?|"          # „… 1“
    r"[A-ZÄÖÜa-zäöüß][^,\n]{2,}?(?:straße|str\.|weg|platz|allee)\s*\d+[a-zA-Z]?)",
    re.IGNORECASE
)
_PLZ_STADT_RE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][a-zäöüß\-]+)\b")

def extract_addr_city_from_text(txt: str) -> Tuple[Optional[str], Optional[str]]:
    addr = None
    m = _ADDR_RE.search(txt or "")
    if m: addr = m.group(1)
    city = None
    m2 = _PLZ_STADT_RE.search(txt or "")
    if m2: city = f"{m2.group(1)} {m2.group(2)}"
    if not city:
        for ln in (txt or "").splitlines():
            ln = ln.strip()
            if re.fullmatch(r"[A-ZÄÖÜ][a-zäöüß]+(?:[-\s][A-ZÄÖÜ][a-zäöüß]+)?", ln):
                city = ln; break
    return addr, city

# ---------- Datum / Zeit ----------
_DATE_RE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS_DE = {
    "januar":1,"jan":1,"februar":2,"feb":2,"märz":3,"maerz":3,"mrz":3,"mär":3,
    "april":4,"apr":4,"mai":5,"juni":6,"jun":6,"juli":7,"jul":7,"august":8,"aug":8,
    "september":9,"sept":9,"sep":9,"oktober":10,"okt":10,"november":11,"nov":11,"dezember":12,"dez":12,
}
# Punkt nach Tag OPTIONAL → erkennt „14. September“ UND „14 September“
_DATE_RE_TEXT = re.compile(r"\b(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüß\.]+)(?:\s+(\d{4}))?\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# --- Noise/Heuristics helpers ---
_SYMBOLS = set("|/:;!()[]{}<>\\^~*_=+@§$%&")
def _has_words(s: str, min_letters: int = 4) -> bool:
    return re.search(r"[A-Za-zÄÖÜäöüß]{%d,}" % min_letters, s) is not None

def _is_noise_line(s: str) -> bool:
    s = s.strip()
    if not s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    digits  = sum(ch.isdigit() for ch in s)
    sym     = sum(ch in _SYMBOLS for ch in s)
    if not _has_words(s):
        return True
    if sym / max(1, len(s)) > 0.30:
        return True
    if digits >= 8 and letters < 6:
        return True
    return False

def _fix_month_token(token: str) -> str:
    """Normalize common OCR confusions for German month tokens and fuzzy match."""
    t = token.lower().strip(".")
    t = (t.replace("ä","ae").replace("ö","oe").replace("ü","ue")
           .replace("0","o").replace("|","l").replace("1","l").replace("5","s"))
    keys = list(_MONTHS_DE.keys())
    match = difflib.get_close_matches(t, keys, n=1, cutoff=0.75)
    return match[0] if match else t

_DAY_HINTS = ("mo", "di", "mi", "do", "fr", "sa", "so", "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")

def extract_opening_hours(lines: list[str]) -> Optional[str]:
    bucket: list[str] = []
    for ln in lines:
        low = ln.lower()
        has_day = any(re.search(rf"\b{day}\b", low) for day in _DAY_HINTS)
        has_time = bool(re.search(r"\d{1,2}[:\.]\d{2}", low)) or "uhr" in low or "bis" in low
        if "öffnungs" in low or (has_day and has_time):
            bucket.append(ln.strip())
    if not bucket:
        return None
    seen: list[str] = []
    for ln in bucket:
        if ln not in seen:
            seen.append(ln)
    return " / ".join(seen[:4])

# Uhrzeiten: erlaubt :, ., -, Leerzeichen, Komma
_TIME_RE = re.compile(r"\b(\d{1,2})[:\.\-,\s](\d{2})\s*(?:uhr)?\b", re.IGNORECASE)
# Zeitspannen „von … bis …“
_TIME_RANGE_RE = re.compile(
    r"\bvon\s+(\d{1,2})[:\.\-,\s]?(\d{2})\s*(?:uhr)?\s*bis\s*(\d{1,2})[:\.\-,\s]?(\d{2})",
    re.IGNORECASE
)

def norm_date_from_text(block: str) -> Optional[str]:
    m = _DATE_RE_NUM.search(block)
    if m:
        if m.group(1):
            d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100: y += 2000
        else:
            y, mth, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
        try: return date(y, mth, d).isoformat()
        except Exception: return None
    m = _DATE_RE_TEXT.search(block)
    if m:
        d = int(m.group(1))
        mon_raw = _fix_month_token(m.group(2))
        mth = _MONTHS_DE.get(mon_raw)
        if not mth: return None
        y = int(m.group(3)) if m.group(3) else date.today().year
        try:
            dt = date(y, mth, d)
            if not m.group(3) and dt < date.today(): dt = date(y+1, mth, d)
            return dt.isoformat()
        except Exception: return None
    return None

def _norm_hhmm(h: str|int, m: str|int) -> str:
    hh = int(h); mm = int(m)
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return ""

def norm_time_from_text(block: str) -> Optional[str]:
    # Bevorzugt frühesten Beginn aus einer Spanne „von … bis …“
    ranges = _TIME_RANGE_RE.findall(block or "")
    if ranges:
        starts = sorted((_norm_hhmm(a,b) for a,b,_,_ in ranges if _norm_hhmm(a,b)), key=lambda s: s)
        if starts: return starts[0]
    m = _TIME_RE.search(block or "")
    if not m: return None
    t = _norm_hhmm(m.group(1), m.group(2))
    return t or None

def extract_time_ranges_text(block: str) -> str:
    """Gibt schön formatierten Text für alle gefundenen Zeitspannen zurück."""
    spans = []
    for a,b,c,d in _TIME_RANGE_RE.findall(block or ""):
        s = _norm_hhmm(a,b); e = _norm_hhmm(c,d)
        if s and e: spans.append(f"{s}–{e} Uhr")
    return ", ".join(spans)

def extract_primary_time_range(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Liefert die erste Zeitspanne (Start, Ende) aus dem Text."""
    ranges: list[Tuple[str, Optional[str]]] = []
    for a, b, c, d in _TIME_RANGE_RE.findall(text or ""):
        start = _norm_hhmm(a, b)
        end = _norm_hhmm(c, d) if c and d else None
        if start:
            ranges.append((start, end))
    if not ranges:
        return None, None
    ranges.sort(key=lambda pair: pair[0])
    return ranges[0]

def clean_location_string(value: Optional[str]) -> Optional[str]:
    """Entfernt Datum- und Zeitangaben aus einer Ortszeile."""
    if not value:
        return value
    cleaned = _TIME_RANGE_RE.sub(" ", value)
    cleaned = _TIME_RE.sub(" ", cleaned)
    cleaned = _DATE_RE_NUM.sub(" ", cleaned)
    cleaned = _DATE_RE_TEXT.sub(" ", cleaned)
    cleaned = re.sub(r"\b(von|bis)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"[,\-;]\s*$", "", cleaned)
    cleaned = cleaned.strip(" ,;-")
    return cleaned or None

_CATEGORY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("Theater", ("theater", "bühne", "puppentheater", "musical", "aufführung", "oper")),
    ("Musik", ("konzert", "band", "musik", "chor", "singen", "dj", "sinfonie", "jazz")),
    ("Sport", ("sport", "turnier", "lauf", "rennen", "spiel", "training", "yoga", "fitness", "tanzen")),
    ("Workshop", ("workshop", "kurs", "seminar", "training", "lernen", "fortbildung", "basteln", "mitmach")),
    ("Museum", ("museum", "ausstellung", "vernissage", "galerie", "führung", "exponat")),
    ("Fest & Markt", ("fest", "markt", "jahrmarkt", "feier", "festival", "kirmes", "straßenfest")),
    ("Outdoor", ("outdoor", "draußen", "wandern", "radtour", "natur", "wald", "park", "picknick")),
    ("Familie & Kinder", ("familie", "kinder", "kids", "eltern", "familien", "kindershow", "kita")),
]

def detect_categories(text: str) -> list[str]:
    base = (text or "").lower()
    found: list[str] = []
    for label, keywords in _CATEGORY_HINTS:
        if any(kw in base for kw in keywords):
            found.append(label)
    # Sichere eindeutige Reihenfolge
    seen = set()
    ordered = []
    for label in found:
        if label not in seen:
            ordered.append(label)
            seen.add(label)
    return ordered[:6]

# ---------- Preis (verschärft) ----------
# Nur ERKENNEN, wenn Währung/Preis-Kontext dabei ist (verhindert „09:00 18:00“)
_PRICE_RE = re.compile(
    r"(?:(?:eintritt|preis|ticket|tickets|kosten|gebühr)\s*[:\-]?\s*)?"
    r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(€|eur|euro)",
    re.IGNORECASE
)

# ---------- Location-Heuristik ----------
def guess_location(lines: list[str]) -> Optional[str]:
    L = [ln for ln in lines if not _is_noise_line(ln)]
    for ln in L:
        if _ADDR_RE.search(ln): return ln  # echte Adresse mit Hausnummer
    for ln in L:
        if _PLZ_STADT_RE.search(ln): return ln
    for ln in L:
        low = ln.lower()
        if any(k in low for k in ["straße","str.","str ","platz","weg","allee","hof","mühlenhof","bauernhof"]):
            return ln
    for ln in L:
        if len(ln.split()) == 1 and ln.istitle(): return ln
    return None

# ---------- Titelwahl ----------
_EVENT_HINTS = ("tag","fest","markt","theater","konzert","maja","puppentheater","lauf","feuer","festival")

def _pick_title(lines: list[str]) -> str | None:
    if not lines:
        return None
    # Vorfiltern gegen OCR-Müll
    clean = [ln for ln in lines[:30] if not _is_noise_line(ln)]
    if not clean:
        return None
    hints_rx = re.compile(r"(theater|puppentheater|markt|fest|konzert|lauf|festival)", re.I)
    cands = [ln for ln in clean if hints_rx.search(ln)] or clean

    def score(s: str):
        s2 = s.strip()
        L  = len(s2)
        has_hint = bool(hints_rx.search(s2))
        letters  = sum(ch.isalpha() for ch in s2)
        return (-has_hint, abs(L - 18), -letters)

    cands.sort(key=score)
    t = cands[0]
    return re.sub(r"([A-Za-zÄÖÜäöüß])(\d{4})\b", r"\1 \2", t)

# ---------- Extra: „Alles rund um …“-Block als Kurzbeschreibung ----------
_DESC_START_RE = re.compile(r"\balles\s+rund\s+um\b", re.IGNORECASE)
_STOP_AFTER_RE = re.compile(r"\b(kostenlos|kostenlose|zzgl\.?|wir freuen uns|tickets?)\b", re.IGNORECASE)

def _extract_short_description(text: str) -> Optional[str]:
    lines = [ln.strip() for ln in (text or "").splitlines()]
    if not lines: return None
    out = []
    started = False
    for ln in lines:
        if not started and _DESC_START_RE.search(ln):
            started = True
        if started:
            if _STOP_AFTER_RE.search(ln): break
            out.append(ln.lstrip("+•- ").strip())
    short = " ".join(out).strip()
    return short or None

# ---------- Haupt-Extractor ----------
_FUZZY_EINTRITT = re.compile(r"\b(f|e)intr[iy]t{1,2}|eintrit|eintitt|eintritt\b", re.IGNORECASE)
_ZZGL = re.compile(r"\bzzgl\.?\b", re.IGNORECASE)
_SUMMARY_HINT_WORDS = (
    "führung", "führungen", "reservier", "whatsapp", "telefon",
    "einlass", "ticket", "karte", "karten", "zzgl", "eintritt",
    "dauer", "min", "anmeldung", "kostenlos", "kostenfreie"
)
_TIME_INLINE_RE = re.compile(r"\b(\d{1,2})[:\.\-\s](\d{2})\s*(uhr)?\b", re.IGNORECASE)

def _build_summary(text: str) -> Optional[str]:
    lines = [re.sub(r"^[\s•\-\*\–\—\·]+", "", ln.strip()) for ln in (text or "").splitlines() if ln.strip()]
    picked = []
    for ln in lines:
        low = ln.lower()
        if any(w in low for w in _SUMMARY_HINT_WORDS) or _TIME_INLINE_RE.search(low):
            if ln not in picked:
                picked.append(ln)
    if not picked:
        return None
    # kompakt zusammenfassen und auf DB-Limit (400) trimmen
    s = " · ".join(picked)
    return shorten(s, 380)  # etwas Puffer

def extract_fields(text: str) -> dict:
    raw_lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln for ln in raw_lines if not _is_noise_line(ln)]
    fields = {
        "title": None, "description": None, "summary": None,  # <- summary hinzu
        "date": None, "time": None, "time_end": None,
        "location": None, "category": None, "age_group": None,
        "price": None, "is_free": None, "is_outdoor": None,
        "maps_url": None, "source_url": None, "source_name": None,
        "lat": None, "lon": None, "image_url": None,
        "categories": [],
        "contact": None, "opening_hours": None, "price_info": None, "registration": None,
    }

    cand = _pick_title(lines)
    if cand:
        fields["title"] = cand

    fields["date"] = norm_date_from_text(text)
    start_range, end_range = extract_primary_time_range(text)
    if start_range:
        fields["time"] = start_range
    else:
        fields["time"] = norm_time_from_text(text)
    if end_range:
        fields["time_end"] = end_range

    loc = guess_location(lines)
    if loc:
        loc_clean = clean_location_string(loc)
        city = None
        for ln in lines:
            if re.fullmatch(r"[A-ZÄÖÜ][a-zäöüß]+(?:[-\s][A-ZÄÖÜ][a-zäöüß]+)?", ln):
                city = ln; break
        location_value = loc_clean
        if city:
            city_clean = (clean_location_string(city) or city).strip()
            if location_value:
                if city_clean and city_clean.lower() not in location_value.lower():
                    location_value = f"{location_value}, {city_clean}"
            else:
                location_value = city_clean
        if location_value:
            fields["location"] = location_value

    if re.search(r"\beintritt\s*frei\b|kostenlos", text, re.IGNORECASE):
        fields["is_free"] = True
    if re.search(r"\bopen\s*air\b|\bdraußen\b|\boutdoor\b", text, re.IGNORECASE):
        fields["is_outdoor"] = True

    pm = _PRICE_RE.search(text)
    if pm:
        price_raw = pm.group(1).replace(".", "").replace(",", ".")
        try:
            price_val = float(price_raw)
            fields["price"] = f"{price_val:.2f}"
            fields["price_info"] = pm.group(0).strip()
        except Exception:
            pass
    if not fields["price_info"]:
        if re.search(r"\bkostenlos\b|\beintritt\s*frei\b", text, re.IGNORECASE):
            if re.search(r"\bzzgl", text, re.IGNORECASE):
                fields["price_info"] = "kostenlos zzgl. Eintritt"
            else:
                fields["price_info"] = "kostenlos"

    am = re.search(r"\bab\s*(\d{1,2})\s*j", text, re.IGNORECASE)
    if am: fields["age_group"] = f"ab {am.group(1)} Jahren"

    if re.search(r"puppentheater|theater|bühne", text, re.IGNORECASE):
        fields["category"] = "Theater"

    email_match = EMAIL_RE.search(text)
    if email_match:
        fields["contact"] = email_match.group(0)

    opening_text = extract_opening_hours(lines)
    if opening_text:
        fields["opening_hours"] = opening_text

    if re.search(r"(keine|ohne)\s+anmeldung|anmeldung\s+nicht\s+erforderlich", text, re.IGNORECASE):
        fields["registration"] = "nein"
    elif re.search(r"\banmeldung\b", text, re.IGNORECASE) and re.search(r"erforderlich|pflicht|nötig|notwendig|bis|unter|per|bitte", text, re.IGNORECASE):
        fields["registration"] = "ja"

    categories = detect_categories(text)
    if categories:
        fields["categories"] = categories
        if not fields["category"]:
            fields["category"] = categories[0]

    fields["description"] = text
    fields["summary"] = _build_summary(text)  # <- NEU
    return fields

def confidence_stats(confs):
    if not confs: return {}
    return {"avg": round(sum(confs)/len(confs), 3)}
