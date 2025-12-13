from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash, send_from_directory,
    abort, send_file, session, Response
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, and_, or_, create_engine, case
from sqlalchemy.orm import selectinload
from flask_migrate import Migrate
import stripe
from .models import (
    db,
    Offer,
    OfferAvailability,
    Location,
    User,
    Category,
    OfferType,
    OfferStatus,
    SourceType,
    Organizer,
    OrganisationGroup,
    OrganisationInvitation,
    CommunityFeedback,
    UserChild,
    UserFavoriteOffer,
    organisation_users,
    ORGANISATION_ROLE_ADMIN,
)
from .permanent import sync_permanent_availability, opening_hours_text
from .sitemap import sitemap_bp
from .organisations import organisations_bp
from jinja2 import TemplateNotFound
from datetime import datetime, timedelta, timezone, date
from calendar import monthrange
from math import ceil
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from flask_mail import Mail, Message
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import json
import os, re, uuid
from pathlib import Path
import mimetypes
import click
from itertools import zip_longest
from typing import Any
from werkzeug.utils import secure_filename
from urllib.parse import urlencode
from .utils import (
    allowed,
    save_upload,
    extract_addr_city_from_text,
    extract_fields,
)
from .ocr_client import run_ocr

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
import logging
from dotenv import load_dotenv
import os
from pathlib import Path
from sqlalchemy import create_engine

# --- Environment laden ---
env = os.getenv("ENV", "local").strip()
env_file = f".env.{env}"
if Path(env_file).exists():
    load_dotenv(env_file, override=True)
    print(f"🔧 Lottina: Environment '{env}' geladen aus {env_file}")
else:
    load_dotenv(override=True)
    print(f"⚠️  Lottina: Kein .env.{env} gefunden – nutze Standard .env")

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Flask App Grundkonfiguration
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)
app.config["SKIP_OFFER_REVIEW"] = _env_flag("SKIP_OFFER_REVIEW", default=(env.lower() == "local"))

EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_REPLACEMENTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
def slugify_value(value: str | None, *, fallback: str = "organisation") -> str:
    value = (value or "").strip().lower()
    for src, dst in _SLUG_REPLACEMENTS.items():
        value = value.replace(src, dst)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or fallback

# ---------------------------------------------------------------------------
# Datenbank-Setup mit Fallback
# ---------------------------------------------------------------------------
PRIMARY_DB_URI = os.getenv("DATABASE_URL")
FALLBACK_DB_URI = os.getenv("FALLBACK_DATABASE_URL")

# Lokaler Fallback, falls kein explizites Fallback gesetzt
if not FALLBACK_DB_URI:
    FALLBACK_DB_URI = f"sqlite:///{Path(__file__).resolve().parent / 'lottina_local.sqlite3'}"

resolved_db_uri = PRIMARY_DB_URI or FALLBACK_DB_URI
fallback_in_use = False

if PRIMARY_DB_URI:
    try:
        with create_engine(PRIMARY_DB_URI, future=True).connect():
            print(f"✅ Verbunden mit PRIMARY DB: {PRIMARY_DB_URI}")
    except Exception as exc:
        app.logger.warning(
            "⚠️  Primäre Datenbank nicht erreichbar (%s). Fallback auf SQLite: %s", exc, FALLBACK_DB_URI
        )
        resolved_db_uri = FALLBACK_DB_URI
        fallback_in_use = True
else:
    print("ℹ️  Keine PRIMARY_DB_URI definiert – verwende SQLite.")
    fallback_in_use = True

app.config["SQLALCHEMY_DATABASE_URI"] = resolved_db_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ---------------------------------------------------------------------------
# App Secrets & Integrationen
# ---------------------------------------------------------------------------
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

# Mail-Konfiguration
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "localhost")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 25))
app.config["MAIL_USE_TLS"] = bool(int(os.getenv("MAIL_USE_TLS", "1")))
app.config["MAIL_USE_SSL"] = bool(int(os.getenv("MAIL_USE_SSL", "0")))
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

# Stripe-Konfiguration
app.config["STRIPE_PRICE_MONTHLY"] = os.getenv(
    "STRIPE_PRICE_MONTHLY", "price_1SWFfnRx44l32FoJcn2FrXst"
)
app.config["STRIPE_PRICE_YEARLY"] = os.getenv(
    "STRIPE_PRICE_YEARLY", "price_1SXn4dRx44l32FoJBE3K15gw"
)
app.config["STRIPE_WEBHOOK_SECRET"] = os.getenv("STRIPE_WEBHOOK_SECRET")

# Feedback-Mails
app.config["FEEDBACK_ALERT_RECIPIENT"] = os.getenv(
    "FEEDBACK_ALERT_RECIPIENT", "hello@lottina.de"
)

# ---------------------------------------------------------------------------
# Erweiterungen initialisieren
# ---------------------------------------------------------------------------
db.init_app(app)
migrate = Migrate(app, db)
mail = Mail(app)

# Preise (Default-Werte, falls .env leer)
MONTHLY_PRICE_EUR = Decimal(os.getenv("MEMBERSHIP_PRICE_MONTHLY_EUR", "1.99"))
YEARLY_PRICE_EUR = Decimal(os.getenv("MEMBERSHIP_PRICE_YEARLY_EUR", "19.99"))

# Blueprints
app.register_blueprint(sitemap_bp)
app.register_blueprint(organisations_bp)

# Log-Ausgabe zum Überblick
print(f"📦 Aktive Datenbank: {resolved_db_uri}")
if fallback_in_use:
    print("⚠️  Achtung: Fallback-Datenbank (SQLite) aktiv!")


FEEDBACK_KIND_FEEDBACK = "feedback"
FEEDBACK_KIND_CITY = "city_request"
FEEDBACK_KIND_LABELS = {
    FEEDBACK_KIND_FEEDBACK: "Feedback",
    FEEDBACK_KIND_CITY: "Wunschstadt / Aktivität",
}

RECURRENCE_GENERATION_DAYS = 90



def send_templated_email(subject, template, recipients, **context):
    if not recipients or not app.config.get("MAIL_SERVER"):
        app.logger.exception("E-Mail Versand übersprungen: Keine Empfänger oder kein Mail-Server konfiguriert.")
        return
    try:
        msg = Message(subject=subject, recipients=recipients)
        try:
            msg.body = render_template(f"emails/{template}.txt", **context)
        except TemplateNotFound:
            msg.body = ""
        try:
            msg.html = render_template(f"emails/{template}.html", **context)
        except TemplateNotFound:
            msg.html = msg.body
        mail.send(msg)
    except Exception:
        app.logger.exception("E-Mail Versand fehlgeschlagen: %s", subject)


def send_welcome_email(user):
    if not user or not user.email:
        return
    send_templated_email(
        subject="Willkommen bei lottina 🌿",
        template="welcome",
        recipients=[user.email],
        user=user,
    )


def send_membership_email(user):
    if not user or not user.email:
        return
    send_templated_email(
        subject="Dein lottina Family Abo ist aktiv 💚",
        template="membership",
        recipients=[user.email],
        user=user,
    )


def send_feedback_notification(entry):
    recipient = app.config.get("FEEDBACK_ALERT_RECIPIENT")
    if not recipient or not entry:
        return
    label = FEEDBACK_KIND_LABELS.get(entry.kind, "Feedback")
    send_templated_email(
        subject=f"Neues Community-Feedback ({label})",
        template="feedback_notification",
        recipients=[recipient],
        entry=entry,
        category_label=label,
    )


def create_feedback_entry(kind, email, city, message, source):
    normalized_kind = kind or FEEDBACK_KIND_FEEDBACK
    entry = CommunityFeedback(
        kind=normalized_kind,
        email=email or None,
        city=city or None,
        message=message or None,
        source=source or None,
    )
    db.session.add(entry)
    db.session.commit()
    send_feedback_notification(entry)
    return entry


if fallback_in_use and resolved_db_uri.startswith("sqlite"):
    with app.app_context():
        db.create_all()


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
IMAGE_FOLDER = UPLOAD_FOLDER / "images"
PROFILE_IMAGE_FOLDER = UPLOAD_FOLDER / "profile"
IMAGE_FOLDER.mkdir(parents=True, exist_ok=True)
ORG_LOGO_FOLDER = IMAGE_FOLDER / "org"
ORG_LOGO_FOLDER.mkdir(parents=True, exist_ok=True)
PROFILE_IMAGE_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

@app.route("/robots.txt")
def robots():
    return Response(
        "User-agent: *\nAllow: /\n\nSitemap: https://lottina.de/sitemap.xml",
        mimetype="text/plain",
    )

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

MODE_MANUAL = "manual"
MODE_OCR = "ocr"
OCR_FORM_FIELDS = [
    "title",
    "description",
    "date",
    "time",
    "time_end",
    "location",
    "category",
    "age_group",
    "contact",
    "opening_hours",
    "price_info",
    "registration",
    "maps_url",
]
OCR_FIELD_LABELS = {
    "title": "Titel",
    "description": "Beschreibung",
    "date": "Datum",
    "time": "Startzeit",
    "time_end": "Endzeit",
    "location": "Ort",
    "category": "Kategorie",
    "age_group": "Altersgruppe",
    "contact": "Kontakt",
    "opening_hours": "Öffnungszeiten",
    "price_info": "Preis / Hinweis",
    "registration": "Anmeldung",
    "maps_url": "Maps-URL",
}
ORGANISATION_TYPE_CHOICES = [
    ("verein", "Verein"),
    ("kirche", "Kirche"),
    ("kommune", "Kommune / Verwaltung"),
    ("öffentliche Einrichtung", "öffentliche Einrichtung"),
    ("ehrenamt", "Ehrenamt / Initiative"),
    ("sonstiges", "Sonstiges"),
    ("unternehmen", "Unternehmen"),
]

def _format_datetime_input(value: datetime | None) -> str:
    if not value:
        return ""
    try:
        localized = value.astimezone(timezone.utc)
    except Exception:
        localized = value
    return localized.strftime("%Y-%m-%dT%H:%M")

def _unique_slug_for(model, base_value: str, *, fallback: str = "organisation", attr: str = "slug") -> str:
    slug = slugify_value(base_value, fallback=fallback)
    if not slug:
        slug = fallback
    existing = (
        db.session.query(getattr(model, attr))
        .filter(getattr(model, attr) == slug)
        .first()
    )
    if not existing:
        return slug
    counter = 2
    while True:
        candidate = f"{slug}-{counter}"
        exists = (
            db.session.query(getattr(model, attr))
            .filter(getattr(model, attr) == candidate)
            .first()
        )
        if not exists:
            return candidate
        counter += 1

