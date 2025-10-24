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

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, server_default="false")
    created_at = db.Column(db.DateTime, server_default=func.now())

    offers = db.relationship("Offer", back_populates="creator", lazy="dynamic")

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
class Organizer(db.Model):
    __tablename__ = "organizers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    website = db.Column(db.String(200))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))

    offers = db.relationship("Offer", back_populates="organizer")

    def __repr__(self):
        return f"<Organizer {self.name}>"

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

    creator = db.relationship("User", back_populates="offers")
    organizer = db.relationship("Organizer", back_populates="offers")
    location = db.relationship("Location", back_populates="offers")

    # Many-to-Many
    categories = db.relationship("Category", secondary=offer_categories, lazy="joined")
    tags = db.relationship("Tag", secondary=offer_tags, lazy="selectin")

    __table_args__ = (
        Index("idx_offers_filters", "is_free", "is_outdoor", "is_sporty", "is_creative", "pets_allowed"),
        Index("idx_offers_dates", "dt_start", "dt_end"),
    )

    def __repr__(self):
        return f"<Offer {self.title} {self.id}>"
