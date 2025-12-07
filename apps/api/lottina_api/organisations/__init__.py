import csv
import io
import json
import re
import uuid
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    abort,
    request,
    redirect,
    url_for,
    flash,
    current_app,
    send_from_directory,
    Response,
)
from flask_login import login_required, current_user
from sqlalchemy import or_
from flask_mail import Message
from werkzeug.utils import secure_filename

from ..models import (
    Organizer,
    OrganisationGroup,
    OrganisationFolder,
    OrganisationDocument,
    OrganisationInvitation,
    OrganisationForm,
    OrganisationFormField,
    OrganisationFormSubmission,
    Offer,
    OfferStatus,
    OfferType,
    SourceType,
    DocumentVisibility,
    Location,
    User,
    organisation_users,
    ORGANISATION_ROLE_ADMIN,
    ORGANISATION_ROLE_MEMBER,
)
from ..models import db
from ..utils import save_upload, allowed

organisations_bp = Blueprint("organisations", __name__, url_prefix="/organisations")


def _get_org_or_404(slug: str) -> Organizer:
    organisation = Organizer.query.filter_by(slug=slug).first_or_404()
    return organisation


def _require_org_admin(organisation: Organizer) -> None:
    if not current_user.is_authenticated:
        abort(403)
    if current_user.is_admin:
        return
    membership = db.session.execute(
        organisation_users.select().where(
            organisation_users.c.organisation_id == organisation.id,
            organisation_users.c.user_id == current_user.id,
            organisation_users.c.role == ORGANISATION_ROLE_ADMIN,
        )
    ).first()
    if membership is None:
        abort(403)


def _image_folder() -> Path:
    upload_root = Path(current_app.config.get("UPLOAD_FOLDER", "uploads"))
    folder = upload_root / "images"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _parse_datetime(date_str: str | None, time_str: str | None) -> datetime | None:
    if not date_str:
        return None
    time_str = (time_str or "").strip() or "09:00"
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        normalized = value.replace(",", ".")
        return Decimal(normalized)
    except (InvalidOperation, AttributeError):
        return None


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _ensure_location(name: str, address: str | None, city: str | None) -> Location | None:
    if not name:
        return None
    location = Location.query.filter(Location.name.ilike(name)).first()
    if location:
        return location
    location = Location(
        name=name,
        address=address or None,
        city=city or None,
    )
    db.session.add(location)
    db.session.flush()
    return location


def _calendar_payload(events: list[Offer], organisation: Organizer) -> str:
    payload = []
    for event in events:
        payload.append(
            {
                "id": str(event.id),
                "title": event.title or "Ohne Titel",
                "start": event.dt_start.isoformat() if event.dt_start else "",
                "end": event.dt_end.isoformat() if event.dt_end else "",
                "is_internal": bool(event.is_internal),
                "is_recurring": bool(event.is_recurring),
                "url": url_for(
                    "organisations.organisation_event_detail",
                    slug=organisation.slug,
                    event_id=event.id,
                ),
            }
        )
    return json.dumps(payload)


_GROUP_SLUG_REPLACEMENTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
DOC_ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}
DEFAULT_FOLDER_NAME = "Allgemein"


def _slugify_value(value: str | None, fallback: str = "eintrag") -> str:
    value = (value or "").strip().lower()
    for src, dst in _GROUP_SLUG_REPLACEMENTS.items():
        value = value.replace(src, dst)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or fallback


def _slugify_group(value: str | None, fallback: str = "gruppe") -> str:
    return _slugify_value(value, fallback=fallback)


def _unique_group_slug(organisation: Organizer, name: str, exclude_id: int | None = None) -> str:
    base = _slugify_group(name)
    slug = base
    counter = 2
    while True:
        query = OrganisationGroup.query.filter(
            OrganisationGroup.organisation_id == organisation.id,
            OrganisationGroup.slug == slug,
        )
        if exclude_id:
            query = query.filter(OrganisationGroup.id != exclude_id)
        if not query.first():
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def _get_group_or_404(organisation: Organizer, group_slug: str) -> OrganisationGroup:
    return (
        OrganisationGroup.query.filter(
            OrganisationGroup.organisation_id == organisation.id,
            OrganisationGroup.slug == group_slug,
        )
        .first_or_404()
    )