@app.route("/organisation-onboarding", methods=["GET", "POST"])
def organisation_onboarding():
    default_contact = current_user.email if getattr(current_user, "is_authenticated", False) else ""
    form_defaults = {
        "organisation_type": "",
        "name": "",
        "description": "",
        "address": "",
        "website": "",
        "contact_email": default_contact,
        "logo_url": "",
    }
    form_data = dict(form_defaults)
    group_entries: list[dict[str, str]] = []
    invite_entries: list[str] = []
    submission_errors: list[str] = []
    wizard_param = (request.args.get("wizard") or "").strip().lower()
    open_wizard = wizard_param in {"1", "true", "yes", "open"}

    if request.method == "POST":
        open_wizard = True
        for key in form_defaults:
            if key == "logo_url":
                continue
            form_data[key] = (request.form.get(key) or "").strip()
        form_data.setdefault("contact_email", default_contact)

        saved_logo_path = None
        logo_file = request.files.get("logo_file")
        if logo_file and logo_file.filename:
            if not allowed(logo_file.filename):
                submission_errors.append("Unterstützt werden nur PNG, JPG, JPEG oder WEBP für Logos.")
            else:
                saved_logo_path = save_upload(logo_file, ORG_LOGO_FOLDER)
                relative_logo = saved_logo_path.relative_to(IMAGE_FOLDER)
                form_data["logo_url"] = f"/uploads/images/{relative_logo.as_posix()}"

        names = request.form.getlist("group_names[]")
        descriptions = request.form.getlist("group_descriptions[]")
        for name, desc in zip_longest(names, descriptions, fillvalue=""):
            clean_name = (name or "").strip()
            clean_desc = (desc or "").strip()
            if clean_name:
                group_entries.append({"name": clean_name, "description": clean_desc})

        invite_candidates = [(email or "").strip().lower() for email in request.form.getlist("invite_emails[]")]
        seen_invites: set[str] = set()
        for email in invite_candidates:
            if not email or email in seen_invites:
                continue
            invite_entries.append(email)
            seen_invites.add(email)

        if not form_data["organisation_type"]:
            submission_errors.append("Bitte wähle einen Organisationstyp aus.")
        if not form_data["name"]:
            submission_errors.append("Der Name der Organisation ist erforderlich.")
        if not form_data["description"]:
            submission_errors.append("Bitte beschreibe deine Organisation kurz.")
        if not group_entries:
            submission_errors.append("Lege mindestens eine Gruppe an.")
        if not form_data["contact_email"] and not getattr(current_user, "is_authenticated", False):
            submission_errors.append("Bitte gib eine Kontakt-E-Mail an, damit wir dich kontaktieren können.")

        if not getattr(current_user, "is_authenticated", False):
            contact_invite = (form_data["contact_email"] or "").strip().lower()
            if contact_invite and contact_invite not in invite_entries:
                invite_entries.append(contact_invite)

        invalid_invites = [email for email in invite_entries if not EMAIL_RX.match(email)]
        if invalid_invites:
            submission_errors.append(f"Diese Einladungen sehen nicht korrekt aus: {', '.join(invalid_invites)}")

        if submission_errors:
            if saved_logo_path:
                try:
                    saved_logo_path.unlink()
                except Exception:
                    pass
            return (
                render_template(
                    "organisation-onboarding.html",
                    form_data=form_data,
                    group_entries=group_entries,
                    invite_entries=invite_entries,
                    submission_errors=submission_errors,
                    open_wizard=open_wizard,
                    organisation_type_choices=ORGANISATION_TYPE_CHOICES,
                ),
                400,
            )

        try:
            slug = _unique_slug_for(Organizer, form_data["name"], fallback="organisation")
            organisation_contact_email = form_data["contact_email"] or default_contact or None
            organisation = Organizer(
                slug=slug,
                name=form_data["name"],
                organisation_type=form_data["organisation_type"],
                description=form_data["description"],
                address=form_data["address"] or None,
                website=form_data["website"] or None,
                email=organisation_contact_email,
                logo=form_data.get("logo_url") or None,
            )
            db.session.add(organisation)
            db.session.flush()

            if getattr(current_user, "is_authenticated", False):
                db.session.execute(
                    organisation_users.insert().values(
                        organisation_id=organisation.id,
                        user_id=current_user.id,
                        role=ORGANISATION_ROLE_ADMIN,
                    )
                )

            used_group_slugs: set[str] = set()
            for entry in group_entries:
                base_slug = slugify_value(entry["name"], fallback="gruppe")
                candidate = base_slug
                counter = 2
                while candidate in used_group_slugs:
                    candidate = f"{base_slug}-{counter}"
                    counter += 1
                used_group_slugs.add(candidate)
                db.session.add(
                    OrganisationGroup(
                        organisation_id=organisation.id,
                        name=entry["name"],
                        slug=candidate,
                        description=entry.get("description") or None,
                    )
                )

            for email in invite_entries:
                db.session.add(
                    OrganisationInvitation(
                        organisation_id=organisation.id,
                        email=email,
                        role=ORGANISATION_ROLE_ADMIN,
                        invited_by=current_user if getattr(current_user, "is_authenticated", False) else None,
                    )
                )

            db.session.commit()
            if getattr(current_user, "is_authenticated", False):
                return redirect(url_for("organisations.organisation_dashboard", slug=organisation.slug))
            flash("Organisation angelegt! Lege jetzt dein Nutzerkonto an oder melde dich an, um das Dashboard zu nutzen.", "success")
            return redirect(url_for("register"))
        except IntegrityError:
            db.session.rollback()
            submission_errors.append("Die Organisation konnte nicht gespeichert werden. Bitte versuche es erneut.")
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            app.logger.exception("Organisation onboarding failed: %s", exc)
            submission_errors.append("Unbekannter Fehler beim Speichern. Bitte versuche es erneut.")

        return (
            render_template(
                "organisation-onboarding.html",
                form_data=form_data,
                group_entries=group_entries,
                invite_entries=invite_entries,
                submission_errors=submission_errors,
                open_wizard=open_wizard,
                organisation_type_choices=ORGANISATION_TYPE_CHOICES,
            ),
            500,
        )

    return render_template(
        "organisation-onboarding.html",
        form_data=form_data,
        group_entries=group_entries,
        invite_entries=invite_entries,
        submission_errors=submission_errors,
        open_wizard=open_wizard,
        organisation_type_choices=ORGANISATION_TYPE_CHOICES,
    )

MARKER_COLOR_SEQUENCE = ["blue", "orange", "violet", "red", "gold", "grey", "black"]
MARKER_COLOR_STYLES = {
    "green": {"hex": "#059669"},
    "blue": {"hex": "#2563eb"},
    "orange": {"hex": "#f97316"},
    "violet": {"hex": "#7c3aed"},
    "red": {"hex": "#dc2626"},
    "gold": {"hex": "#fbbf24"},
    "grey": {"hex": "#6b7280"},
    "black": {"hex": "#111827"},
}
DEFAULT_MARKER_COLOR = "violet"

def _has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _skip_offer_review() -> bool:
    return bool(app.config.get("SKIP_OFFER_REVIEW"))


def _filter_visible_offers(query):
    if _skip_offer_review():
        return query
    return query.filter(Offer.status == OfferStatus.published)


# Stripe config
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def _plan_key_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    price_id = price_id.strip()
    if price_id == (app.config.get("STRIPE_PRICE_MONTHLY") or "").strip():
        return "monthly"
    if price_id and price_id == (app.config.get("STRIPE_PRICE_YEARLY") or "").strip():
        return "yearly"
    return None


def _resolve_user_from_metadata(metadata: dict | None, fallback_email: str | None = None) -> User | None:
    metadata = metadata or {}
    user_id = metadata.get("user_id") or metadata.get("userId")
    user = None
    if user_id:
        try:
            user = User.query.get(int(user_id))
        except (TypeError, ValueError):
            app.logger.warning("Ungültige user_id in Stripe-Metadaten: %s", user_id)
    if not user and fallback_email:
        fallback_email = fallback_email.strip().lower()
        if fallback_email:
            user = User.query.filter(func.lower(User.email) == fallback_email).first()
    return user


def _update_membership_until(user: User, period_end_timestamp: int | None) -> bool:
    if not user or not period_end_timestamp:
        return False
    period_end = datetime.fromtimestamp(period_end_timestamp, tz=timezone.utc)
    current_until = user.premium_until
    changed = False
    # Nur anpassen, wenn wir ein späteres Ende erhalten oder noch nichts gesetzt haben
    if not current_until or period_end > current_until:
        user.premium_until = period_end
        changed = True
    new_is_premium = bool(user.premium_until and user.premium_until > datetime.now(timezone.utc))
    if user.is_premium != new_is_premium:
        user.is_premium = new_is_premium
        changed = True
    db.session.add(user)
    return changed


def _extract_period_end_from_invoice(invoice: dict) -> int | None:
    lines = (invoice or {}).get("lines", {}).get("data", [])
    if not lines:
        return None
    period = lines[0].get("period") or {}
    return period.get("end")


def _handle_checkout_completed(session_data: dict) -> bool:
    metadata = session_data.get("metadata") or {}
    email = (
        (session_data.get("customer_details") or {}).get("email")
        or session_data.get("customer_email")
    )
    user = _resolve_user_from_metadata(metadata, email)
    if not user:
        app.logger.warning("Stripe Checkout ohne zuordenbaren User (Session %s)", session_data.get("id"))
        return False

    period_end_ts = None
    subscription_id = session_data.get("subscription")
    if subscription_id:
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            subscription_meta = subscription.get("metadata") or {}
            if not metadata.get("user_id") and subscription_meta.get("user_id"):
                metadata["user_id"] = subscription_meta["user_id"]
            period_end_ts = subscription.get("current_period_end")
        except Exception:  # noqa: BLE001
            app.logger.exception("Stripe Subscription %s konnte nicht geladen werden", subscription_id)

    if not period_end_ts:
        period_end_ts = _extract_period_end_from_invoice(session_data)

    updated = _update_membership_until(user, period_end_ts)
    if updated:
        db.session.commit()
        app.logger.info("Premium bis %s für User %s gesetzt (Checkout)", user.premium_until, user.id)
    return updated


def _handle_invoice_paid(invoice: dict) -> bool:
    metadata = invoice.get("metadata") or {}
    email = invoice.get("customer_email")
    user = _resolve_user_from_metadata(metadata, email)

    subscription_id = invoice.get("subscription")
    subscription_meta = {}
    if subscription_id:
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            subscription_meta = subscription.get("metadata") or {}
            if not metadata:
                metadata = subscription_meta
            if not user:
                user = _resolve_user_from_metadata(subscription_meta, email)
        except Exception:  # noqa: BLE001
            app.logger.exception("Stripe Subscription %s konnte nicht geladen werden", subscription_id)

    if not user and email:
        user = _resolve_user_from_metadata({}, email)

    if not user:
        app.logger.warning("Stripe Invoice ohne zuordenbaren User (Invoice %s)", invoice.get("id"))
        return False

    period_end_ts = _extract_period_end_from_invoice(invoice)
    if not period_end_ts and subscription_id:
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            period_end_ts = subscription.get("current_period_end")
        except Exception:  # noqa: BLE001
            app.logger.exception("Subscription %s konnte nicht geladen werden, um period_end zu bestimmen", subscription_id)

    updated = _update_membership_until(user, period_end_ts)
    if updated:
        db.session.commit()
        app.logger.info(
            "Premium bis %s für User %s gesetzt (Invoice %s)",
            user.premium_until,
            user.id,
            invoice.get("id"),
        )
    return updated

# ---------------------------------------------------------------------------
# Globale Template-Variablen
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        "request_path": request.path,
        "current_user": current_user,
        "FEEDBACK_KIND_CITY": FEEDBACK_KIND_CITY,
        "FEEDBACK_KIND_FEEDBACK": FEEDBACK_KIND_FEEDBACK,
    }

@app.context_processor
def inject_seo_defaults():
    view_args = request.view_args or {}
    canonical = request.base_url
    try:
        canonical = url_for(request.endpoint, _external=True, **view_args)
    except Exception:
        pass
    return {"default_canonical": canonical}

# ---------------------------------------------------------------------------
# Healthcheck (für Sliplane)
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True}, 200

@app.get("/_debug/db")
def _debug_db():
    try:
        db.session.execute(db.text("SELECT 1"))
        return {"db": "ok"}, 200
    except Exception as exc:
        app.logger.exception("DB check failed")
        return {"db": "fail", "error": str(exc)}, 500

# ---------------------------------------------------------------------------
# Kernrouten
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    try:
        events_query = db.session.query(Offer)
        events_query = _filter_visible_offers(events_query)
        events = (
            events_query
            .order_by(Offer.dt_start.asc().nulls_last(), Offer.id.desc())
            .limit(9)
            .all()
        )

        rows = (
            db.session.query(Category.name, Category.slug)
            .distinct()
            .order_by(Category.name.asc())
            .limit(40)
            .all()
        )

        _AUML = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

        def _slugify(s: str) -> str:
            import re

            s = (s or "").strip().lower()
            for a, b in _AUML.items():
                s = s.replace(a, b)
            return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "kategorie"

        categories = [{"label": name, "slug": (slug or _slugify(name))} for (name, slug) in rows]

        curated_manual_qry = (
            db.session.query(func.count(Offer.id))
            .filter(Offer.source == "manual")
        )
        curated_manual_qry = _filter_visible_offers(curated_manual_qry)
        curated_manual = curated_manual_qry.scalar() or 0
    except (OperationalError, ProgrammingError):
        app.logger.exception("DB not ready, rendering fallback.")
        events = []
        categories = [
            {"label": "Outdoor", "slug": "outdoor"},
            {"label": "Museen", "slug": "museen"},
            {"label": "Kostenlos", "slug": "free"},
            {"label": "Heute", "slug": "today"},
        ]
        curated_manual = 0

    quick_filters = [
        {"label": "Heute", "href": url_for("suchergebnisse", date=datetime.now().strftime("%Y-%m-%d"))},
        {"label": "Kostenlos", "href": url_for("suchergebnisse", free=1)},
        {"label": "Outdoor", "href": url_for("suchergebnisse", outdoor=1)},
        {"label": "Ständiges Angebot", "href": url_for("suchergebnisse", always=1)},
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
        curated_manual=curated_manual,
    )

