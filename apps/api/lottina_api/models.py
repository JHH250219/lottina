import uuid
from datetime import datetime
from enum import Enum as PyEnum

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy import Index
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

db = SQLAlchemy()

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class OfferStatus(PyEnum):
    draft = "draft"
    published = "published"
    archived = "archived"

class SourceType(PyEnum):
    manual = "manual"
    crawler = "crawler"
    ocr = "ocr"

class OfferType(PyEnum):
    event = "event"         # A: Theater, Sportveranstaltungen
    community = "community" # B: Vereine, Ehrenamt, Tourismuszentren
    permanent = "permanent" # C: Dauerausstellungen, Spielplätze, Minigolf

class DocumentVisibility(PyEnum):
    internal = "internal"
    public = "public"

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
ORGANISATION_ROLE_ADMIN = "organisation_admin"
ORGANISATION_ROLE_MEMBER = "organisation_member"

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, server_default="false")
    created_at = db.Column(db.DateTime, server_default=func.now())
    firstname = db.Column(db.String(80))
    lastname = db.Column(db.String(80))
    city = db.Column(db.String(120))
    profile_image = db.Column(db.String(255))
    is_premium = db.Column(db.Boolean, server_default="false")
    premium_until = db.Column(db.DateTime)
    preferences = db.Column(db.JSON)

    offers = db.relationship("Offer", back_populates="creator", lazy="dynamic")
    organisations = db.relationship(
        "Organizer",
        secondary="organisation_users",
        back_populates="members",
        lazy="dynamic",
    )
    organisation_groups = db.relationship(
        "OrganisationGroup",
        secondary="organisation_group_members",
        back_populates="members",
        lazy="dynamic",
    )
    children = db.relationship(
        "UserChild",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="UserChild.created_at",
        lazy="dynamic",
    )
    favorite_offers = db.relationship(
        "UserFavoriteOffer",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    # Helpers
    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

# ---------------------------------------------------------------------------
# Kategorien & Tags (Many-to-Many)
# ---------------------------------------------------------------------------
offer_categories = db.Table(
    "offer_categories",
    db.Column("offer_id", UUID(as_uuid=True), db.ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("category_id", db.Integer, db.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)

offer_tags = db.Table(
    "offer_tags",
    db.Column("offer_id", UUID(as_uuid=True), db.ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)

    def __repr__(self):
        return f"<Category {self.slug}>"

class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)

    def __repr__(self):
        return f"<Tag {self.name}>"

# ---------------------------------------------------------------------------
# Organizer
# ---------------------------------------------------------------------------
organisation_users = db.Table(
    "organisation_users",
    db.Column("organisation_id", db.Integer, db.ForeignKey("organizers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role", db.String(50), nullable=False, server_default=ORGANISATION_ROLE_ADMIN),
    db.Column("added_at", db.DateTime, server_default=func.now()),
)

organisation_group_members = db.Table(
    "organisation_group_members",
    db.Column("group_id", db.Integer, db.ForeignKey("organisation_groups.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role", db.String(50), nullable=False, server_default=ORGANISATION_ROLE_ADMIN),
    db.Column("added_at", db.DateTime, server_default=func.now()),
)

organisation_group_offers = db.Table(
    "organisation_group_offers",
    db.Column("group_id", db.Integer, db.ForeignKey("organisation_groups.id", ondelete="CASCADE"), primary_key=True),
    db.Column("offer_id", UUID(as_uuid=True), db.ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("assigned_at", db.DateTime, server_default=func.now()),
)


class Organizer(db.Model):
    __tablename__ = "organizers"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, index=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    address = db.Column(db.String(255))
    logo = db.Column(db.String(255))
    organisation_type = db.Column(db.String(80))
    website = db.Column(db.String(200))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))

    offers = db.relationship("Offer", back_populates="organizer", foreign_keys="Offer.organizer_id")
    managed_offers = db.relationship("Offer", back_populates="organisation", foreign_keys="Offer.organisation_id")
    members = db.relationship(
        "User",
        secondary=organisation_users,
        back_populates="organisations",
        lazy="dynamic",
    )
    groups = db.relationship(
        "OrganisationGroup",
        back_populates="organisation",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    invitations = db.relationship(
        "OrganisationInvitation",
        back_populates="organisation",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    folders = db.relationship(
        "OrganisationFolder",
        back_populates="organisation",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    documents = db.relationship(
        "OrganisationDocument",
        back_populates="organisation",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    forms = db.relationship(
        "OrganisationForm",
        back_populates="organisation",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def __repr__(self):
        return f"<Organisation {self.slug or self.name}>"

# ---------------------------------------------------------------------------
# Organisation-Gruppen & Einladungen
# ---------------------------------------------------------------------------
class OrganisationGroup(db.Model):
    __tablename__ = "organisation_groups"

    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=func.now())

    organisation = db.relationship("Organizer", back_populates="groups")
    members = db.relationship(
        "User",
        secondary=organisation_group_members,
        back_populates="organisation_groups",
        lazy="dynamic",
    )
    events = db.relationship(
        "Offer",
        secondary=organisation_group_offers,
        back_populates="groups",
        lazy="dynamic",
    )

    __table_args__ = (
        db.UniqueConstraint("organisation_id", "slug", name="uq_organisation_group_slug"),
    )

    def __repr__(self):
        return f"<OrganisationGroup {self.slug} ({self.organisation_id})>"


class OrganisationFolder(db.Model):
    __tablename__ = "organisation_folders"

    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="CASCADE"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(160), nullable=False)
    created_at = db.Column(db.DateTime, server_default=func.now())

    organisation = db.relationship("Organizer", back_populates="folders")
    documents = db.relationship(
        "OrganisationDocument",
        back_populates="folder_ref",
        lazy="dynamic",
    )

    __table_args__ = (
        db.UniqueConstraint("organisation_id", "slug", name="uq_organisation_folder_slug"),
    )

    def __repr__(self):
        return f"<OrganisationFolder {self.name} ({self.organisation_id})>"


class OrganisationDocument(db.Model):
    __tablename__ = "organisation_documents"

    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="CASCADE"), nullable=False)
    folder_id = db.Column(db.Integer, db.ForeignKey("organisation_folders.id", ondelete="SET NULL"))
    folder = db.Column(db.String(160))
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255))
    mime_type = db.Column(db.String(120))
    file_size = db.Column(db.Integer)
    visibility = db.Column(db.Enum(DocumentVisibility, name="document_visibility"), nullable=False, server_default=DocumentVisibility.internal.value)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, server_default=func.now())

    organisation = db.relationship("Organizer", back_populates="documents")
    folder_ref = db.relationship("OrganisationFolder", back_populates="documents")
    uploaded_by = db.relationship("User")

    def __repr__(self):
        return f"<OrganisationDocument {self.original_filename or self.filename}>"


class OrganisationInvitation(db.Model):
    __tablename__ = "organisation_invitations"

    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="CASCADE"), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    role = db.Column(db.String(50), nullable=False, server_default=ORGANISATION_ROLE_ADMIN)
    token = db.Column(db.String(120), unique=True, nullable=False, default=lambda: uuid.uuid4().hex)
    status = db.Column(db.String(20), nullable=False, server_default="pending")
    invited_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, server_default=func.now())
    accepted_at = db.Column(db.DateTime)

    organisation = db.relationship("Organizer", back_populates="invitations")
    invited_by = db.relationship("User")

    def __repr__(self):
        return f"<OrganisationInvitation {self.email} {self.status}>"


