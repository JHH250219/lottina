from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash, send_from_directory,
    abort, send_file
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, and_, or_
from flask_migrate import Migrate
from .models import db, Offer, Location, User, Category
from jinja2 import TemplateNotFound
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
import os, re, uuid
from pathlib import Path
import mimetypes
from werkzeug.utils import secure_filename
from .utils import (
    allowed,
    save_upload,
    extract_fields,
    confidence_stats,
    extract_addr_city_from_text,
)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
load_dotenv()
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Datenbank-Setup
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

# DB + Migrationssystem initialisieren
db.init_app(app)
migrate = Migrate(app, db)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
IMAGE_FOLDER = UPLOAD_FOLDER / "images"
PDF_FOLDER   = UPLOAD_FOLDER / "pdf"
for p in (IMAGE_FOLDER, PDF_FOLDER):
    p.mkdir(parents=True, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

MAX_TITLE_LEN    = 200
MAX_SUMMARY_LEN  = 400
MAX_SRC_NAME     = 120
MAX_MEETING_LEN  = 200
MAX_LOC_NAME     = 160
MAX_LOC_ADDR     = 160
MAX_CITY_LEN     = 120

# ---------------------------------------------------------------------------
# Globale Template-Variablen
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        "request_path": request.path,
        "current_user": current_user,
    }

# ---------------------------------------------------------------------------
# Healthcheck (für Sliplane)
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True}, 200

# ---------------------------------------------------------------------------
# Kernrouten
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    events = (
        db.session.query(Offer)
        .order_by(Offer.dt_start.asc().nulls_last(), Offer.id.desc())
        .limit(9)
        .all()
    )

   # helper für slug (ä->ae etc.)
    _AUML = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    def _slugify(s: str) -> str:
        import re
        s = (s or "").strip().lower()
        for a, b in _AUML.items():
            s = s.replace(a, b)
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s or "kategorie"

    # hole name + slug aus DB; fallback: slugify(name), falls slug NULL ist
    rows = (
        db.session.query(Category.name, Category.slug)
        .distinct()
        .order_by(Category.name.asc())
        .limit(40)
        .all()
    )
    categories = [{"label": name, "slug": (slug or _slugify(name))} for (name, slug) in rows]


    quick_filters = [
        {"label": "Heute", "href": url_for("suchergebnisse", date=datetime.now().strftime("%Y-%m-%d"))},
        {"label": "Kostenlos", "href": url_for("suchergebnisse", free=1)},
        {"label": "Outdoor", "href": url_for("suchergebnisse", outdoor=1)},
        {"label": "Immer offen", "href": url_for("suchergebnisse", always=1)},
    ]

    coords = []
    for ev in events:
        if ev.location and ev.location.lat is not None and ev.location.lon is not None:
            coords.append(
                {
                    "id": str(ev.id),
                    "title": ev.title or "Ohne Titel",
                    "lat": ev.location.lat,
                    "lon": ev.location.lon,
                    "date": ev.dt_start.isoformat() if ev.dt_start else "",
                    "url": url_for("event_detail", event_id=str(ev.id)),
                }
            )

    testimonials = [
        {
            "name": "Lena & Tom",
            "text": "Endlich alles an einem Ort – mega praktisch!",
            "img": "img/oma-paper-512.png",
        },
        {
            "name": "Mara",
            "text": "Hab so viele neue Kinderkurse entdeckt.",
            "img": "img/oma-cookies.png",
        },
        {
            "name": "Philipp",
            "text": "Die Karte ist Gold wert.",
            "img": "img/lottina_logo.png",
        },
    ]

    return render_template(
        "index.html",
        categories=categories,
        events=events,
        coords=coords,
        quick_filters=quick_filters,
        testimonials=testimonials,
    )

@app.template_filter("smartdate")
def smartdate(dt):
    if not dt:
        return ""
    return dt.astimezone().strftime("%a, %d.%m. %H:%M")