WEEKDAY_ABBR_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _format_weekday(dt):
    localized = dt.astimezone()
    day_name = WEEKDAY_ABBR_DE[localized.weekday()]
    return localized, day_name


@app.template_filter("smartdate")
def smartdate(dt):
    if not dt:
        return ""
    localized, day_name = _format_weekday(dt)
    return f"{day_name}, {localized.strftime('%d.%m. %H:%M')}"


@app.template_filter("shortdate")
def shortdate(dt):
    if not dt:
        return ""
    localized, day_name = _format_weekday(dt)
    return f"{day_name}, {localized.strftime('%d.%m.')}"

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
    page     = request.args.get("page", type=int) or 1
    if page < 1:
        page = 1
    per_page = 50
    chunk_span = 2
    chunk_size = per_page * chunk_span
    def _parse_age_param(name):
        value = request.args.get(name)
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    age_min = _parse_age_param("age_min")
    age_max = _parse_age_param("age_max")

    show_always = request.args.get("always") == "1"

    day = _parse_date(date_str)
    if day:
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end   = day_start + timedelta(days=1)
    else:
        day_start = day_end = None
    today_cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    is_premium_user = current_user.is_authenticated and getattr(current_user, "premium_active", False)
    qry = (
        db.session
        .query(Offer)
        .join(Location, Offer.location_id == Location.id, isouter=True)
    )
    qry = _filter_visible_offers(qry)

    playground_clause = or_(
        func.lower(Category.slug) == "spielplatz",
        func.lower(Category.name) == "spielplatz",
        func.lower(Category.slug) == "playground",
        func.lower(Category.name) == "playground",
    )
    qry = qry.filter(~Offer.categories.any(playground_clause))

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
        target_date = day_start.date()
        filters = [
            and_(Offer.dt_start >= day_start, Offer.dt_start < day_end),
        ]
        if show_always:
            permanent_subq = (
                db.session.query(OfferAvailability.offer_id)
                .filter(OfferAvailability.day == target_date)
            )
            filters.append(Offer.id.in_(permanent_subq))
        qry = qry.filter(
            or_(*filters)
        )
    else:
        qry = qry.filter(or_(Offer.dt_start.is_(None), Offer.dt_start >= today_cutoff))

    # Kategorien (relational)
    if cats:
        qry = qry.join(Offer.categories).filter(Category.name.in_(cats))

    if age_min is not None:
        qry = qry.filter(or_(Offer.target_age_max.is_(None), Offer.target_age_max >= age_min))
    if age_max is not None:
        qry = qry.filter(or_(Offer.target_age_min.is_(None), Offer.target_age_min <= age_max))

    permanent_count_qry = qry.filter(Offer.type == OfferType.permanent)

    # Permanent filter
    if show_always:
        qry = permanent_count_qry
    else:
        qry = qry.filter(or_(Offer.type.is_(None), Offer.type != OfferType.permanent))

    # Flags
    if request.args.get("free") == "1":
        qry = qry.filter(Offer.is_free.is_(True))
        permanent_count_qry = permanent_count_qry.filter(Offer.is_free.is_(True))
    if request.args.get("outdoor") == "1":
        qry = qry.filter(Offer.is_outdoor.is_(True))
        permanent_count_qry = permanent_count_qry.filter(Offer.is_outdoor.is_(True))

    free_teaser_events: list[Offer] = []
    if not is_premium_user:
        teaser_query = qry.filter(Offer.is_free.is_(True))
        free_teaser_events = (
            teaser_query.order_by(Offer.dt_start.asc().nulls_last(), Offer.id.desc())
            .limit(3)
            .all()
        )
        qry = qry.filter(or_(Offer.is_free.is_(False), Offer.is_free.is_(None)))
        permanent_count_qry = permanent_count_qry.filter(or_(Offer.is_free.is_(False), Offer.is_free.is_(None)))

    filtered_qry = qry
    stats_row = filtered_qry.with_entities(
        func.count(Offer.id).label("total"),
        func.coalesce(func.sum(case((Offer.is_free.is_(True), 1), else_=0)), 0).label("free"),
        func.coalesce(func.sum(case((Offer.is_outdoor.is_(True), 1), else_=0)), 0).label("outdoor"),
    ).first()
    total_results = stats_row.total or 0
    free_results = int(stats_row.free or 0)
    outdoor_results = int(stats_row.outdoor or 0)
    permanent_count = permanent_count_qry.with_entities(func.count(Offer.id)).scalar() or 0
    total_pages = max(1, ceil(total_results / per_page)) if total_results else 1
    if page > total_pages:
        page = total_pages
    chunk_offset = (page - 1) * per_page if total_results else 0

    qry = qry.order_by(Offer.dt_start.asc().nulls_last(), Offer.id.desc())
    events_chunk = qry.offset(chunk_offset).limit(chunk_size).all()
    events = events_chunk[:per_page]
    prefetched_events = events_chunk[per_page:]
    next_prefetch_page = page + 1 if prefetched_events and page < total_pages else None

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
    playground_ids = set()
    permanent_ids = set()
    for ev in events:
        offer_type_value = getattr(ev.type, "value", ev.type)
        is_permanent = offer_type_value == getattr(OfferType.permanent, "value", "permanent")
        if is_permanent:
            permanent_ids.add(str(ev.id))

        has_playground_category = any(
            ((c.slug or "").lower() == "playground") or ((c.name or "").lower() == "spielplatz")
            for c in (ev.categories or [])
        )
        if has_playground_category:
            playground_ids.add(str(ev.id))

        if ev.location and ev.location.lat is not None and ev.location.lon is not None:
            coords.append({
                "id":    str(ev.id),
                "title": ev.title or "Ohne Titel",
                "lat":   ev.location.lat,
                "lon":   ev.location.lon,
                "date":  ev.dt_start.isoformat() if ev.dt_start else "",
                "url":   url_for("event_detail", event_id=str(ev.id)),
                "address": ev.location.address or ev.location.city or ev.location.name or "",
                "location_name": ev.location.name or "",
                "is_playground": has_playground_category,
                "is_permanent": is_permanent,
                "source": ev.source,
                "offer_type": offer_type_value,
            })
    hero_stats = [
        {"label": "Ergebnisse gesamt", "value": total_results},
        {"label": "davon kostenlos", "value": free_results},
        {"label": "Outdoor Treffer", "value": outdoor_results},
        {"label": "Ständiges Angebot", "value": permanent_count},
    ]

    favorite_offer_ids = set()
    if current_user.is_authenticated and events:
        user_favorites = (
            db.session.query(UserFavoriteOffer.offer_id)
            .filter(UserFavoriteOffer.user_id == current_user.id)
            .all()
        )
        favorite_offer_ids = {str(row.offer_id) for row in user_favorites}

    redirect_params = request.args.to_dict(flat=False)
    redirect_params.pop("partial", None)
    redirect_query = urlencode(redirect_params, doseq=True)
    redirect_path = request.path
    base_page_params = {key: list(value) for key, value in redirect_params.items()}

    def _page_url(target_page: int) -> str:
        params = {key: list(value) for key, value in base_page_params.items()}
        if target_page <= 1:
            params.pop("page", None)
        else:
            params["page"] = [str(target_page)]
        query = urlencode(params, doseq=True)
        return f"{request.path}{'?' + query if query else ''}"

    prev_page_url = _page_url(page - 1) if page > 1 else None
    next_page_url = _page_url(page + 1) if page < total_pages else None

    if request.args.get("partial") == "1":
        return jsonify(
            {
                "page": page,
                "total_pages": total_pages,
                "page_html": render_template("results/_event_cards.html", events=events, redirect_query=redirect_query, redirect_path=redirect_path),
                "prefetch_html": render_template("results/_event_cards.html", events=prefetched_events, redirect_query=redirect_query, redirect_path=redirect_path),
                "prefetch_page": next_prefetch_page if next_prefetch_page and next_prefetch_page <= total_pages else None,
            }
        )

    return render_template(
        "results.html",
        events=events,
        prefetched_events=prefetched_events,
        coords=coords,
        categories=categories,
        date_filter=date_str or "",
        playground_ids=playground_ids,
        permanent_ids=permanent_ids,
        favorite_offer_ids=favorite_offer_ids,
        hero_stats=hero_stats,
        total_results=total_results,
        page=page,
        total_pages=total_pages,
        prefetch_page=next_prefetch_page,
        redirect_query=redirect_query,
        redirect_path=redirect_path,
        prev_page_url=prev_page_url,
        next_page_url=next_page_url,
        free_teaser_events=free_teaser_events,
        is_premium_user=is_premium_user,
    )