class OrganisationForm(db.Model):
    __tablename__ = "organisation_forms"

    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="CASCADE"), nullable=False)
    slug = db.Column(db.String(160), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    max_participants = db.Column(db.Integer)
    confirmation_message = db.Column(db.Text)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, server_default=func.now(), onupdate=func.now())

    organisation = db.relationship("Organizer", back_populates="forms")
    created_by = db.relationship("User")
    fields = db.relationship(
        "OrganisationFormField",
        back_populates="form",
        order_by="OrganisationFormField.position",
        cascade="all, delete-orphan",
    )
    submissions = db.relationship(
        "OrganisationFormSubmission",
        back_populates="form",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint("organisation_id", "slug", name="uq_organisation_form_slug"),
    )

    def __repr__(self):
        return f"<OrganisationForm {self.title}>"


class OrganisationFormField(db.Model):
    __tablename__ = "organisation_form_fields"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("organisation_forms.id", ondelete="CASCADE"), nullable=False)
    label = db.Column(db.String(200), nullable=False)
    field_type = db.Column(db.String(50), nullable=False, server_default="text")
    required = db.Column(db.Boolean, nullable=False, server_default="false")
    options = db.Column(db.JSON)
    position = db.Column(db.Integer, nullable=False, server_default="0")

    form = db.relationship("OrganisationForm", back_populates="fields")

    def __repr__(self):
        return f"<OrganisationFormField {self.label} ({self.field_type})>"


class OrganisationFormSubmission(db.Model):
    __tablename__ = "organisation_form_submissions"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("organisation_forms.id", ondelete="CASCADE"), nullable=False)
    data = db.Column(db.JSON, nullable=False)
    participant_name = db.Column(db.String(200))
    participant_email = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, server_default=func.now())
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    form = db.relationship("OrganisationForm", back_populates="submissions")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<OrganisationFormSubmission {self.id}>"


# ---------------------------------------------------------------------------
# Community Feedback / Wünsche
# ---------------------------------------------------------------------------
class CommunityFeedback(db.Model):
    __tablename__ = "community_feedback"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200))
    city = db.Column(db.String(160))
    message = db.Column(db.Text)
    kind = db.Column(db.String(40), nullable=False, server_default="feedback")
    source = db.Column(db.String(80))
    status = db.Column(db.String(20), nullable=False, server_default="new")
    created_at = db.Column(db.DateTime, server_default=func.now())

    def __repr__(self):
        return f"<CommunityFeedback {self.kind} {self.id}>"