@app.template_filter("euro")
def euro(v):
    if v is None:
        return ""
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

@app.get("/results")
@app.get("/suchergebnisse", endpoint="suchergebnisse")
def results():
    q        = request.args.get("q", "").strip()
    date_str = request.args.get("date")
    cats     = request.args.getlist("cats[]")

    day = _parse_date(date_str)
    if day:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end   = day_start + timedelta(days=1)
    else:
        day_start = day_end = None

    qry = (
        db.session
        .query(Offer)
        .join(Location, Offer.location_id == Location.id, isouter=True)
    )

    # Freitext
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(
            Offer.title.ilike(like),
            Offer.description.ilike(like),
            Location.address.ilike(like),
            Location.city.ilike(like),
        ))

    # Datum
    if day_start and day_end:
        qry = qry.filter(
            and_(Offer.dt_start >= day_start, Offer.dt_start < day_end)
        )

    # Kategorien (relational)
    if cats:
        qry = qry.join(Offer.categories).filter(Category.name.in_(cats))

    # Flags
    if request.args.get("free") == "1":
        qry = qry.filter(Offer.is_free.is_(True))
    if request.args.get("outdoor") == "1":
        qry = qry.filter(Offer.is_outdoor.is_(True))
    if request.args.get("always") == "1":
        qry = qry.filter(
            Offer.dt_start.is_(None),
            Offer.dt_end.is_(None),
        )

    qry = qry.order_by(Offer.dt_start.asc().nulls_last(), Offer.id.desc())
    events = qry.limit(60).all()

    # Kategorienliste für Sidebar
    categories = [
        name for (name,) in (
            db.session.query(Category.name)
            .distinct()
            .order_by(Category.name.asc())
            .all()
        )
    ]

    # Koordinaten für die Map
    coords = []
    for ev in events:
        if ev.location and ev.location.lat is not None and ev.location.lon is not None:
            coords.append({
                "id":    str(ev.id),
                "title": ev.title or "Ohne Titel",
                "lat":   ev.location.lat,
                "lon":   ev.location.lon,
                "date":  ev.dt_start.isoformat() if ev.dt_start else "",
                "url":   url_for("event_detail", event_id=str(ev.id)),
            })

    return render_template(
        "results.html",
        events=events,
        coords=coords,
        categories=categories,
        date_filter=date_str or "",
    )

@app.get("/teaser")
def teaser_preview():
    return render_template("teaser.html")

@app.route("/sichtbar_werden")
def sichtbar_werden():
    return render_template("sichtbar_werden.html")

@app.get("/impressum")
def impressum():
    return render_template("impressum.html")

@app.get("/vorgaben")
def vorgaben():
    return render_template("vorgaben.html")

@app.route("/event/<uuid:event_id>")
def event_detail(event_id):
    event = Offer.query.get_or_404(event_id)
    return render_template("event.html", event=event)

@app.get("/ueber_uns")
def ueber_uns():
    team = [
        {
            "name": "Anne Sophie",
            "role": "Bildung & Elternperspektive, Veranstalter-Erfahrung",
            "bio": "Bringt die Perspektive von Familien und Veranstaltern ein – praxisnah und nutzerzentriert.",
        },
        {
            "name": "Jan",
            "role": "IT & Marketing, Digitalisierungs-Experte",
            "bio": "Verantwortet Produkt, Systeme und Skalierung – mit Fokus auf klare UX und offene Infrastruktur.",
        },
    ]
    values = [
        "Kostenlos für Familien & Vereine",
        "OCR: Poster & Flyer in Sekunden digital",
        "Filter: Alter, Indoor/Outdoor, kostenfrei, barrierefrei, Sprache, Radius",
        "Hosting ausschließlich in der EU · DSGVO-konform",
    ]
    return render_template("ueber_uns.html", team=team, values=values)