@app.get("/karte")
def karte():
    # Locations mit Koordinaten laden
    locations = (
        db.session.query(Location)
        .filter(
            Location.lat.isnot(None),
            Location.lon.isnot(None),
        )
        .options(
            selectinload(Location.offers).selectinload(Offer.categories)
        )
        .order_by(Location.created_at.desc().nullslast(), Location.id.desc())
        .all()
    )

    # Kategorien für Filter sammeln
    base_categories = (
        db.session.query(Category.slug, Category.name)
        .distinct()
        .order_by(Category.name.asc())
        .all()
    )

    _AUML = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

    def _slugify(value: str | None) -> str:
        if not value:
            return "kategorie"
        s = value.strip().lower()
        for a, b in _AUML.items():
            s = s.replace(a, b)
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s or "kategorie"

    marker_color_map = {"spielplatz": "green", "playground": "green"}
    color_index = 0

    def assign_marker_color(slug: str | None) -> str:
        nonlocal color_index
        normalized = (slug or "").strip().lower()
        if not normalized:
            return DEFAULT_MARKER_COLOR
        if normalized in marker_color_map:
            return marker_color_map[normalized]
        color_key = MARKER_COLOR_SEQUENCE[color_index % len(MARKER_COLOR_SEQUENCE)]
        color_index += 1
        marker_color_map[normalized] = color_key
        return color_key

    category_lookup = {}
    for slug, name in base_categories:
        normalized_slug = (slug or "").strip().lower() or _slugify(name)
        category_lookup[normalized_slug] = name
    category_lookup.setdefault("ort", "Ort")
    assign_marker_color("ort")

    coords = []
    location_ids: list[str] = []
    for location in locations:
        try:
            lat_value = float(location.lat)
            lon_value = float(location.lon)
        except (TypeError, ValueError):
            continue

        related_offers = list(location.offers or [])
        visible_offers = [
            offer
            for offer in related_offers
            if _skip_offer_review() or offer.status == OfferStatus.published
        ]
        representative_offer = visible_offers[0] if visible_offers else None

        categories = []
        category_labels = []
        if visible_offers:
            for offer in visible_offers:
                for cat in (offer.categories or []):
                    slug = (cat.slug or "").strip().lower() or _slugify(cat.name)
                    name = cat.name or slug
                    categories.append(slug)
                    category_labels.append(name)
                    category_lookup.setdefault(slug, name)
                    assign_marker_color(slug)

        has_playground_category = any(slug in {"spielplatz", "playground"} for slug in categories)

        if not categories:
            categories = ["ort"]
            category_labels = ["Ort"]
            has_playground_category = False

        primary_category_slug = categories[0] if categories else "ort"
        marker_color = assign_marker_color(primary_category_slug)

        if representative_offer:
            offer_type_value = getattr(representative_offer.type, "value", representative_offer.type)
            is_permanent = offer_type_value == getattr(OfferType.permanent, "value", "permanent")
            meta_line = "Ständiges Angebot" if is_permanent else "Termin folgt"
            summary = representative_offer.summary or representative_offer.description or ""
            detail_url = url_for("event_detail", event_id=str(representative_offer.id))
            source = representative_offer.source
        else:
            offer_type_value = "location"
            is_permanent = True
            summary = ""
            meta_line = location.city or "Standort"
            query_value = (location.name or location.city or "").strip()
            detail_url = url_for("suchergebnisse", q=query_value) if query_value else url_for("suchergebnisse")
            source = "location"

        if not summary:
            summary = "Adresse: " + (location.address or location.city or "Noch keine Details")

        location_ids.append(str(location.id))

        coords.append(
            {
                "id": f"loc-{location.id}",
                "location_id": str(location.id),
                "title": location.name or (representative_offer.title if representative_offer else "Unbekannter Ort"),
                "summary": summary,
                "lat": lat_value,
                "lon": lon_value,
                "url": detail_url,
                "address": location.address or location.city or "",
                "location_name": location.name or "",
                "categories": categories,
                "category_labels": category_labels,
                "is_playground": has_playground_category,
                "is_permanent": is_permanent,
                "source": source,
                "offer_type": offer_type_value,
                "marker_color": marker_color,
                "meta_line": meta_line,
            }
        )

    category_filters = []
    for slug, name in sorted(category_lookup.items(), key=lambda item: item[1].lower()):
        color_key = assign_marker_color(slug)
        category_filters.append({"slug": slug, "name": name, "color": color_key})

    return render_template(
        "karte.html",
        coords=coords,
        location_ids=location_ids,
        category_filters=category_filters,
        total=len(coords),
        marker_color_styles=MARKER_COLOR_STYLES,
        default_marker_color=DEFAULT_MARKER_COLOR,
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

@app.get("/datenschutz")
def datenschutz():
    return render_template("datenschutz.html")

@app.get("/vorgaben")
def vorgaben():
    return render_template("vorgaben.html")

@app.route("/event/<uuid:event_id>")
def event_detail(event_id):
    event = Offer.query.get_or_404(event_id)
    if event.status != OfferStatus.published and not _skip_offer_review():
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(404)
    is_favorite = False
    if current_user.is_authenticated:
        is_favorite = (
            UserFavoriteOffer.query.filter_by(user_id=current_user.id, offer_id=event.id).first()
            is not None
        )
    canonical = url_for("event_detail", event_id=event.id, _external=True)
    meta_title = f"{event.title or 'Event'} – Familien-Erlebnis auf lottina"
    if event.summary:
        meta_description = event.summary
    elif event.description:
        meta_description = event.description[:150] + "..."
    else:
        meta_description = "Familienfreundliches Erlebnis auf lottina."
    type_value = getattr(event.type, "value", event.type)
    is_permanent = type_value == OfferType.permanent.value
    opening_hours_label = opening_hours_text(event.opening_hours)
    return render_template(
        "event.html",
        event=event,
        is_permanent=is_permanent,
        opening_hours_label=opening_hours_label,
        canonical=canonical,
        meta_title=meta_title,
        meta_description=meta_description,
        is_favorite=is_favorite,
        favorite_endpoint=url_for("favorite_offer_add", offer_id=event.id),
        register_url=url_for("register", next=request.full_path or request.path),
    )


@app.post("/event/<uuid:event_id>/delete")
@login_required
def event_delete(event_id):
    if not current_user.is_admin:
        abort(403)
    offer = Offer.query.get_or_404(event_id)
    db.session.delete(offer)
    db.session.commit()
    flash("Event gelöscht.", "success")
    redirect_target = request.form.get("redirect") or url_for("freigeben")
    if not redirect_target.startswith("/"):
        redirect_target = url_for("suchergebnisse")
    return redirect(redirect_target)


@app.route("/event/<uuid:event_id>/edit", methods=["GET", "POST"])
@login_required
def edit_event(event_id):
    event = Offer.query.get_or_404(event_id)
    if not current_user.is_admin:
        abort(403)

    had_existing_recurrence = bool(event.recurrence_rule or event.recurring_series_id)

    recurrence_frequency_choices = [
        ("none", "Keine Wiederholung"),
        ("daily", "Täglich"),
        ("weekly", "Wöchentlich"),
        ("biweekly", "Zweiwöchentlich"),
        ("monthly", "Monatlich"),
        ("quarterly", "Vierteljährlich"),
    ]
    recurrence_frequency_values = {value for value, _ in recurrence_frequency_choices}
    recurrence_weekday_core_choices = [
        ("mo", "Montag"),
        ("di", "Dienstag"),
        ("mi", "Mittwoch"),
        ("do", "Donnerstag"),
        ("fr", "Freitag"),
        ("sa", "Samstag"),
        ("so", "Sonntag"),
    ]
    recurrence_weekday_extra_choices = [
        ("mo-fr", "Montag bis Freitag"),
        ("mo-so", "Montag bis Sonntag"),
    ]
    recurrence_weekday_choices = recurrence_weekday_core_choices + recurrence_weekday_extra_choices
    recurrence_weekday_values = {value for value, _ in recurrence_weekday_core_choices}
    recurrence_weekday_range_map = {
        "mo-fr": ["mo", "di", "mi", "do", "fr"],
        "mo-so": ["mo", "di", "mi", "do", "fr", "sa", "so"],
    }

    publish_and_redirect = request.form.get("publish_now") == "1" if request.method == "POST" else False

    recurrence_scope_prefill = ""
    if request.method == "POST":
        recurrence_scope_prefill = (request.form.get("recurrence_update_scope") or "").strip().lower()
        if recurrence_scope_prefill not in {"single", "series"}:
            recurrence_scope_prefill = ""
    else:
        scope_from_query = (request.args.get("scope") or "").strip().lower()
        if scope_from_query in {"single", "series"}:
            recurrence_scope_prefill = scope_from_query

    text_fields = [
        "title",
        "summary",
        "image",
        "maps_url",
        "meeting_point",
        "currency",
        "source",
        "source_url",
        "source_name",
    ]
    long_text_fields = ["description"]
    datetime_fields = ["dt_start", "dt_end"]
    decimal_fields = ["price_value", "price_min", "price_max"]
    integer_fields = ["target_age_min", "target_age_max"]
    bool_fields = [
        "is_free",
        "is_outdoor",
        "is_indoor",
        "with_accompaniment",
        "hobby_regular",
        "is_once",
        "is_sporty",
        "is_creative",
        "pets_allowed",
    ]
    json_fields = {
        "opening_hours_json": "opening_hours",
        "holiday_hours_json": "holiday_hours",
    }
    location_text_fields = ["location_name", "location_address", "location_city"]
    location_number_fields = {"location_lat": "lat", "location_lon": "lon"}

    errors: list[str] = []
    field_errors: dict[str, str] = {}

    def _default_recurrence_slots() -> list[dict[str, str]]:
        return [{"weekday": "mo", "start": "", "end": ""}]

    def _parse_recurrence_rule(value: str | None) -> tuple[str, list[dict[str, str]], str]:
        if not value:
            return ("none", [], "")
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return ("none", [], "")
        freq = str(data.get("frequency") or "none").lower()
        raw_slots = data.get("slots") or []
        slots: list[dict[str, str]] = []
        for entry in raw_slots:
            if not isinstance(entry, dict):
                continue
            weekday = str(entry.get("weekday") or "").strip().lower()
            start_time = str(entry.get("start") or "").strip()
            end_time = str(entry.get("end") or "").strip()
            if freq == "daily" and (not weekday or weekday == "any"):
                weekday = "any"
            if weekday in recurrence_weekday_values or (freq == "daily" and weekday == "any"):
                slots.append({"weekday": weekday, "start": start_time, "end": end_time})
        if freq not in recurrence_frequency_values:
            freq = "none"
        until_raw = data.get("until")
        until_value = until_raw.strip() if isinstance(until_raw, str) else ""
        return (freq, slots, until_value)

    def _parse_time_value(value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        try:
            parsed = datetime.strptime(value, "%H:%M")
            return parsed.strftime("%H:%M")
        except ValueError:
            return None

    def _parse_date_value(value: str | None) -> date | None:
        value = (value or "").strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _set_recurrence_error(message: str) -> None:
        errors.append(message)
        field_errors.setdefault("recurrence", message)

    weekday_index_lookup = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}

    def _first_date_for_weekday(start_date, weekday_idx: int):
        delta = (weekday_idx - start_date.weekday()) % 7
        return start_date + timedelta(days=delta)

    def _month_meta(date_value):
        days_in_month = monthrange(date_value.year, date_value.month)
        nth = (date_value.day - 1) // 7 + 1
        is_last = date_value.day + 7 > days_in_month[1]
        return nth, is_last

    def _nth_weekday_of_month(year: int, month: int, weekday_idx: int, nth: int, prefer_last: bool):
        days_in_month = monthrange(year, month)[1]
        matches = []
        for day in range(1, days_in_month + 1):
            candidate = datetime(year, month, day).date()
            if candidate.weekday() == weekday_idx:
                matches.append(candidate)
        if not matches:
            return None
        if prefer_last or nth > len(matches):
            return matches[-1]
        return matches[nth - 1]

    def _advance_monthly_date(date_value, months_step: int, weekday_idx: int, nth: int, prefer_last: bool):
        month = date_value.month - 1 + months_step
        year = date_value.year + month // 12
        month = month % 12 + 1
        return _nth_weekday_of_month(year, month, weekday_idx, nth, prefer_last)

    def _advance_occurrence_date(date_value, frequency: str, weekday_idx: int, nth: int, prefer_last: bool):
        if frequency == "daily":
            return date_value + timedelta(days=1)
        if frequency == "weekly":
            return date_value + timedelta(weeks=1)
        if frequency == "biweekly":
            return date_value + timedelta(weeks=2)
        if frequency == "monthly":
            return _advance_monthly_date(date_value, 1, weekday_idx, nth, prefer_last)
        if frequency == "quarterly":
            return _advance_monthly_date(date_value, 3, weekday_idx, nth, prefer_last)
        return date_value + timedelta(weeks=1)

    def _generate_recurrence_occurrences(
        event_obj: Offer, frequency: str, slots_data: list[dict[str, str]], until_limit: date | None
    ):
        if not event_obj.dt_start:
            return []
        tzinfo = event_obj.dt_start.tzinfo or timezone.utc
        anchor_date = event_obj.dt_start.date()
        now_dt = datetime.now(tzinfo)
        cutoff_dt = event_obj.dt_start if event_obj.dt_start >= now_dt else now_dt
        horizon_end = cutoff_dt.date() + timedelta(days=RECURRENCE_GENERATION_DAYS)
        if until_limit:
            horizon_end = min(horizon_end, until_limit)
        occurrences: list[tuple[datetime, datetime]] = []
        max_occurrences = 500

        for slot in slots_data:
            weekday_value = (slot.get("weekday") or "").strip().lower()
            daily_mode = frequency == "daily" and (weekday_value in {"", "any"})
            if daily_mode:
                weekday_idx = anchor_date.weekday()
                current_date = anchor_date
            else:
                weekday_idx = weekday_index_lookup.get(weekday_value)
                if weekday_idx is None:
                    continue
                current_date = _first_date_for_weekday(anchor_date, weekday_idx)
            try:
                start_time_obj = datetime.strptime(slot.get("start", ""), "%H:%M").time()
                end_time_obj = datetime.strptime(slot.get("end", ""), "%H:%M").time()
            except (TypeError, ValueError):
                continue
            nth, prefer_last = _month_meta(current_date)
            safety_counter = 0
            while current_date <= horizon_end:
                start_dt = datetime.combine(current_date, start_time_obj).replace(tzinfo=tzinfo)
                end_date_base = current_date
                if end_time_obj <= start_time_obj:
                    end_date_base = current_date + timedelta(days=1)
                end_dt = datetime.combine(end_date_base, end_time_obj).replace(tzinfo=tzinfo)
                if start_dt != event_obj.dt_start and start_dt >= cutoff_dt:
                    occurrences.append((start_dt, end_dt))
                    if len(occurrences) >= max_occurrences:
                        return sorted(occurrences, key=lambda entry: entry[0])
                next_date = _advance_occurrence_date(current_date, frequency, weekday_idx, nth, prefer_last)
                if not next_date or next_date <= current_date:
                    break
                current_date = next_date
                safety_counter += 1
                if safety_counter > max_occurrences:
                    break
        return sorted(occurrences, key=lambda entry: entry[0])

    def _build_recurring_external_id(template_event: Offer) -> str:
        base_part = getattr(getattr(template_event, "id", None), "hex", "")[:8]
        if not base_part:
            base_part = uuid.uuid4().hex[:8]
        suffix = uuid.uuid4().hex[:8]
        value = f"rec-{base_part}-{suffix}"
        return value[:64]

    def _apply_template_to_offer(
        target: Offer,
        template: Offer,
        start_dt: datetime,
        end_dt: datetime,
        series_id,
        *,
        keep_external_id: bool,
    ) -> None:
        if not keep_external_id:
            target.external_id = _build_recurring_external_id(template)
        target.title = template.title
        target.summary = template.summary
        target.description = template.description
        target.image = template.image
        target.maps_url = template.maps_url
        target.meeting_point = template.meeting_point
        target.source = template.source
        target.source_url = template.source_url
        target.source_name = template.source_name
        target.source_type = template.source_type
        target.type = template.type
        target.status = template.status
        target.price_value = template.price_value
        target.price_min = template.price_min
        target.price_max = template.price_max
        target.currency = template.currency
        target.target_age_min = template.target_age_min
        target.target_age_max = template.target_age_max
        target.with_accompaniment = template.with_accompaniment
        target.is_free = template.is_free
        target.is_outdoor = template.is_outdoor
        target.is_indoor = template.is_indoor
        target.hobby_regular = template.hobby_regular
        target.is_sporty = template.is_sporty
        target.is_creative = template.is_creative
        target.pets_allowed = template.pets_allowed
        target.organizer_id = template.organizer_id
        target.organisation_id = template.organisation_id
        target.location_id = template.location_id
        target.is_internal = template.is_internal
        target.opening_hours = template.opening_hours
        target.holiday_hours = template.holiday_hours
        target.created_by_user_id = template.created_by_user_id
        target.registration_required = template.registration_required
        target.registration_methods = template.registration_methods
        target.registration_contact = template.registration_contact
        target.dt_start = start_dt
        target.dt_end = end_dt
        target.recurrence_rule = None
        target.is_recurring = True
        target.is_once = False
        target.recurring_series_id = series_id
        target.categories = list(template.categories)
        target.tags = list(template.tags)

    def _sync_recurrence_instances(
        event_obj: Offer, frequency: str, slots_data: list[dict[str, str]], until_limit: date | None
    ) -> None:
        def _delete_children(series_id):
            if not series_id:
                return
            siblings = (
                Offer.query.filter(Offer.recurring_series_id == series_id, Offer.id != event_obj.id).all()
            )
            for sibling in siblings:
                db.session.delete(sibling)

        if frequency == "none" or not slots_data:
            _delete_children(event_obj.recurring_series_id)
            event_obj.recurring_series_id = None
            return

        if not event_obj.dt_start:
            return

        series_id = event_obj.recurring_series_id or uuid.uuid4()
        event_obj.recurring_series_id = series_id

        desired_occurrences = _generate_recurrence_occurrences(event_obj, frequency, slots_data, until_limit)
        existing_children = (
            Offer.query.filter(Offer.recurring_series_id == series_id, Offer.id != event_obj.id).all()
        )
        existing_by_start = {child.dt_start: child for child in existing_children if child.dt_start}
        desired_starts: set[datetime] = set()

        for start_dt, end_dt in desired_occurrences:
            desired_starts.add(start_dt)
            child = existing_by_start.get(start_dt)
            if child:
                _apply_template_to_offer(child, event_obj, start_dt, end_dt, series_id, keep_external_id=True)
            else:
                child = Offer()
                _apply_template_to_offer(child, event_obj, start_dt, end_dt, series_id, keep_external_id=False)
                db.session.add(child)

        for child in existing_children:
            if child.dt_start not in desired_starts:
                db.session.delete(child)

    recurrence_frequency, recurrence_slots, recurrence_until_value = _parse_recurrence_rule(event.recurrence_rule)
    if not recurrence_slots:
        recurrence_slots = _default_recurrence_slots()

    def _slugify_category(value: str) -> str:
        mapping = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
        slug = value.strip().lower()
        for src, target in mapping.items():
            slug = slug.replace(src, target)
        slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
        return slug or "kategorie"

    if request.method == "POST":
        for field in text_fields:
            raw = (request.form.get(field) or "").strip()
            setattr(event, field, raw or None)

        for field in long_text_fields:
            raw = request.form.get(field)
            setattr(event, field, raw or None)

        for field in datetime_fields:
            raw = (request.form.get(field) or "").strip()
            if not raw:
                setattr(event, field, None)
                continue
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                setattr(event, field, parsed)
            except ValueError:
                errors.append(f"Ungültiges Datum/Zeit für Feld '{field}'.")
                field_errors[field] = "Ungültiges Datum/Zeit"

        for field in decimal_fields:
            raw = (request.form.get(field) or "").strip()
            if not raw:
                setattr(event, field, None)
                continue
            try:
                setattr(event, field, Decimal(raw))
            except InvalidOperation:
                errors.append(f"Ungültiger Zahlenwert für Feld '{field}'.")
                field_errors[field] = "Ungültiger Zahlenwert"

        for field in integer_fields:
            raw = (request.form.get(field) or "").strip()
            if not raw:
                setattr(event, field, None)
                continue
            try:
                setattr(event, field, int(raw))
            except ValueError:
                errors.append(f"Ungültige Ganzzahl für Feld '{field}'.")
                field_errors[field] = "Ungültige Ganzzahl"

        for field in bool_fields:
            setattr(event, field, request.form.get(field) == "on")

        force_single_event = request.form.get("is_once") == "on"

        type_value = (request.form.get("type") or "").strip()
        if type_value:
            try:
                event.type = OfferType(type_value)
            except ValueError:
                errors.append("Ungültiger Angebotstyp.")
                field_errors["type"] = "Ungültiger Typ"

        status_value = (request.form.get("status") or "").strip()
        if status_value:
            try:
                event.status = OfferStatus(status_value)
            except ValueError:
                errors.append("Ungültiger Status.")
                field_errors["status"] = "Ungültiger Status"

        source_type_value = (request.form.get("source_type") or "").strip()
        if source_type_value:
            try:
                event.source_type = SourceType(source_type_value)
            except ValueError:
                errors.append("Ungültiger Quellentyp.")
                field_errors["source_type"] = "Ungültiger Quellentyp"

        recurrence_rule_serialized: str | None = None
        recurrence_frequency_value = (request.form.get("recurrence_frequency") or "none").strip().lower()
        if force_single_event:
            recurrence_frequency_value = "none"
        if recurrence_frequency_value not in recurrence_frequency_values:
            _set_recurrence_error("Ungültiger Wiederholungsrhythmus ausgewählt.")
            recurrence_frequency = "none"
        else:
            recurrence_frequency = recurrence_frequency_value

        recurrence_slots_input: list[dict[str, str]] = []
        recurrence_until_input = (request.form.get("recurrence_until") or "").strip()
        recurrence_until_date: date | None = None

        if recurrence_frequency != "none":
            weekdays_input = request.form.getlist("recurrence_weekday[]")
            starts_input = request.form.getlist("recurrence_start[]")
            ends_input = request.form.getlist("recurrence_end[]")
            max_count = max(len(weekdays_input), len(starts_input), len(ends_input))
            for idx in range(max_count):
                weekday_raw = (weekdays_input[idx] if idx < len(weekdays_input) else "").strip().lower()
                start_raw = (starts_input[idx] if idx < len(starts_input) else "").strip()
                end_raw = (ends_input[idx] if idx < len(ends_input) else "").strip()
                if not (weekday_raw or start_raw or end_raw):
                    continue
                if recurrence_frequency == "daily":
                    target_weekdays = ["any"]
                else:
                    if weekday_raw in recurrence_weekday_range_map:
                        target_weekdays = recurrence_weekday_range_map[weekday_raw]
                    elif weekday_raw in recurrence_weekday_values:
                        target_weekdays = [weekday_raw]
                    else:
                        _set_recurrence_error("Bitte wähle einen gültigen Wochentag oder Zeitraum.")
                        continue
                start_time = _parse_time_value(start_raw)
                end_time = _parse_time_value(end_raw)
                if not start_time or not end_time:
                    _set_recurrence_error("Start- und Endzeit müssen im Format HH:MM angegeben werden.")
                    continue
                if start_time == end_time:
                    _set_recurrence_error("Start- und Endzeit dürfen nicht identisch sein.")
                    continue
                for weekday_value in target_weekdays:
                    recurrence_slots_input.append(
                        {"weekday": weekday_value, "start": start_time, "end": end_time}
                    )

            if not recurrence_slots_input:
                error_message = (
                    "Bitte füge mindestens eine Zeitspanne hinzu."
                    if recurrence_frequency == "daily"
                    else "Bitte füge mindestens einen Wochentag mit Zeitspanne hinzu."
                )
                _set_recurrence_error(error_message)

            if recurrence_until_input:
                recurrence_until_date = _parse_date_value(recurrence_until_input)
                if not recurrence_until_date:
                    _set_recurrence_error("Bitte gib ein gültiges Enddatum für die Wiederholungen an (YYYY-MM-DD).")
                elif event.dt_start and recurrence_until_date < event.dt_start.date():
                    _set_recurrence_error("Das Enddatum der Wiederholungen muss nach dem Startdatum liegen.")
        else:
            recurrence_until_input = ""
            recurrence_until_date = None

        recurrence_slots = recurrence_slots_input or _default_recurrence_slots()
        recurrence_until_value = recurrence_until_input

        if recurrence_frequency != "none" and not event.dt_start:
            _set_recurrence_error("Bitte gib Startdatum und Startzeit an, um Wiederholungen zu planen.")

        if (
            recurrence_frequency != "none"
            and recurrence_slots_input
            and "recurrence" not in field_errors
        ):
            payload = {"frequency": recurrence_frequency, "slots": recurrence_slots_input}
            if recurrence_until_date:
                payload["until"] = recurrence_until_date.isoformat()
            serialized_rule = json.dumps(payload, separators=(",", ":"))
            if len(serialized_rule) > 1000:
                _set_recurrence_error("Die Angaben zur Wiederholung sind zu umfangreich.")
            else:
                recurrence_rule_serialized = serialized_rule

        if publish_and_redirect:
            event.status = OfferStatus.published

        for form_name, attr_name in json_fields.items():
            raw = (request.form.get(form_name) or "").strip()
            if not raw:
                setattr(event, attr_name, None)
                continue
            try:
                setattr(event, attr_name, json.loads(raw))
            except json.JSONDecodeError as exc:
                errors.append(f"JSON Feld '{form_name}': {exc.msg}")
                field_errors[form_name] = "Ungültiges JSON"

        location_inputs_present = any(
            (request.form.get(name) or "").strip() for name in [*location_text_fields, *location_number_fields.keys()]
        )
        if event.location is None and location_inputs_present:
            event.location = Location()
            db.session.add(event.location)

        if event.location:
            for field in location_text_fields:
                value = (request.form.get(field) or "").strip()
                setattr(event.location, field.replace("location_", ""), value or None)

            for form_name, attr_name in location_number_fields.items():
                raw_val = (request.form.get(form_name) or "").strip()
                if not raw_val:
                    setattr(event.location, attr_name, None)
                    continue
                try:
                    setattr(event.location, attr_name, float(raw_val))
                except ValueError:
                    errors.append(f"Ungültiger Zahlenwert für Feld '{form_name}'.")
                    field_errors[form_name] = "Ungültiger Zahlenwert"

        categories_raw = request.form.get("categories", "") or ""
        category_names = [name.strip() for name in categories_raw.split(",") if name.strip()]
        if category_names:
            new_categories = []
            for name in category_names:
                slug = _slugify_category(name)
                category = (
                    Category.query.filter(func.lower(Category.slug) == slug).one_or_none()
                    or Category.query.filter(func.lower(Category.name) == name.lower()).one_or_none()
                )
                if category is None:
                    category = Category(slug=slug, name=name)
                    db.session.add(category)
                new_categories.append(category)
            event.categories = new_categories
        else:
            event.categories = []

        if not errors:
            try:
                if recurrence_frequency == "none" or not recurrence_rule_serialized:
                    event.recurrence_rule = None
                    event.is_recurring = False
                    event.is_once = True
                else:
                    event.recurrence_rule = recurrence_rule_serialized
                    event.is_recurring = True
                    event.is_once = False

                apply_series_updates = not (had_existing_recurrence and recurrence_scope_prefill == "single")
                sync_slots = recurrence_slots_input if recurrence_frequency != "none" else []
                until_limit = recurrence_until_date if recurrence_frequency != "none" else None
                should_sync_series = recurrence_frequency == "none" or apply_series_updates
                if should_sync_series:
                    _sync_recurrence_instances(event, recurrence_frequency, sync_slots, until_limit)

                sync_permanent_availability(db.session, event)
                db.session.commit()
                flash(
                    "Event aktualisiert und veröffentlicht." if publish_and_redirect else "Event erfolgreich aktualisiert.",
                    "success",
                )
                redirect_target = url_for("freigeben") if publish_and_redirect else url_for("event_detail", event_id=str(event.id))
                return redirect(redirect_target)
            except Exception:
                db.session.rollback()
                errors.append("Speichern fehlgeschlagen. Bitte später erneut versuchen.")

    location_proxy = event.location or Location()
    prefill = {
        "title": event.title or "",
        "summary": event.summary or "",
        "description": event.description or "",
        "image": event.image or "",
        "maps_url": event.maps_url or "",
        "meeting_point": event.meeting_point or "",
        "source": event.source or "",
        "source_url": event.source_url or "",
        "source_name": event.source_name or "",
        "price_value": "" if event.price_value is None else str(event.price_value),
        "price_min": "" if event.price_min is None else str(event.price_min),
        "price_max": "" if event.price_max is None else str(event.price_max),
        "currency": event.currency or "",
        "target_age_min": "" if event.target_age_min is None else str(event.target_age_min),
        "target_age_max": "" if event.target_age_max is None else str(event.target_age_max),
        "dt_start": _format_datetime_input(event.dt_start),
        "dt_end": _format_datetime_input(event.dt_end),
        "opening_hours_json": json.dumps(event.opening_hours, ensure_ascii=False, indent=2)
        if event.opening_hours
        else "",
        "holiday_hours_json": json.dumps(event.holiday_hours, ensure_ascii=False, indent=2)
        if event.holiday_hours
        else "",
        "location_name": location_proxy.name or "",
        "location_address": location_proxy.address or "",
        "location_city": location_proxy.city or "",
        "location_lat": "" if location_proxy.lat is None else str(location_proxy.lat),
        "location_lon": "" if location_proxy.lon is None else str(location_proxy.lon),
        "categories": ", ".join(filter(None, [cat.name or cat.slug for cat in event.categories])),
        "type": getattr(event.type, "value", ""),
        "status": getattr(event.status, "value", ""),
        "source_type": getattr(event.source_type, "value", ""),
        "recurrence_frequency": recurrence_frequency,
        "recurrence_until": recurrence_until_value,
    }

    if request.method == "POST":
        for key in list(prefill.keys()):
            if key in request.form:
                prefill[key] = request.form.get(key, "")
        prefill["recurrence_frequency"] = recurrence_frequency
        prefill["recurrence_until"] = recurrence_until_value

    bool_states = {field: bool(getattr(event, field)) for field in bool_fields}
    if request.method == "POST":
        for field in bool_fields:
            bool_states[field] = request.form.get(field) == "on"

    offer_type_choices = [(choice.value, choice.name.title()) for choice in OfferType]
    source_type_choices = [(choice.value, choice.name.title()) for choice in SourceType]
    status_choices = [(choice.value, choice.name.title()) for choice in OfferStatus]
    category_choices = [
        cat.name or cat.slug
        for cat in Category.query.order_by(Category.name.asc()).all()
        if (cat.name or cat.slug)
    ]

    return render_template(
        "event_edit.html",
        event=event,
        prefill=prefill,
        bool_states=bool_states,
        errors=errors,
        field_errors=field_errors,
        offer_type_choices=offer_type_choices,
        source_type_choices=source_type_choices,
        status_choices=status_choices,
        category_choices=category_choices,
        recurrence_frequency_choices=recurrence_frequency_choices,
        recurrence_weekday_choices=recurrence_weekday_choices,
        recurrence_frequency=prefill.get("recurrence_frequency", "none"),
        recurrence_slots=recurrence_slots or _default_recurrence_slots(),
        recurrence_until=prefill.get("recurrence_until", ""),
        has_existing_recurrence=had_existing_recurrence,
        recurrence_scope_prefill=recurrence_scope_prefill,
    )

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
    total_entries = db.session.query(func.count(Offer.id)).scalar() or 0
    feedback_count = db.session.query(func.count(CommunityFeedback.id)).scalar() or 0
    user_count = db.session.query(func.count(User.id)).scalar() or 0
    impact_stats = [
        {"label": "Datenbank-Einträge", "value": total_entries},
        {"label": "Community-Feedbacks", "value": feedback_count},
        {"label": "Registrierte Familien", "value": user_count},
    ]
    return render_template("ueber_uns.html", team=team, values=values, impact_stats=impact_stats)

@app.get("/feedback")
def feedback():
    return render_template("feedback.html")


@app.post("/feedback/submit")
def submit_feedback():
    kind = (request.form.get("kind") or FEEDBACK_KIND_FEEDBACK).strip().lower()
    if kind not in FEEDBACK_KIND_LABELS:
        kind = FEEDBACK_KIND_FEEDBACK
    email = (request.form.get("email") or "").strip().lower()
    city = (request.form.get("city") or "").strip()
    message = (request.form.get("message") or "").strip()
    source = (request.form.get("source") or "").strip() or request.referrer or "unknown"
    redirect_target = request.form.get("redirect") or request.referrer or url_for("feedback")

    if email and not EMAIL_RX.match(email):
        flash("Bitte gib eine gültige E-Mail-Adresse an.", "danger")
        return redirect(redirect_target)
    if not message and not city:
        flash("Bitte erzähl uns kurz, welche Stadt oder welches Feedback du hast.", "danger")
        return redirect(redirect_target)

    try:
        create_feedback_entry(kind=kind, email=email, city=city, message=message, source=source)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.exception("Feedback konnte nicht gespeichert werden: %s", exc)
        flash("Dein Hinweis konnte nicht gespeichert werden. Versuch es gleich nochmal.", "danger")
        return redirect(redirect_target)

    success_msg = "Danke für dein Feedback!"
    if kind == FEEDBACK_KIND_CITY:
        success_msg = "Danke! Wir notieren deinen Wunsch und melden uns bei Neuigkeiten."
    flash(success_msg, "success")
    return redirect(redirect_target)

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
    annual_base = MONTHLY_PRICE_EUR * Decimal(12)
    savings_value = annual_base - YEARLY_PRICE_EUR
    if savings_value < Decimal("0"):
        savings_value = Decimal("0.00")
    savings_value = savings_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    savings_percent = Decimal("0.0")
    if annual_base:
        savings_percent = (
            (savings_value / annual_base) * Decimal(100)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    return render_template(
        "preise.html",
        stripe_price_monthly=app.config["STRIPE_PRICE_MONTHLY"],
        stripe_price_yearly=app.config.get("STRIPE_PRICE_YEARLY"),
        monthly_price_eur=MONTHLY_PRICE_EUR,
        yearly_price_eur=YEARLY_PRICE_EUR,
        annual_savings_eur=savings_value,
        annual_savings_percent=savings_percent,
    )


@app.get("/checkout/success", endpoint="checkout_success")
def checkout_success():
    if current_user.is_authenticated and not session.get("membership_mail_sent"):
        send_membership_email(current_user)
        session["membership_mail_sent"] = True
    return render_template("success.html")

@app.cli.command("crawl-external")
def crawl_external():
    """Hole Events über hinterlegte Crawler."""
    from .crawlers import run_all_crawlers

    results = run_all_crawlers()
    click.echo("Crawler abgeschlossen:")
    for name, stats in results.items():
        created = stats.get("created", 0)
        updated = stats.get("updated", 0)
        click.echo(f"  • {name}: {created} erstellt, {updated} aktualisiert")

@app.cli.command("cleanup-events")
@click.option("--days", default=1, show_default=True, help="Wie viele Tage sollen behalten werden?")
def cleanup_events(days: int):
    """Löscht Events, deren Datum in der Vergangenheit liegt."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = (
        Offer.query.filter(
            (
                (Offer.dt_end.isnot(None)) & (Offer.dt_end < cutoff)
            ) | (
                (Offer.dt_end.is_(None)) & (Offer.dt_start.isnot(None)) & (Offer.dt_start < cutoff)
            )
        )
        .delete(synchronize_session=False)
    )
    db.session.commit()
    click.echo(f"{deleted} Events bereinigt (älter als {days} Tag(e)).")

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

def _dashboard_context():
    """Dashboard-Daten aus der Datenbank aufbereiten."""
    children_query = current_user.children.order_by(UserChild.created_at.asc())
    child_rows = [
        {
            "id": child.id,
            "name": child.name or "",
            "age": child.age or "",
            "interests": child.interests or [],
        }
        for child in children_query
    ]
    child_profiles_count = len(child_rows)
    child_profiles = child_rows if child_rows else [{"id": None, "name": "", "age": "", "interests": []}]
    if not child_profiles:
        child_profiles = [{"id": None, "name": "", "age": "", "interests": []}]

    favorite_rows = (
        db.session.query(Offer, UserFavoriteOffer.created_at)
        .join(UserFavoriteOffer, UserFavoriteOffer.offer_id == Offer.id)
        .filter(UserFavoriteOffer.user_id == current_user.id)
        .order_by(UserFavoriteOffer.created_at.desc())
        .limit(6)
        .all()
    )
    favorite_events = []
    for offer, created_at in favorite_rows:
        date_label = "Termin folgt"
        time_label = ""
        if offer.dt_start:
            localized = offer.dt_start.astimezone()
            date_label = localized.strftime("%A, %d.%m.")
            end_dt = offer.dt_end.astimezone() if offer.dt_end else None
            if end_dt and end_dt.date() == localized.date():
                time_label = f"{localized.strftime('%H:%M')} – {end_dt.strftime('%H:%M')} Uhr"
            elif end_dt:
                time_label = f"{localized.strftime('%H:%M')} Uhr · bis {end_dt.strftime('%d.%m. %H:%M')} Uhr"
            else:
                time_label = f"{localized.strftime('%H:%M')} Uhr"
        tags = [cat.name for cat in (offer.categories or [])][:3]
        favorite_events.append(
            {
                "id": str(offer.id),
                "title": offer.title or "Ohne Titel",
                "date": date_label,
                "time": time_label,
                "location": offer.location.name if offer.location else "Ort folgt",
                "tags": tags,
                "is_free": bool(offer.is_free),
                "url": url_for("event_detail", event_id=str(offer.id)),
            }
        )

    available_interests = [
        "Klettern",
        "Forschen",
        "Musik",
        "Kreativ",
        "Schwimmen",
        "Natur",
        "Tanz",
        "Gaming",
        "Sprachen",
    ]

    nearby_events = [
        {
            "title": "Kinderbauernhof Pinke-Panke",
            "category": "Draußen",
            "distance": "0,8 km",
            "time": "Heute, 15:00 Uhr",
            "lat": 52.5564,
            "lon": 13.4027,
        },
        {
            "title": "FabLab Mini-Maker",
            "category": "Technik",
            "distance": "1,3 km",
            "time": "Heute, 17:30 Uhr",
            "lat": 52.5488,
            "lon": 13.4129,
        },
        {
            "title": "Kinderkino am Pankeufer",
            "category": "Film",
            "distance": "2,1 km",
            "time": "Morgen, 16:00 Uhr",
            "lat": 52.5462,
            "lon": 13.3895,
        },
    ]

    return dict(
        favorite_events=favorite_events,
        child_profiles=child_profiles,
        child_profiles_count=child_profiles_count,
        available_interests=available_interests,
        nearby_events=nearby_events,
    )


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", **_dashboard_context())


@app.post("/dashboard/profile")
@login_required
def dashboard_profile_update():
    firstname = (request.form.get("firstname") or "").strip()
    lastname = (request.form.get("lastname") or "").strip()
    city = (request.form.get("city") or "").strip()
    had_changes = False
    if firstname != (current_user.firstname or ""):
        current_user.firstname = firstname or None
        had_changes = True
    if lastname != (current_user.lastname or ""):
        current_user.lastname = lastname or None
        had_changes = True
    if city != (current_user.city or ""):
        current_user.city = city or None
        had_changes = True
    if had_changes:
        db.session.commit()
        flash("Profil aktualisiert.", "success")
    else:
        flash("Keine Änderungen erkannt.", "info")
    return redirect(url_for("dashboard"))


@app.post("/dashboard/profile/image")
@login_required
def dashboard_profile_image_upload():
    file = request.files.get("profile_image")
    if not file or not file.filename:
        flash("Bitte wähle ein Bild aus.", "danger")
        return redirect(url_for("dashboard"))
    if not allowed(file.filename):
        flash("Unterstützt werden JPG, JPEG, PNG oder WEBP.", "danger")
        return redirect(url_for("dashboard"))
    saved = save_upload(file, PROFILE_IMAGE_FOLDER)
    old_image = (current_user.profile_image or "").strip()
    if old_image:
        old_path = (PROFILE_IMAGE_FOLDER / secure_filename(old_image)).resolve()
        try:
            if str(old_path).startswith(str(PROFILE_IMAGE_FOLDER.resolve())) and old_path.exists():
                old_path.unlink()
        except Exception:
            pass
    current_user.profile_image = saved.name
    db.session.commit()
    flash("Profilbild aktualisiert.", "success")
    return redirect(url_for("dashboard"))


@app.post("/dashboard/children")
@login_required
def dashboard_children_save():
    payload = request.get_json(silent=True) or {}
    children = payload.get("children") or []
    cleaned_children = []
    for entry in children:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        try:
            age_value = entry.get("age")
            age = int(age_value) if age_value not in (None, "") else None
            if age is not None and age < 0:
                age = None
        except (TypeError, ValueError):
            age = None
        raw_interests = entry.get("interests") or []
        interests = []
        for interest in raw_interests:
            if interest and interest not in interests:
                interests.append(str(interest)[:80])
            if len(interests) >= 3:
                break
        cleaned_children.append({"name": name[:80], "age": age, "interests": interests})

    UserChild.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
    for child in cleaned_children:
        db.session.add(
            UserChild(
                user_id=current_user.id,
                name=child["name"],
                age=child["age"],
                interests=child["interests"],
            )
        )
    db.session.commit()
    return jsonify({"ok": True, "count": len(cleaned_children)})


@app.post("/favorites/<uuid:offer_id>")
@login_required
def favorite_offer_add(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    exists = (
        UserFavoriteOffer.query.filter_by(user_id=current_user.id, offer_id=offer.id).first()
    )
    if exists:
        return jsonify({"ok": True, "favorite": True})
    db.session.add(UserFavoriteOffer(user_id=current_user.id, offer_id=offer.id))
    db.session.commit()
    return jsonify({"ok": True, "favorite": True})


@app.delete("/favorites/<uuid:offer_id>")
@login_required
def favorite_offer_remove(offer_id):
    deleted = (
        UserFavoriteOffer.query.filter_by(user_id=current_user.id, offer_id=offer_id).delete()
    )
    if deleted:
        db.session.commit()
    return jsonify({"ok": True, "favorite": False})


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not username or not email or not password:
            flash("Bitte alle Felder ausfüllen.", "danger")
            return render_template("register.html"), 400
        if len(password) < 8:
            flash("Passwort muss mindestens 8 Zeichen haben.", "danger")
            return render_template("register.html"), 400
        if password != password_confirm:
            flash("Die eingegebenen Passwörter stimmen nicht überein.", "danger")
            return render_template("register.html"), 400

        try:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            send_welcome_email(user)
            flash("Konto angelegt. Du kannst dich jetzt einloggen.", "success")
            return redirect(url_for("login"))
        except IntegrityError:
            db.session.rollback()
            flash("Benutzername oder E-Mail ist bereits vergeben.", "danger")
            return render_template("register.html"), 409

    return render_template("register.html")


@app.post("/password-reset")
def password_reset_request():
    email = (request.form.get("reset_email") or "").strip().lower()
    if not email:
        flash("Bitte gib deine E-Mail-Adresse ein.", "danger")
        return redirect(url_for("login"))
    user = User.query.filter(func.lower(User.email) == email).first()
    if not user:
        flash("Wir konnten kein Konto mit dieser E-Mail finden.", "danger")
        return redirect(url_for("login"))
    reset_url = url_for("login", _external=True) + "?reset=1"
    send_templated_email(
        subject="Passwort zurücksetzen",
        template="password_reset",
        recipients=[user.email],
        user=user,
        reset_url=reset_url,
    )
    flash("Wir haben dir eine E-Mail zum Zurücksetzen gesendet.", "success")
    return redirect(url_for("login"))

@app.get("/profil")
def profil():
    return render_template("dashboard.html", **_dashboard_context())


@app.post("/account/delete")
@login_required
def delete_account():
    user = current_user
    try:
        db.session.delete(user)
        db.session.commit()
        logout_user()
        flash("Dein Konto wurde gelöscht.", "success")
        return redirect(url_for("index"))
    except Exception:
        db.session.rollback()
        flash("Konto konnte nicht entfernt werden. Bitte versuch es später erneut.", "danger")
        return redirect(url_for("profil")), 500

# ---------------------------------------------------------------------------
# Anbieter / Events erstellen
# ---------------------------------------------------------------------------
def _list_media_images(limit: int = 60):
    try:
        files = sorted(
            (p for p in IMAGE_FOLDER.glob("*") if p.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return []

    items: list[dict[str, str]] = []
    for path in files[:limit]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        items.append(
            {
                "name": path.name,
                "url": f"/uploads/images/{path.name}",
                "size": f"{stat.st_size / 1024:.1f} KB",
            }
        )
    return items

def _render_event_form(
    form_data=None,
    ocr_text=None,
    ocr_error=None,
    ocr_filled=None,
    submission_success=False,
    submitted_title=None,
    submission_error=None,
):
    data = dict(form_data or {})
    mode = data.get("form_mode")
    if mode not in (MODE_MANUAL, MODE_OCR):
        mode = MODE_MANUAL
    data["form_mode"] = mode
    return render_template(
        "event-erstellen.html",
        form_data=data,
        form_mode=mode,
        ocr_text=ocr_text,
        ocr_error=ocr_error,
        ocr_filled=ocr_filled or [],
        media_images=_list_media_images(),
        submission_success=submission_success,
        submitted_title=submitted_title,
        submission_error=submission_error,
    )


@app.get("/event-erstellen")
def event_erstellen():
    return _render_event_form({"form_mode": MODE_MANUAL})

def _serve_uploaded_file(folder: Path, fname: str):
    safe_name = secure_filename(fname)
    target_path = (folder / safe_name).resolve()
    base_path = folder.resolve()
    try:
        if not str(target_path).startswith(str(base_path)):
            abort(404)
    except Exception:
        abort(404)
    if not target_path.exists() or not target_path.is_file():
        abort(404)
    mimetype, _ = mimetypes.guess_type(target_path.name)
    return send_file(str(target_path), mimetype=mimetype, as_attachment=False)


@app.route("/uploads/images/<path:fname>")
def _serve_uploaded_image(fname):
    return _serve_uploaded_file(IMAGE_FOLDER, fname)


@app.route("/uploads/profile/<path:fname>")
def serve_profile_image(fname):
    return _serve_uploaded_file(PROFILE_IMAGE_FOLDER, fname)

@app.post("/event-erstellen")
def create_event():
    f = request.form
    poster_file = request.files.get("poster_file")
    interactive_mode = f.get("interactive") == "1"
    form_data = request.form.to_dict()
    form_data.pop("perform_ocr", None)
    form_data.setdefault("image_url", form_data.get("image_url") or "")
    mode = form_data.get("form_mode")
    if mode not in (MODE_MANUAL, MODE_OCR):
        mode = MODE_MANUAL
    form_data["form_mode"] = mode

    if f.get("perform_ocr") == "1":
        form_data["form_mode"] = MODE_OCR
        if not poster_file or not poster_file.filename:
            return _render_event_form(form_data, ocr_error="Bitte wähle eine Bilddatei aus.")
        if not allowed(poster_file.filename):
            return _render_event_form(form_data, ocr_error="Unterstützt werden JPG, JPEG, PNG oder WEBP.")
        saved = save_upload(poster_file, IMAGE_FOLDER)
        form_data["image_url"] = f"/uploads/images/{saved.name}"
        try:
            text = run_ocr(str(saved))
            extracted = extract_fields(text) or {}
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Remote OCR prefill failed: %s", exc)
            return _render_event_form(form_data, ocr_error="Texterkennung konnte nicht durchgeführt werden.")
        filled_labels: list[str] = []
        if extracted:
            filled_keys: list[str] = []
            for key in OCR_FORM_FIELDS:
                value = extracted.get(key)
                if value:
                    form_data[key] = value
                    filled_keys.append(key)
            if not filled_keys and text:
                form_data["description"] = text
            filled_labels = [OCR_FIELD_LABELS.get(key, key.title()) for key in filled_keys]
        else:
            if text:
                form_data["description"] = text
        return _render_event_form(form_data, ocr_text=text, ocr_filled=filled_labels)

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

    def _fail(message: str):
        if interactive_mode:
            return _render_event_form(form_data, submission_error=message), 400
        flash(message, "danger")
        return redirect(url_for("event_erstellen")), 400

    # Bild
    image_url = (f.get("image_url") or "").strip() or None
    ocr_text = None
    is_permanent_flag = (f.get("is_permanent") or "").strip().lower() in {"1", "true", "on", "yes"}
    if poster_file and poster_file.filename:
        if not allowed(poster_file.filename):
            msg = "Nur JPG, JPEG, PNG oder WEBP werden unterstützt."
            if interactive_mode:
                return _render_event_form(form_data, submission_error=msg), 400
            flash(msg, "danger")
            return redirect(url_for("event_erstellen")), 400
        saved = save_upload(poster_file, IMAGE_FOLDER)
        image_url = f"/uploads/images/{saved.name}"
        try:
            ocr_text = run_ocr(str(saved))
            flash("Texterkennung erfolgreich – Beschreibung wurde vorausgefüllt.", "info")
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Remote OCR failed during event save: %s", exc)
            flash("Bild gespeichert, Texterkennung nicht möglich.", "warning")

    description_text = (f.get("description") or "").strip()
    if not description_text and ocr_text:
        description_text = ocr_text

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
    if is_permanent_flag:
        dt_start = None
        dt_end = None

    price_value = _to_float(f.get("price") or f.get("price_value"))
    price_min = _to_float(f.get("price_min"))
    price_max = _to_float(f.get("price_max"))
    currency_raw = (f.get("currency") or "").strip().upper()
    currency = currency_raw[:3] if currency_raw else None
    is_free = _to_bool(f.get("is_free"))
    if is_free is None and price_value is not None:
        is_free = (price_value == 0.0)
    if is_free is None and price_info and "kostenlos" in price_info.lower():
        is_free = True

    ag_min = ag_max = None
    target_age_min_input = (f.get("target_age_min") or "").strip()
    target_age_max_input = (f.get("target_age_max") or "").strip()
    if target_age_min_input.isdigit():
        ag_min = int(target_age_min_input)
    if target_age_max_input.isdigit():
        ag_max = int(target_age_max_input)

    age_group = (f.get("age_group") or "").strip()
    if not age_group and (ag_min is not None or ag_max is not None):
        if ag_min is not None and ag_max is not None:
            age_group = f"{ag_min}–{ag_max} Jahre"
        elif ag_min is not None:
            age_group = f"ab {ag_min} Jahren"
        elif ag_max is not None:
            age_group = f"bis {ag_max} Jahre"
    if not age_group:
        return _fail("Bitte gib eine Altersempfehlung an (z. B. ab 4 Jahren oder 4–8 Jahre).")
    m = re.search(r"\b(\d{1,2})\b", age_group)
    if m:
        ag_min = int(m.group(1))

    location_name_raw = (f.get("location_name") or f.get("location") or "").strip()
    location_address_raw = (f.get("location_address") or "").strip()
    location_city_raw = (f.get("location_city") or "").strip()
    if not location_name_raw:
        return _fail("Bitte gib einen Ort oder eine Adresse an.")
    lat = _to_float(f.get("location_lat") or f.get("lat"))
    lon = _to_float(f.get("location_lon") or f.get("lon"))

    if len(location_name_raw) > MAX_LOC_NAME:
        location_name_raw = location_name_raw[:MAX_LOC_NAME]

    loc_name = shorten(location_name_raw, MAX_LOC_NAME)
    loc_addr = shorten(location_address_raw or location_name_raw, MAX_LOC_ADDR)

    city_guess = location_city_raw or None
    if not city_guess and description_text:
        _, city_guess = extract_addr_city_from_text(description_text)

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

    is_outdoor_flag = _to_bool(f.get("is_outdoor"))
    is_indoor_flag = _to_bool(f.get("is_indoor"))
    with_accompaniment_flag = _to_bool(f.get("with_accompaniment"))
    hobby_regular_flag = _to_bool(f.get("hobby_regular"))
    is_once_flag = _to_bool(f.get("is_once"))
    pets_allowed_flag = _to_bool(f.get("pets_allowed"))

    offer = Offer(
        title=title,
        description=description_text or None,
        summary=summary,
        external_id=external_id,
        source=source,
        source_url=source_url,
        dt_start=dt_start,
        dt_end=dt_end,
        price_value=price_value,
        price_min=price_min if price_min is not None else price_value,
        price_max=price_max if price_max is not None else price_value,
        currency=currency or "EUR",
        is_free=is_free if is_free is not None else False,
        image=image_url,
        maps_url=(f.get("maps_url") or None),
        meeting_point=meeting_point,
        is_outdoor=is_outdoor_flag if is_outdoor_flag is not None else False,
        is_indoor=is_indoor_flag if is_indoor_flag is not None else False,
        with_accompaniment=with_accompaniment_flag if with_accompaniment_flag is not None else False,
        hobby_regular=hobby_regular_flag if hobby_regular_flag is not None else False,
        is_once=is_once_flag if is_once_flag is not None else False,
        pets_allowed=pets_allowed_flag if pets_allowed_flag is not None else False,
        opening_hours={"general": opening_hours_text} if opening_hours_text else None,
        target_age_min=ag_min,
        target_age_max=ag_max,
        source_name=source_name,
        type=OfferType.permanent if is_permanent_flag else OfferType.event,
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
        location_id=location.id if location else None,
    )
    offer.status = OfferStatus.draft
    db.session.add(offer)
    db.session.flush()
    sync_permanent_availability(db.session, offer)

    categories_raw = (f.get("categories") or f.get("category") or "").strip()
    category_names = [cat.strip() for cat in categories_raw.split(",") if cat.strip()]
    seen_slugs: set[str] = set()
    for cat_name in category_names:
        _AUML = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

        def slugify(s: str) -> str:
            s = s.strip().lower()
            for a, b in _AUML.items():
                s = s.replace(a, b)
            s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
            return s or "kategorie"

        slug = slugify(cat_name)
        if slug in seen_slugs:
            continue
        cat = Category.query.filter_by(slug=slug).first()
        if not cat:
            cat = Category(slug=slug, name=cat_name)
            db.session.add(cat)
            db.session.flush()
        offer.categories.append(cat)
        seen_slugs.add(slug)

    try:
        db.session.commit()
        if interactive_mode:
            return _render_event_form(
                {"form_mode": MODE_MANUAL},
                submission_success=True,
                submitted_title=offer.title,
            )
        flash("Event gespeichert.", "success")
        return redirect(url_for("suchergebnisse"))
    except IntegrityError:
        db.session.rollback()
        if interactive_mode:
            return _render_event_form(
                form_data,
                submission_error="Konnte Event nicht speichern (DB-Fehler).",
            ), 400
        flash("Konnte Event nicht speichern (DB-Fehler).", "danger")
        return redirect(url_for("event_erstellen")), 400


@app.get("/freigeben")
@login_required
def freigeben():
    if not current_user.is_admin:
        abort(403)
    show_from_today = request.args.get("ab_heute") == "1"
    today = datetime.now(timezone.utc).date()
    query = db.session.query(Offer).filter(Offer.status == OfferStatus.draft)
    if show_from_today:
        query = query.filter(
            or_(
                Offer.dt_start.is_(None),
                Offer.dt_start >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            )
        )
    pending_offers = (
        query.order_by(Offer.dt_start.asc().nullslast(), Offer.created_at.asc().nullslast())
        .limit(200)
        .all()
    )
    city_rows = (
        db.session.query(
            CommunityFeedback.city.label("city"),
            func.count(CommunityFeedback.id).label("count"),
            func.max(CommunityFeedback.created_at).label("latest_at"),
        )
        .filter(
            CommunityFeedback.kind == FEEDBACK_KIND_CITY,
            CommunityFeedback.city.isnot(None),
            CommunityFeedback.city != "",
        )
        .group_by(CommunityFeedback.city)
        .order_by(func.count(CommunityFeedback.id).desc(), func.max(CommunityFeedback.created_at).desc())
        .limit(50)
        .all()
    )
    city_requests = [
        {"city": row.city, "count": row.count, "latest_at": row.latest_at}
        for row in city_rows
    ]
    feedback_entries = (
        CommunityFeedback.query
        .filter(CommunityFeedback.kind != FEEDBACK_KIND_CITY)
        .order_by(CommunityFeedback.created_at.desc())
        .limit(15)
        .all()
    )
    def _sort_value(dt_value):
        if not dt_value:
            return float("inf")
        try:
            return dt_value.timestamp()
        except AttributeError:
            return float("inf")

    def _format_short_label(dt_value):
        if not dt_value:
            return "Termin offen"
        localized = dt_value.astimezone()
        return localized.strftime("%d.%m.%Y · %H:%M")

    single_offers: list[Offer] = []
    series_groups: dict[uuid.UUID, list[Offer]] = {}
    for offer in pending_offers:
        series_id = getattr(offer, "recurring_series_id", None)
        if series_id:
            series_groups.setdefault(series_id, []).append(offer)
        else:
            single_offers.append(offer)

    stacked_offers: list[dict[str, Any]] = []
    for series_id, offers in series_groups.items():
        offers_sorted = sorted(offers, key=lambda entry: (_sort_value(entry.dt_start), _sort_value(entry.created_at)))
        if len(offers_sorted) <= 1:
            single_offers.extend(offers_sorted)
            continue
        primary = offers_sorted[0]
        modal_events = [
            {
                "id": str(item.id),
                "title": item.title or "Event",
                "date_label": _format_short_label(item.dt_start),
                "edit_single_url": url_for("edit_event", event_id=item.id, scope="single"),
                "location": item.location.name if item.location else "",
            }
            for item in offers_sorted
        ]
        stacked_offers.append(
            {
                "series_id": str(series_id),
                "count": len(offers_sorted),
                "primary": primary,
                "offers": offers_sorted,
                "preview_dates": [_format_short_label(item.dt_start) for item in offers_sorted[:3]],
                "modal_payload": {
                    "title": primary.title or "Event-Serie",
                    "count": len(offers_sorted),
                    "events": modal_events,
                    "series_edit_url": url_for("edit_event", event_id=primary.id, scope="series"),
                },
            }
        )

    single_offers.sort(key=lambda entry: (_sort_value(entry.dt_start), _sort_value(entry.created_at)))
    stacked_offers.sort(key=lambda entry: (_sort_value(entry["primary"].dt_start), _sort_value(entry["primary"].created_at)))

    return render_template(
        "freigeben.html",
        pending_offers=pending_offers,
        single_offers=single_offers,
        stacked_offers=stacked_offers,
        city_requests=city_requests,
        feedback_entries=feedback_entries,
        feedback_kind_labels=FEEDBACK_KIND_LABELS,
        show_from_today=show_from_today,
    )


@app.post("/freigeben/<uuid:offer_id>/publish")
@login_required
def freigeben_publish(offer_id):
    if not current_user.is_admin:
        abort(403)
    offer = Offer.query.get_or_404(offer_id)
    offer.status = OfferStatus.published
    db.session.commit()
    flash("Event veröffentlicht.", "success")
    return redirect(url_for("freigeben"))


@app.post("/freigeben/publish_bulk")
@login_required
def freigeben_publish_bulk():
    if not current_user.is_admin:
        abort(403)

    raw_ids = request.form.getlist("offer_ids")
    valid_ids: list[uuid.UUID] = []
    for raw in raw_ids:
        try:
            valid_ids.append(uuid.UUID(raw))
        except (ValueError, AttributeError):
            continue

    if not valid_ids:
        flash("Bitte wähle mindestens ein Event aus.", "danger")
        return redirect(url_for("freigeben"))

    offers = (
        Offer.query.filter(
            Offer.id.in_(valid_ids),
            Offer.status == OfferStatus.draft,
        ).all()
    )

    if not offers:
        flash("Die ausgewählten Events konnten nicht veröffentlicht werden.", "danger")
        return redirect(url_for("freigeben"))

    for offer in offers:
        offer.status = OfferStatus.published

    db.session.commit()
    count = len(offers)
    flash(
        f"{count} Event{'s' if count != 1 else ''} veröffentlicht.",
        "success",
    )
    return redirect(url_for("freigeben"))


@app.post("/freigeben/<uuid:offer_id>/archive")
@login_required
def freigeben_archive(offer_id):
    if not current_user.is_admin:
        abort(403)
    offer = Offer.query.get_or_404(offer_id)
    offer.status = OfferStatus.archived
    db.session.commit()
    flash("Event archiviert.", "info")
    return redirect(url_for("freigeben"))

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
    

@app.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    price_id = (request.form.get("priceId") or "").strip()
    if not price_id:
        flash("Kein Preis ausgewählt – bitte versuche es erneut.", "danger")
        return redirect(url_for("preise"))

    plan_key = _plan_key_for_price(price_id)
    if not plan_key:
        flash("Unbekannter Preis – bitte lade die Seite neu und versuche es erneut.", "danger")
        return redirect(url_for("preise"))

    success_url = url_for("checkout_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = url_for("preise", _external=True)

    metadata = {"plan": plan_key, "user_id": str(current_user.id), "price_id": price_id}

    try:
        session = stripe.checkout.Session.create(
            success_url=success_url,
            cancel_url=cancel_url,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            client_reference_id=str(current_user.id),
            customer_email=current_user.email,
            metadata=metadata,
            subscription_data={"metadata": metadata},
            allow_promotion_codes=True,
        )
    except Exception:
        app.logger.exception("Stripe Checkout konnte nicht erstellt werden")
        flash("Checkout konnte nicht gestartet werden. Bitte versuche es später erneut.", "danger")
        return redirect(url_for("preise"))

    return redirect(session.url, code=303)


@app.post("/stripe/webhook")
def stripe_webhook():
    webhook_secret = app.config.get("STRIPE_WEBHOOK_SECRET")
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    if not webhook_secret:
        app.logger.error("STRIPE_WEBHOOK_SECRET nicht gesetzt – Webhook wird ignoriert")
        return ("Webhook nicht konfiguriert", 500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        app.logger.warning("Stripe Webhook konnte nicht geparst werden")
        return ("Ungültige Payload", 400)
    except stripe.error.SignatureVerificationError:
        app.logger.warning("Stripe Webhook Signatur ungültig")
        return ("Ungültige Signatur", 400)

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data_object)
    elif event_type == "invoice.payment_succeeded":
        _handle_invoice_paid(data_object)
    else:
        app.logger.debug("Stripe Webhook ignoriert (%s)", event_type)

    return ("", 200)


# ---------------------------------------------------------------------------
# Lokaler Dev-Start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=True)
