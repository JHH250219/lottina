"""Add table for permanent offer availability slots.

Revision ID: b7041ef0bc7c
Revises: 9c137fb0165c
Create Date: 2024-05-23 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b7041ef0bc7c"
down_revision = "9c137fb0165c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "offer_availabilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("offer_id", "day", name="uq_offer_availabilities_offer_day"),
    )
    op.create_index("ix_offer_availabilities_day", "offer_availabilities", ["day"], unique=False)
    op.create_index("ix_offer_availabilities_offer_id", "offer_availabilities", ["offer_id"], unique=False)


def downgrade():
    op.drop_index("ix_offer_availabilities_offer_id", table_name="offer_availabilities")
    op.drop_index("ix_offer_availabilities_day", table_name="offer_availabilities")
    op.drop_table("offer_availabilities")