@app.post("/notify")
def notify():
    # Honeypot: echte Nutzer lassen 'website' leer
    honeypot = request.form.get("website", "").strip()
    if honeypot:
        return ("", 204)

    email   = (request.form.get("email") or "").strip().lower()
    consent = request.form.get("consent") == "on"

    if not email or not EMAIL_RX.match(email):
        return jsonify({"ok": False, "error": "invalid_email"}), 400
    if not consent:
        return jsonify({"ok": False, "error": "no_consent"}), 400

    app.logger.info(f"[notify] {email}")
    return jsonify({"ok": True})

@app.route("/preise", methods=["GET"], endpoint="preise")
def preise():
    return render_template("preise.html")

# ---------------------------------------------------------------------------
# Account / Auth
# ---------------------------------------------------------------------------
login_manager = LoginManager(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Willkommen zurück!", "success")
            return redirect(url_for("dashboard"))
        flash("Ungültige Anmeldedaten.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Abgemeldet.", "success")
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("Bitte alle Felder ausfüllen.", "danger")
            return render_template("register.html"), 400
        if len(password) < 8:
            flash("Passwort muss mindestens 8 Zeichen haben.", "danger")
            return render_template("register.html"), 400

        try:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("Konto angelegt. Du kannst dich jetzt einloggen.", "success")
            return redirect(url_for("login"))
        except IntegrityError:
            db.session.rollback()
            flash("Benutzername oder E-Mail ist bereits vergeben.", "danger")
            return render_template("register.html"), 409

    return render_template("register.html")

@app.get("/profil")
def profil():
    return render_template("profil.html")

# ---------------------------------------------------------------------------
# Anbieter / Events erstellen
# ---------------------------------------------------------------------------
@app.get("/event-erstellen")
def event_erstellen():
    return render_template("event-erstellen.html")

@app.route("/uploads/images/<path:fname>")
def _serve_uploaded_image(fname):
    safe_name = secure_filename(fname)

    target_path = (IMAGE_FOLDER / fname).resolve()
    base_path   = IMAGE_FOLDER.resolve()
    try:
        if not str(target_path).startswith(str(base_path)):
            abort(404)
    except Exception:
        abort(404)

    if not target_path.exists() or not target_path.is_file():
        abort(404)

    mimetype, _ = mimetypes.guess_type(target_path.name)
    return send_file(str(target_path), mimetype=mimetype, as_attachment=False)

@app.post("/ocr/upload")
def ocr_upload():
    """
    Nimmt Bild oder PDF entgegen, führt OCR aus und gibt extrahierte Felder zurück.
    Erwartet Feldname 'file' im Multipart-Upload.
    """

    # Lazy imports, damit der Server in Production ohne libGL (OpenCV)
    # trotzdem starten kann und Sliplane den Container nicht sofort killt.
    import numpy as np
    import cv2
    from .utils.ocr import pdf_to_images, ocr_image  # zieht easyocr/cv2 erst jetzt rein

    file = request.files.get("file")
    if not file or not file.filename or not allowed(file.filename):
        return jsonify({"ok": False, "error": "Keine gültige Datei (Bild/PDF)."}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()

    # --- PDF Upload ---
    if ext == "pdf":
        saved = save_upload(file, PDF_FOLDER)
        try:
            pages = pdf_to_images(saved)
        except Exception:
            app.logger.exception("pdf_to_images failed")
            return jsonify({"ok": False, "error": "PDF konnte nicht gerendert werden."}), 400

        if not pages:
            return jsonify({"ok": False, "error": "PDF hatte keine renderbaren Seiten."}), 400

        texts = []
        confs_all = []
        for img_rgb in pages:
            text, confs, meta = ocr_image(img_rgb)
            if text:
                texts.append(text)
            confs_all.extend(confs)

        full_text = "\n".join(texts).strip()
        fields = extract_fields(full_text)

        found = [k for k, v in fields.items() if v not in (None, "")]
        missing = [k for k in ("title", "date", "location") if not fields.get(k)]

        return jsonify({
            "ok": True,
            "fields": fields,
            "found": found,
            "missing": missing,
            "confidence": confidence_stats(confs_all),
            "image_url": None,
        })

    # --- Bild Upload ---
    saved = save_upload(file, IMAGE_FOLDER)
    rel_url = f"/uploads/images/{saved.name}"

    try:
        # np.fromfile + cv2.imdecode ist pfad-sicherer bei Unicode etc.
        data = np.fromfile(str(saved), dtype=np.uint8)
        img_bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img_bgr is None:
            raise ValueError("imdecode returned None")
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    except Exception:
        app.logger.exception("Bild konnte nicht gelesen werden")
        return jsonify({"ok": False, "error": "Bild konnte nicht gelesen werden."}), 400

    text, confs, meta = ocr_image(img_rgb)

    fields = extract_fields(text)
    fields["image_url"] = rel_url

    found = [k for k, v in fields.items() if v not in (None, "")]
    missing = [k for k in ("title", "date", "location") if not fields.get(k)]

    return jsonify({
        "ok": True,
        "fields": fields,
        "found": found,
        "missing": missing,
        "confidence": confidence_stats(confs),
        "image_url": rel_url,
    })


@app.post("/event-erstellen")
def create_event():
    f  = request.form
    up = request.files.get("summary_file")

    def _to_bool(v):
        return True if v == "true" else False if v == "false" else None

    def _to_float(v):
        try:
            return float((v or "").replace(",", "."))
        except Exception:
            return None

    def shorten(s, n):
        import re as _re
        if not s:
            return None
        s = _re.sub(r"\s+", " ", s).strip()
        return s[:n]

    # Bild
    image_url = f.get("image_url") or None
    if up and up.filename:
        saved = save_upload(up, IMAGE_FOLDER)
        image_url = f"/uploads/images/{saved.name}"

    contact_email       = shorten((f.get("contact") or "").strip(), MAX_SRC_NAME)
    opening_hours_text  = shorten((f.get("opening_hours") or "").strip(), 260)
    price_info          = shorten((f.get("price_info") or "").strip(), 160)
    registration_raw    = (f.get("registration") or "").strip().lower()
    if registration_raw in ("ja", "yes", "true"):
        registration_display = "Ja"
    elif registration_raw in ("nein", "no", "false"):
        registration_display = "Nein"
    else:
        registration_display = registration_raw.title() if registration_raw else ""

    # Datum/Zeit
    date_s     = f.get("date")
    time_s     = (f.get("time") or "").strip()
    time_end_s = (f.get("time_end") or "").strip()
    dt_start   = None
    dt_end     = None
    if date_s:
        try:
            start_token = time_s if time_s else "09:00"
            dt_start = datetime.strptime(
                f"{date_s} {start_token}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            dt_start = None
        if time_end_s:
            try:
                dt_end_candidate = datetime.strptime(
                    f"{date_s} {time_end_s}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=timezone.utc)
                if dt_start and dt_end_candidate <= dt_start:
                    dt_end_candidate += timedelta(days=1)
                dt_end = dt_end_candidate
            except Exception:
                dt_end = None

    price   = _to_float(f.get("price"))
    is_free = _to_bool(f.get("is_free"))
    if is_free is None and price is not None:
        is_free = (price == 0.0)
    if is_free is None and price_info and "kostenlos" in price_info.lower():
        is_free = True

    ag_min = ag_max = None
    age_group = (f.get("age_group") or "").strip()
    m = re.search(r"\b(\d{1,2})\b", age_group)
    if m:
        ag_min = int(m.group(1))

    location_name_raw = (f.get("location") or "").strip()
    lat = _to_float(f.get("lat"))
    lon = _to_float(f.get("lon"))

    if not location_name_raw or len(location_name_raw) > MAX_LOC_NAME:
        addr_guess, city_guess = extract_addr_city_from_text(
            f.get("description") or ""
        )
        location_name_raw = addr_guess or location_name_raw or city_guess or ""

    loc_name = shorten(location_name_raw, MAX_LOC_NAME)
    loc_addr = shorten(location_name_raw, MAX_LOC_ADDR)

    city_guess = None
    if not city_guess:
        _, city_guess = extract_addr_city_from_text(f.get("description") or "")

    location = None
    if loc_name:
        location = Location.query.filter_by(name=loc_name).first()
        if not location:
            location = Location(
                name=loc_name,
                address=loc_addr,
                lat=lat,
                lon=lon,
                city=shorten(city_guess, MAX_CITY_LEN),
            )
            db.session.add(location)
            db.session.flush()

    external_id = uuid.uuid4().hex
    source      = "manual"
    source_url  = (f.get("source_url") or "").strip() or f"manual://admin/{external_id}"

    title = shorten((f.get("title") or "Ohne Titel"), MAX_TITLE_LEN)

    summary_parts = []
    base_summary  = shorten((f.get("summary") or ""), MAX_SUMMARY_LEN)
    if base_summary:
        summary_parts.append(base_summary)
    if price_info:
        summary_parts.append(f"Preis: {price_info}")
    if registration_display:
        summary_parts.append(f"Anmeldung: {registration_display}")
    if contact_email:
        summary_parts.append(f"Kontakt: {contact_email}")
    if opening_hours_text:
        summary_parts.append(f"Öffnungszeiten: {opening_hours_text}")
    summary = (
        shorten(" · ".join([part for part in summary_parts if part]), MAX_SUMMARY_LEN)
        if summary_parts else None
    )

    source_name_raw = (f.get("source_name") or "").strip()
    if not source_name_raw and contact_email:
        source_name_raw = contact_email
    source_name = shorten(source_name_raw or None, MAX_SRC_NAME)

    meeting_point = shorten((f.get("meeting_point") or None), MAX_MEETING_LEN)

    offer = Offer(
        title=title,
        description=f.get("description") or None,
        summary=summary,
        external_id=external_id,
        source=source,
        source_url=source_url,
        dt_start=dt_start,
        dt_end=dt_end,
        price_value=price,
        price_min=price,
        price_max=price,
        is_free=is_free if is_free is not None else False,
        image=image_url,
        maps_url=(f.get("maps_url") or None),
        meeting_point=meeting_point,
        is_outdoor=_to_bool(f.get("is_outdoor")) or False,
        opening_hours={"general": opening_hours_text} if opening_hours_text else None,
        target_age_min=ag_min,
        target_age_max=ag_max,
        source_name=source_name,
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
        location_id=location.id if location else None,
    )
    db.session.add(offer)

    cat_name = (f.get("category") or "").strip()
    if cat_name:
        _AUML = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

        def slugify(s: str) -> str:
            s = s.strip().lower()
            for a, b in _AUML.items():
                s = s.replace(a, b)
            s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
            return s or "kategorie"

        slug = slugify(cat_name)
        cat = Category.query.filter_by(slug=slug).first()
        if not cat:
            cat = Category(slug=slug, name=cat_name)
            db.session.add(cat)
            db.session.flush()
        offer.categories.append(cat)

    try:
        db.session.commit()
        flash("Event gespeichert.", "success")
        return redirect(url_for("suchergebnisse"))
    except IntegrityError:
        db.session.rollback()
        flash("Konnte Event nicht speichern (DB-Fehler).", "danger")
        return redirect(url_for("event_erstellen")), 400

# ---------------------------------------------------------------------------
# Fehlerseiten
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    try:
        return render_template("404.html"), 404
    except TemplateNotFound:
        return "Seite nicht gefunden", 404

@app.errorhandler(500)
def server_error(e):
    try:
        return render_template("500.html"), 500
    except TemplateNotFound:
        return "Interner Serverfehler", 500

# ---------------------------------------------------------------------------
# Lokaler Dev-Start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=True)