INVITE_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _organisation_documents_root(organisation: Organizer) -> Path:
    upload_root = Path(current_app.config.get("UPLOAD_FOLDER", "uploads"))
    root = upload_root / "organisation" / str(organisation.id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unique_folder_slug(organisation: Organizer, name: str) -> str:
    base = _slugify_value(name, fallback="ordner")
    slug = base
    counter = 2
    while True:
        exists = OrganisationFolder.query.filter(
            OrganisationFolder.organisation_id == organisation.id,
            OrganisationFolder.slug == slug,
        ).first()
        if not exists:
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def _ensure_folder(organisation: Organizer, name: str) -> OrganisationFolder:
    slug = _slugify_value(name, fallback="ordner")
    folder = OrganisationFolder.query.filter_by(organisation_id=organisation.id, slug=slug).first()
    if folder:
        return folder
    folder = OrganisationFolder(
        organisation_id=organisation.id,
        name=name,
        slug=_unique_folder_slug(organisation, name),
    )
    db.session.add(folder)
    db.session.commit()
    return folder


def _ensure_default_folder(organisation: Organizer) -> OrganisationFolder:
    default_slug = _slugify_value(DEFAULT_FOLDER_NAME, fallback="allgemein")
    folder = OrganisationFolder.query.filter_by(organisation_id=organisation.id, slug=default_slug).first()
    if folder:
        return folder
    folder = OrganisationFolder(
        organisation_id=organisation.id,
        name=DEFAULT_FOLDER_NAME,
        slug=default_slug,
    )
    db.session.add(folder)
    db.session.commit()
    return folder


def _get_folder_by_slug(organisation: Organizer, slug: str | None) -> OrganisationFolder | None:
    if not slug:
        return None
    return OrganisationFolder.query.filter_by(organisation_id=organisation.id, slug=slug).first()


def _unique_form_slug(organisation: Organizer, title: str, exclude_id: int | None = None) -> str:
    base = _slugify_value(title, fallback="formular")
    slug = base
    counter = 2
    while True:
        query = OrganisationForm.query.filter(
            OrganisationForm.organisation_id == organisation.id,
            OrganisationForm.slug == slug,
        )
        if exclude_id:
            query = query.filter(OrganisationForm.id != exclude_id)
        if not query.first():
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def _get_form_or_404(organisation: Organizer, form_slug: str) -> OrganisationForm:
    return (
        OrganisationForm.query.filter(
            OrganisationForm.organisation_id == organisation.id,
            OrganisationForm.slug == form_slug,
        )
        .first_or_404()
    )


def _allowed_document(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in DOC_ALLOWED_EXTENSIONS


def _send_invite_email(organisation: Organizer, invitation) -> None:
    mail = current_app.extensions.get("mail")
    if not mail:
        return
    invite_url = url_for("register", _external=True)
    try:
        msg = Message(
            subject=f"Einladung zur Organisation {organisation.name}",
            recipients=[invitation.email],
        )
        msg.body = (
            f"Hallo!\n\n"
            f"Du wurdest eingeladen, bei {organisation.name} auf lottina mitzuarbeiten.\n"
            f"Registriere dich oder logge dich ein, um die Einladung anzunehmen: {invite_url}\n\n"
            f"Viele Grüße\nlottina.de"
        )
        mail.send(msg)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Invite email could not be sent: %s", exc)


def _send_form_confirmation(form: OrganisationForm, submission: OrganisationFormSubmission) -> None:
    if not submission.participant_email:
        return
    mail = current_app.extensions.get("mail")
    if not mail:
        return
    message_template = form.confirmation_message or (
        f"Hallo {submission.participant_name or ''},\n\n"
        f"deine Anmeldung für \"{form.title}\" wurde gespeichert.\n"
        f"Wir melden uns bei dir mit weiteren Infos.\n\n"
        f"Viele Grüße\n{form.organisation.name}"
    )
    try:
        msg = Message(
            subject=f"Bestätigung: {form.title}",
            recipients=[submission.participant_email],
        )
        msg.body = message_template
        mail.send(msg)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Form confirmation email failed: %s", exc)


@organisations_bp.route("/<slug>/dashboard")
@login_required
def organisation_dashboard(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    events = organisation.managed_offers
    return render_template(
        "organisations/dashboard.html",
        organisation=organisation,
        events=events,
        active_tab="dashboard",
    )


@organisations_bp.route("/<slug>/events")
@login_required
def organisation_events(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)

    view_mode = request.args.get("view", "list")
    if view_mode not in {"list", "calendar"}:
        view_mode = "list"
    visibility = request.args.get("visibility", "all")

    events_query = Offer.query.filter(Offer.organisation_id == organisation.id)
    if visibility == "internal":
        events_query = events_query.filter(Offer.is_internal.is_(True))
    elif visibility == "public":
        events_query = events_query.filter(
            or_(Offer.is_internal.is_(False), Offer.is_internal.is_(None))
        )

    events = events_query.order_by(Offer.dt_start.asc(), Offer.created_at.desc()).all()
    calendar_events = _calendar_payload(events, organisation)

    return render_template(
        "organisations/events.html",
        organisation=organisation,
        events=events,
        active_tab="events",
        view_mode=view_mode,
        filter_visibility=visibility,
        calendar_events_json=calendar_events,
    )


@organisations_bp.route("/<slug>/events/<uuid:event_id>")
@login_required
def organisation_event_detail(slug, event_id):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    event = (
        Offer.query.filter(
            Offer.id == event_id,
            Offer.organisation_id == organisation.id,
        )
        .first_or_404()
    )
    return render_template(
        "organisations/event_detail.html",
        organisation=organisation,
        event=event,
        active_tab="events",
    )


@organisations_bp.route("/<slug>/events/new", methods=["GET", "POST"])
@login_required
def organisation_events_new(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)

    today_str = datetime.now().strftime("%Y-%m-%d")
    form_defaults = {
        "title": "",
        "summary": "",
        "description": "",
        "start_date": today_str,
        "start_time": "10:00",
        "end_date": "",
        "end_time": "",
        "location_name": "",
        "location_address": "",
        "location_city": "",
        "maps_url": "",
        "price": "",
        "contact": organisation.email or current_user.email or "",
        "visibility": "public",
        "is_outdoor": "",
        "is_indoor": "",
        "repeat_pattern": "none",
        "repeat_until": "",
    }
    form_data = dict(form_defaults)
    errors: list[str] = []

    if request.method == "POST":
        for key in form_defaults:
            if key in {"is_outdoor", "is_indoor"}:
                form_data[key] = "on" if request.form.get(key) else ""
                continue
            form_data[key] = (request.form.get(key) or "").strip()

        title = form_data["title"]
        summary = form_data["summary"]
        description = form_data["description"]
        start_date = form_data["start_date"]
        start_time = form_data["start_time"]
        end_date = form_data["end_date"] or start_date
        end_time = form_data["end_time"] or ""
        location_name = form_data["location_name"]
        location_address = form_data["location_address"]
        location_city = form_data["location_city"]
        maps_url = form_data["maps_url"]
        price_value = _parse_decimal(form_data["price"])
        contact = form_data["contact"]
        visibility_choice = form_data["visibility"] or "public"
        repeat_pattern = form_data["repeat_pattern"] or "none"
        repeat_until_str = form_data["repeat_until"]

        if not title:
            errors.append("Bitte gib dem Event einen Titel.")
        start_dt = _parse_datetime(start_date, start_time)
        if not start_dt:
            errors.append("Bitte wähle ein gültiges Startdatum und eine Startzeit.")
        end_dt = _parse_datetime(end_date, end_time) if end_date else None
        if start_dt and not end_dt and end_time:
            end_dt = _parse_datetime(start_date, end_time)
        if start_dt and end_dt and end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=2)
        if start_dt and not end_dt:
            end_dt = start_dt + timedelta(hours=2)

        is_internal = visibility_choice == "internal"
        is_outdoor = bool(form_data["is_outdoor"])
        is_indoor = bool(form_data["is_indoor"])

        repeat_until_date = None
        if repeat_pattern != "none":
            if not repeat_until_str:
                errors.append("Bitte gib ein Enddatum für die Wiederholung an.")
            else:
                try:
                    repeat_until_date = datetime.strptime(repeat_until_str, "%Y-%m-%d").date()
                    if start_dt and repeat_until_date < start_dt.date():
                        errors.append("Das Wiederholungs-Enddatum muss nach dem Startdatum liegen.")
                except Exception:
                    errors.append("Das Wiederholungs-Enddatum ist ungültig.")

        image_file = request.files.get("image_file")

        if errors:
            return (
                render_template(
                    "organisations/events_new.html",
                    organisation=organisation,
                    active_tab="events_new",
                    form_data=form_data,
                    errors=errors,
                ),
                400,
            )

        image_url = None
        if image_file and image_file.filename:
            if not allowed(image_file.filename):
                errors.append("Bitte nutze PNG, JPG, JPEG oder WEBP als Bildformat.")
            else:
                saved = save_upload(image_file, _image_folder())
                image_url = f"/uploads/images/{saved.name}"

        if errors:
            return (
                render_template(
                    "organisations/events_new.html",
                    organisation=organisation,
                    active_tab="events_new",
                    form_data=form_data,
                    errors=errors,
                ),
                400,
            )

        location = _ensure_location(location_name, location_address, location_city)
        occurrences: list[tuple[datetime | None, datetime | None]] = []
        if start_dt:
            occurrences.append((start_dt, end_dt))
            if repeat_pattern != "none" and repeat_until_date:
                duration = (end_dt - start_dt) if (start_dt and end_dt) else None
                next_start = start_dt
                next_end = end_dt
                while True:
                    if repeat_pattern == "weekly":
                        next_start = next_start + timedelta(weeks=1)
                        next_end = next_end + timedelta(weeks=1) if next_end else None
                    else:
                        next_start = _add_months(next_start, 1)
                        next_end = _add_months(next_end, 1) if next_end else None
                    if duration and not next_end:
                        next_end = next_start + duration
                    if next_start.date() > repeat_until_date:
                        break
                    occurrences.append((next_start, next_end))
        else:
            occurrences.append((None, None))

        is_recurring = len(occurrences) > 1
        series_id = uuid.uuid4() if is_recurring else None
        created_events: list[Offer] = []
        try:
            for occ_start, occ_end in occurrences:
                external_id = f"org-{organisation.id}-{uuid.uuid4().hex}"
                offer = Offer(
                    title=title,
                    summary=summary or None,
                    description=description or None,
                    dt_start=occ_start,
                    dt_end=occ_end,
                    price_value=price_value,
                    price_min=price_value,
                    price_max=price_value,
                    is_free=True if price_value is None or price_value == 0 else False,
                    image=image_url,
                    maps_url=maps_url or None,
                    source="organisation",
                    source_url=f"organisation://{organisation.slug}/{external_id}",
                    source_name=contact or organisation.name,
                    source_type=SourceType.manual,
                    external_id=external_id,
                    type=OfferType.event,
                    status=OfferStatus.draft,
                    created_by_user_id=current_user.id,
                    organizer_id=organisation.id,
                    organisation_id=organisation.id,
                    location_id=location.id if location else None,
                    is_internal=is_internal,
                    is_recurring=is_recurring,
                    recurring_series_id=series_id if is_recurring else None,
                    is_once=not is_recurring,
                    is_outdoor=is_outdoor,
                    is_indoor=is_indoor,
                )
                created_events.append(offer)
                db.session.add(offer)
            db.session.commit()
            flash(f"{len(created_events)} Event(s) wurden angelegt.", "success")
            return redirect(url_for("organisations.organisation_events", slug=organisation.slug))
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.exception("Organisation event creation failed: %s", exc)
            errors.append("Die Events konnten nicht gespeichert werden. Bitte versuche es erneut.")

    return render_template(
        "organisations/events_new.html",
        organisation=organisation,
        active_tab="events_new",
        form_data=form_data,
        errors=errors,
    )


@organisations_bp.route("/<slug>/groups", methods=["GET", "POST"])
@login_required
def organisation_groups(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    errors: list[str] = []

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            if not name:
                errors.append("Bitte gib einen Gruppennamen an.")
            else:
                slug_value = _unique_group_slug(organisation, name)
                group = OrganisationGroup(
                    organisation_id=organisation.id,
                    name=name,
                    slug=slug_value,
                    description=description or None,
                )
                db.session.add(group)
                db.session.commit()
                flash("Gruppe angelegt.", "success")
                return redirect(url_for("organisations.organisation_groups", slug=organisation.slug))
        elif action == "rename":
            group_id = request.form.get("group_id", type=int)
            new_name = (request.form.get("name") or "").strip()
            if not group_id or not new_name:
                errors.append("Ungültige Angaben für die Umbenennung.")
            else:
                group = OrganisationGroup.query.filter_by(
                    id=group_id,
                    organisation_id=organisation.id,
                ).first()
                if not group:
                    errors.append("Gruppe nicht gefunden.")
                else:
                    group.name = new_name
                    group.slug = _unique_group_slug(organisation, new_name, exclude_id=group.id)
                    db.session.commit()
                    flash("Gruppe aktualisiert.", "success")
                    return redirect(url_for("organisations.organisation_groups", slug=organisation.slug))
        elif action == "delete":
            group_id = request.form.get("group_id", type=int)
            group = OrganisationGroup.query.filter_by(
                id=group_id,
                organisation_id=organisation.id,
            ).first()
            if not group:
                errors.append("Gruppe konnte nicht gefunden werden.")
            else:
                db.session.delete(group)
                db.session.commit()
                flash("Gruppe gelöscht.", "success")
                return redirect(url_for("organisations.organisation_groups", slug=organisation.slug))

    groups = organisation.groups.order_by(OrganisationGroup.name.asc()).all()
    return render_template(
        "organisations/groups.html",
        organisation=organisation,
        groups=groups,
        active_tab="groups",
        errors=errors,
    )


@organisations_bp.route("/<slug>/groups/<group_slug>", methods=["GET", "POST"])
@login_required
def organisation_group_detail(slug, group_slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    group = _get_group_or_404(organisation, group_slug)
    errors: list[str] = []

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add_member":
                user_id = request.form.get("user_id", type=int)
                if not user_id:
                    errors.append("Bitte wähle ein Mitglied aus.")
                else:
                    user = organisation.members.filter(User.id == user_id).first()
                    if not user:
                        errors.append("Nutzer gehört nicht zur Organisation.")
                    elif group.members.filter(User.id == user.id).first():
                        errors.append("Mitglied ist bereits in dieser Gruppe.")
                    else:
                        group.members.append(user)
                        db.session.commit()
                        flash("Mitglied hinzugefügt.", "success")
                        return redirect(url_for("organisations.organisation_group_detail", slug=organisation.slug, group_slug=group.slug))
            elif action == "remove_member":
                user_id = request.form.get("user_id", type=int)
                member = group.members.filter(User.id == user_id).first()
                if not member:
                    errors.append("Mitglied nicht gefunden.")
                else:
                    group.members.remove(member)
                    db.session.commit()
                    flash("Mitglied entfernt.", "success")
                    return redirect(url_for("organisations.organisation_group_detail", slug=organisation.slug, group_slug=group.slug))
            elif action == "add_event":
                event_id_str = request.form.get("event_id")
                if not event_id_str:
                    errors.append("Bitte wähle ein Event aus.")
                else:
                    try:
                        event_uuid = uuid.UUID(event_id_str)
                    except ValueError:
                        errors.append("Ungültiges Event.")
                    else:
                        event = (
                            Offer.query.filter(
                                Offer.id == event_uuid,
                                Offer.organisation_id == organisation.id,
                            )
                            .first()
                        )
                        if not event:
                            errors.append("Event gehört nicht zur Organisation.")
                        elif group.events.filter(Offer.id == event.id).first():
                            errors.append("Event ist bereits zugeordnet.")
                        else:
                            group.events.append(event)
                            db.session.commit()
                            flash("Event hinzugefügt.", "success")
                            return redirect(url_for("organisations.organisation_group_detail", slug=organisation.slug, group_slug=group.slug))
            elif action == "remove_event":
                event_id_str = request.form.get("event_id")
                try:
                    event_uuid = uuid.UUID(event_id_str)
                except Exception:
                    errors.append("Event konnte nicht entfernt werden.")
                else:
                    event = group.events.filter(Offer.id == event_uuid).first()
                    if not event:
                        errors.append("Event nicht gefunden.")
                    else:
                        group.events.remove(event)
                        db.session.commit()
                        flash("Event entfernt.", "success")
                        return redirect(url_for("organisations.organisation_group_detail", slug=organisation.slug, group_slug=group.slug))
            elif action == "update_meta":
                new_name = (request.form.get("name") or "").strip()
                new_description = (request.form.get("description") or "").strip()
                if not new_name:
                    errors.append("Der Name darf nicht leer sein.")
                else:
                    group.name = new_name
                    group.slug = _unique_group_slug(organisation, new_name, exclude_id=group.id)
                    group.description = new_description or None
                    db.session.commit()
                    flash("Gruppe aktualisiert.", "success")
                    return redirect(url_for("organisations.organisation_group_detail", slug=organisation.slug, group_slug=group.slug))
        except Exception as exc:  # noqa: BLE001
            current_app.logger.exception("Group update failed: %s", exc)
            db.session.rollback()
            errors.append("Aktion fehlgeschlagen. Bitte erneut versuchen.")

    group_members = group.members.order_by(User.username.asc()).all()
    all_members = organisation.members.order_by(User.username.asc()).all()
    available_members = [member for member in all_members if member.id not in {gm.id for gm in group_members}]

    group_events = group.events.order_by(Offer.dt_start.asc(), Offer.created_at.desc()).all()
    org_events = (
        Offer.query.filter(Offer.organisation_id == organisation.id)
        .order_by(Offer.dt_start.desc(), Offer.created_at.desc())
        .all()
    )
    assigned_ids = {event.id for event in group_events}
    available_events = [event for event in org_events if event.id not in assigned_ids]

    return render_template(
        "organisations/group_detail.html",
        organisation=organisation,
        group=group,
        group_members=group_members,
        available_members=available_members,
        group_events=group_events,
        available_events=available_events,
        errors=errors,
        active_tab="groups",
    )


@organisations_bp.route("/<slug>/members", methods=["GET", "POST"])
@login_required
def organisation_members(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    errors: list[str] = []

    if request.method == "POST":
        action = request.form.get("action")
        if action == "invite":
            email = (request.form.get("email") or "").strip().lower()
            if not email or not INVITE_EMAIL_RX.match(email):
                errors.append("Bitte gib eine gültige E-Mail-Adresse ein.")
            else:
                existing_member = organisation.members.filter(User.email.ilike(email)).first()
                pending_invite = organisation.invitations.filter(
                    OrganisationInvitation.email == email,
                    OrganisationInvitation.status == "pending",
                ).first()
                if existing_member:
                    errors.append("Diese E-Mail gehört bereits zu einem Mitglied.")
                elif pending_invite:
                    errors.append("Für diese E-Mail existiert bereits eine Einladung.")
                else:
                    # TODO: Invitation acceptance flow (token validation + route) noch implementieren,
                    # damit eingeladene Personen selbstständig beitreten können.
                    invitation = OrganisationInvitation(
                        organisation_id=organisation.id,
                        email=email,
                        invited_by=current_user,
                        role=ORGANISATION_ROLE_MEMBER,
                    )
                    db.session.add(invitation)
                    db.session.commit()
                    _send_invite_email(organisation, invitation)
                    flash("Einladung verschickt.", "success")
                    return redirect(url_for("organisations.organisation_members", slug=organisation.slug))
        elif action == "remove_member":
            user_id = request.form.get("user_id", type=int)
            if not user_id:
                errors.append("Mitglied konnte nicht entfernt werden.")
            else:
                db.session.execute(
                    organisation_users.delete().where(
                        organisation_users.c.organisation_id == organisation.id,
                        organisation_users.c.user_id == user_id,
                    )
                )
                db.session.commit()
                flash("Mitglied entfernt.", "success")
                return redirect(url_for("organisations.organisation_members", slug=organisation.slug))
        elif action in {"make_admin", "remove_admin"}:
            user_id = request.form.get("user_id", type=int)
            if not user_id:
                errors.append("Mitglied nicht gefunden.")
            else:
                new_role = ORGANISATION_ROLE_ADMIN if action == "make_admin" else ORGANISATION_ROLE_MEMBER
                db.session.execute(
                    organisation_users.update()
                    .where(
                        organisation_users.c.organisation_id == organisation.id,
                        organisation_users.c.user_id == user_id,
                    )
                    .values(role=new_role)
                )
                db.session.commit()
                flash("Rolle aktualisiert.", "success")
                return redirect(url_for("organisations.organisation_members", slug=organisation.slug))
        elif action == "cancel_invite":
            invite_id = request.form.get("invite_id", type=int)
            invitation = organisation.invitations.filter(
                OrganisationInvitation.id == invite_id,
                OrganisationInvitation.status == "pending",
            ).first()
            if not invitation:
                errors.append("Einladung konnte nicht gefunden werden.")
            else:
                db.session.delete(invitation)
                db.session.commit()
                flash("Einladung zurückgezogen.", "success")
                return redirect(url_for("organisations.organisation_members", slug=organisation.slug))

    member_rows = organisation.members.order_by(User.username.asc()).all()
    role_rows = db.session.execute(
        organisation_users.select().where(organisation_users.c.organisation_id == organisation.id)
    ).fetchall()
    role_map = {row.user_id: row.role for row in role_rows}
    members = [
        {
            "user": member,
            "role": role_map.get(member.id, ORGANISATION_ROLE_MEMBER),
        }
        for member in member_rows
    ]
    invitations = organisation.invitations.order_by(OrganisationInvitation.created_at.desc()).all()
    return render_template(
        "organisations/members.html",
        organisation=organisation,
        members=members,
        invitations=invitations,
        errors=errors,
        active_tab="members",
    )


@organisations_bp.route("/<slug>/documents", methods=["GET", "POST"])
@login_required
def organisation_documents(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    _ensure_default_folder(organisation)
    errors: list[str] = []
    folder_param = request.args.get("folder")
    active_folder = _get_folder_by_slug(organisation, folder_param)

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create_folder":
                folder_name = (request.form.get("folder_name") or "").strip()
                if not folder_name:
                    errors.append("Der Ordnername darf nicht leer sein.")
                else:
                    _ensure_folder(organisation, folder_name)
                    flash("Ordner erstellt.", "success")
                    return redirect(url_for("organisations.organisation_documents", slug=organisation.slug, folder=folder_param))
            elif action == "upload_file":
                folder_slug = request.form.get("folder_slug") or (active_folder.slug if active_folder else None)
                folder = _get_folder_by_slug(organisation, folder_slug) or _ensure_default_folder(organisation)
                visibility_value = (request.form.get("visibility") or DocumentVisibility.internal.value).strip()
                try:
                    visibility = DocumentVisibility(visibility_value)
                except ValueError:
                    visibility = DocumentVisibility.internal
                file = request.files.get("file")
                if not file or not file.filename:
                    errors.append("Bitte wähle eine Datei aus.")
                elif not _allowed_document(file.filename):
                    errors.append("Erlaubt sind PDF oder Bilddateien (PNG, JPG, JPEG, WEBP).")
                else:
                    documents_root = _organisation_documents_root(organisation)
                    stored_name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
                    file_path = documents_root / stored_name
                    file.save(file_path)
                    size = file_path.stat().st_size if file_path.exists() else None
                    document = OrganisationDocument(
                        organisation_id=organisation.id,
                        folder_id=folder.id,
                        folder=folder.name,
                        filename=stored_name,
                        original_filename=file.filename,
                        mime_type=file.mimetype,
                        file_size=size,
                        visibility=visibility,
                        uploaded_by_user_id=current_user.id,
                    )
                    db.session.add(document)
                    db.session.commit()
                    flash("Datei hochgeladen.", "success")
                    return redirect(url_for("organisations.organisation_documents", slug=organisation.slug, folder=folder.slug))
            elif action == "delete_file":
                document_id = request.form.get("document_id", type=int)
                document = OrganisationDocument.query.filter_by(id=document_id, organisation_id=organisation.id).first()
                if not document:
                    errors.append("Datei konnte nicht gefunden werden.")
                else:
                    doc_folder = _organisation_documents_root(organisation)
                    file_path = doc_folder / document.filename
                    try:
                        if file_path.exists():
                            file_path.unlink()
                    except Exception:
                        current_app.logger.warning("Konnte Datei nicht löschen: %s", file_path)
                    db.session.delete(document)
                    db.session.commit()
                    flash("Datei gelöscht.", "success")
                    return redirect(url_for("organisations.organisation_documents", slug=organisation.slug, folder=folder_param))
        except Exception as exc:  # noqa: BLE001
            current_app.logger.exception("Document action failed: %s", exc)
            db.session.rollback()
            errors.append("Aktion fehlgeschlagen. Bitte erneut versuchen.")

    folders = organisation.folders.order_by(OrganisationFolder.name.asc()).all()
    if not folders:
        folders = [ _ensure_default_folder(organisation) ]
    if folder_param and not active_folder:
        active_folder = folders[0]
    documents_query = OrganisationDocument.query.filter(OrganisationDocument.organisation_id == organisation.id)
    if active_folder:
        documents_query = documents_query.filter(OrganisationDocument.folder_id == active_folder.id)
    documents = documents_query.order_by(OrganisationDocument.created_at.desc()).all()

    visibility_options = [
        (DocumentVisibility.public.value, "Öffentlich"),
        (DocumentVisibility.internal.value, "Intern"),
    ]

    return render_template(
        "organisations/documents.html",
        organisation=organisation,
        folders=folders,
        documents=documents,
        errors=errors,
        active_tab="documents",
        active_folder_slug=active_folder.slug if active_folder else None,
        visibility_options=visibility_options,
    )


@organisations_bp.route("/<slug>/forms")
@login_required
def organisation_forms(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    forms = (
        OrganisationForm.query.filter_by(organisation_id=organisation.id)
        .order_by(OrganisationForm.created_at.desc())
        .all()
    )
    return render_template(
        "organisations/forms.html",
        organisation=organisation,
        forms=forms,
        active_tab="forms",
    )


@organisations_bp.route("/<slug>/forms/new", methods=["GET", "POST"])
@login_required
def organisation_forms_new(slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    form_data = {
        "title": "",
        "description": "",
        "max_participants": "",
        "confirmation_message": "",
    }
    field_rows: list[dict[str, str]] = [{"label": "", "type": "text", "required": "1", "options": ""}]
    errors: list[str] = []

    if request.method == "POST":
        for key in form_data:
            form_data[key] = (request.form.get(key) or "").strip()
        labels = request.form.getlist("field_label[]")
        types = request.form.getlist("field_type[]")
        required_flags = request.form.getlist("field_required[]")
        options_list = request.form.getlist("field_options[]")
        field_rows = []
        for idx, label in enumerate(labels):
            field_rows.append(
                {
                    "label": label,
                    "type": types[idx] if idx < len(types) else "text",
                    "required": "1" if (idx < len(required_flags) and required_flags[idx] == "1") else "",
                    "options": options_list[idx] if idx < len(options_list) else "",
                }
            )
        if not form_data["title"]:
            errors.append("Bitte gib dem Formular einen Titel.")
        cleaned_fields = []
        allowed_types = {"text", "email", "textarea", "select"}
        for idx, row in enumerate(field_rows):
            label = (row.get("label") or "").strip()
            field_type = (row.get("type") or "text").strip().lower()
            if not label:
                errors.append(f"Feld {idx + 1}: Bitte ein Label angeben.")
                continue
            if field_type not in allowed_types:
                field_type = "text"
            options = []
            if field_type == "select":
                options = [opt.strip() for opt in (row.get("options") or "").split(",") if opt.strip()]
                if not options:
                    errors.append(f"Feld {idx + 1}: Bitte Optionen für das Auswahlfeld angeben.")
            cleaned_fields.append(
                {
                    "label": label,
                    "type": field_type,
                    "required": row.get("required") == "1",
                    "options": options,
                }
            )
        if not cleaned_fields:
            errors.append("Bitte füge mindestens ein Feld hinzu.")
        try:
            max_participants = int(form_data["max_participants"]) if form_data["max_participants"] else None
            if max_participants is not None and max_participants < 1:
                errors.append("Maximale Teilnehmerzahl muss größer als 0 sein.")
        except ValueError:
            errors.append("Maximale Teilnehmerzahl muss eine Zahl sein.")
            max_participants = None

        if not errors:
            try:
                slug_value = _unique_form_slug(organisation, form_data["title"])
                form = OrganisationForm(
                    organisation_id=organisation.id,
                    slug=slug_value,
                    title=form_data["title"],
                    description=form_data["description"] or None,
                    confirmation_message=form_data["confirmation_message"] or None,
                    max_participants=max_participants,
                    created_by_user_id=current_user.id,
                )
                db.session.add(form)
                db.session.flush()
                for position, field in enumerate(cleaned_fields):
                    db.session.add(
                        OrganisationFormField(
                            form_id=form.id,
                            label=field["label"],
                            field_type=field["type"],
                            required=field["required"],
                            options=field["options"],
                            position=position,
                        )
                    )
                db.session.commit()
                flash("Formular angelegt.", "success")
                return redirect(url_for("organisations.organisation_form_detail", slug=organisation.slug, form_slug=form.slug))
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                current_app.logger.exception("Form creation failed: %s", exc)
                errors.append("Das Formular konnte nicht gespeichert werden.")

    return render_template(
        "organisations/form_create.html",
        organisation=organisation,
        form_data=form_data,
        field_rows=field_rows,
        errors=errors,
        active_tab="forms",
    )


@organisations_bp.route("/<slug>/forms/<form_slug>", methods=["GET", "POST"])
@login_required
def organisation_form_detail(slug, form_slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    form = _get_form_or_404(organisation, form_slug)
    errors: list[str] = []

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_submission":
            submission_count = len(form.submissions)
            if form.max_participants and submission_count >= form.max_participants:
                errors.append("Die maximale Teilnehmerzahl wurde erreicht.")
            else:
                answers = {}
                participant_name = ""
                participant_email = ""
                for field in form.fields:
                    field_name = f"field_{field.id}"
                    value = (request.form.get(field_name) or "").strip()
                    if field.required and not value:
                        errors.append(f"{field.label}: Dieses Feld ist erforderlich.")
                    answers[field.label] = value
                    if not participant_name and field.field_type in {"text", "textarea"}:
                        participant_name = value
                    if not participant_email and field.field_type == "email":
                        participant_email = value
                if not errors:
                    submission = OrganisationFormSubmission(
                        form_id=form.id,
                        data=answers,
                        participant_name=participant_name or None,
                        participant_email=participant_email or None,
                        created_by_user_id=current_user.id,
                    )
                    db.session.add(submission)
                    db.session.commit()
                    _send_form_confirmation(form, submission)
                    flash("Teilnehmer hinzugefügt.", "success")
                    return redirect(url_for("organisations.organisation_form_detail", slug=organisation.slug, form_slug=form.slug))
        elif action == "delete_submission":
            submission_id = request.form.get("submission_id", type=int)
            submission = OrganisationFormSubmission.query.filter_by(id=submission_id, form_id=form.id).first()
            if not submission:
                errors.append("Teilnahme konnte nicht gefunden werden.")
            else:
                db.session.delete(submission)
                db.session.commit()
                flash("Teilnehmer entfernt.", "success")
                return redirect(url_for("organisations.organisation_form_detail", slug=organisation.slug, form_slug=form.slug))

    submissions = (
        OrganisationFormSubmission.query.filter_by(form_id=form.id)
        .order_by(OrganisationFormSubmission.created_at.desc())
        .all()
    )
    remaining_slots = None
    if form.max_participants:
        remaining_slots = max(form.max_participants - len(submissions), 0)
    return render_template(
        "organisations/form_detail.html",
        organisation=organisation,
        form=form,
        submissions=submissions,
        errors=errors,
        remaining_slots=remaining_slots,
        active_tab="forms",
    )


@organisations_bp.route("/<slug>/forms/<form_slug>/export.csv")
@login_required
def organisation_form_export(slug, form_slug):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    form = _get_form_or_404(organisation, form_slug)
    submissions = (
        OrganisationFormSubmission.query.filter_by(form_id=form.id)
        .order_by(OrganisationFormSubmission.created_at.asc())
        .all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    header = ["Datum", "Name", "E-Mail"] + [field.label for field in form.fields]
    writer.writerow(header)
    for submission in submissions:
        row = [
            submission.created_at.strftime("%d.%m.%Y %H:%M") if submission.created_at else "",
            submission.participant_name or "",
            submission.participant_email or "",
        ]
        for field in form.fields:
            row.append((submission.data or {}).get(field.label, ""))
        writer.writerow(row)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{form.slug}-export.csv"'},
    )

@organisations_bp.route("/<slug>/documents/<int:doc_id>/download")
@login_required
def organisation_document_download(slug, doc_id):
    organisation = _get_org_or_404(slug)
    _require_org_admin(organisation)
    document = OrganisationDocument.query.filter_by(id=doc_id, organisation_id=organisation.id).first_or_404()
    root = _organisation_documents_root(organisation)
    file_path = root / document.filename
    if not file_path.exists():
        abort(404)
    download_name = document.original_filename or document.filename
    return send_from_directory(
        str(root),
        document.filename,
        as_attachment=True,
        download_name=download_name,
        mimetype=document.mime_type or "application/octet-stream",
    )