class UserChild(db.Model):
    __tablename__ = "user_children"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    age = db.Column(db.Integer)
    interests = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, server_default=func.now(), onupdate=func.now())

    user = db.relationship("User", back_populates="children")

    def __repr__(self):
        return f"<UserChild {self.name} ({self.user_id})>"


class UserFavoriteOffer(db.Model):
    __tablename__ = "user_favorite_offers"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    offer_id = db.Column(UUID(as_uuid=True), db.ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=func.now())

    user = db.relationship("User", back_populates="favorite_offers")
    offer = db.relationship("Offer", back_populates="favorited_by")

    def __repr__(self):
        return f"<UserFavoriteOffer user={self.user_id} offer={self.offer_id}>"

# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------
class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.String(64), unique=True, index=True, nullable=True)
    name = db.Column(db.String, nullable=True)
    address = db.Column(db.String, nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lon = db.Column(db.Float, nullable=True)
    city = db.Column(db.String, nullable=True)

    created_at = db.Column(db.DateTime, server_default=func.now())

    offers = db.relationship("Offer", back_populates="location")

    def __repr__(self):
        return f"<Location {self.name or self.address or self.id}>"

# ---------------------------------------------------------------------------
# Offer
# ---------------------------------------------------------------------------
class Offer(db.Model):
    __tablename__ = "offers"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Basis
    title = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text)
    summary = db.Column(db.String(400))

    external_id = db.Column(db.String(64), unique=True, index=True, nullable=False)
    source = db.Column(db.String(64), nullable=False)
    source_url = db.Column(db.Text, nullable=False)

    # Zeit
    dt_start = db.Column(db.DateTime(timezone=True), index=True)
    dt_end = db.Column(db.DateTime(timezone=True))

    # Preis(e)
    price_value = db.Column(db.Numeric(10, 2))
    price_min = db.Column(db.Numeric(10, 2))
    price_max = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(3), server_default="EUR")

    # Medien
    image = db.Column(db.Text)

    # Typ A/B/C
    type = db.Column(db.Enum(OfferType, name="offer_type"), nullable=False, server_default=OfferType.event.value)

    # Öffnungszeiten (für permanent/community, optional)
    opening_hours = db.Column(db.JSON)
    holiday_hours = db.Column(db.JSON)

    # Ort / Flags
    maps_url = db.Column(db.String(500))
    meeting_point = db.Column(db.String(200))
    is_outdoor = db.Column(db.Boolean, server_default="false")
    is_indoor = db.Column(db.Boolean, server_default="true")

    # Zielgruppe
    target_age_min = db.Column(db.Integer)
    target_age_max = db.Column(db.Integer)
    with_accompaniment = db.Column(db.Boolean, server_default="false")

    # Preis-Flags
    is_free = db.Column(db.Boolean, server_default="false")

    # Filter Flags
    hobby_regular = db.Column(db.Boolean, server_default="false")
    is_once = db.Column(db.Boolean, server_default="true")
    is_sporty = db.Column(db.Boolean, server_default="false")
    is_creative = db.Column(db.Boolean, server_default="false")
    pets_allowed = db.Column(db.Boolean, server_default="false")

    # Quelle
    source_name = db.Column(db.String(120))
    source_type = db.Column(db.Enum(SourceType, name="source_type"), nullable=False, server_default=SourceType.manual.value)

    # Meta
    status = db.Column(db.Enum(OfferStatus, name="offer_status"), nullable=False, server_default=OfferStatus.draft.value)
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, server_default=func.now(), onupdate=func.now())

    # Relationen
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    organizer_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="SET NULL"))
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id", ondelete="SET NULL"), index=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organizers.id", ondelete="SET NULL"), index=True)
    is_internal = db.Column(db.Boolean, server_default="false")
    recurring_series_id = db.Column(UUID(as_uuid=True))
    is_recurring = db.Column(db.Boolean, server_default="false")

    creator = db.relationship("User", back_populates="offers")
    organizer = db.relationship("Organizer", back_populates="offers", foreign_keys=[organizer_id])
    location = db.relationship("Location", back_populates="offers")
    organisation = db.relationship("Organizer", foreign_keys=[organisation_id], back_populates="managed_offers")
    groups = db.relationship(
        "OrganisationGroup",
        secondary=organisation_group_offers,
        back_populates="events",
        lazy="dynamic",
    )
    favorited_by = db.relationship(
        "UserFavoriteOffer",
        back_populates="offer",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    # Many-to-Many
    categories = db.relationship("Category", secondary=offer_categories, lazy="joined")
    tags = db.relationship("Tag", secondary=offer_tags, lazy="selectin")

    __table_args__ = (
        Index("idx_offers_filters", "is_free", "is_outdoor", "is_sporty", "is_creative", "pets_allowed"),
        Index("idx_offers_dates", "dt_start", "dt_end"),
    )

    def __repr__(self):
        return f"<Offer {self.title} {self.id}>"
