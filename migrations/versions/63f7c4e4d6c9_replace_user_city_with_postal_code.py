"""Switch users.city to postal_code.

Revision ID: 63f7c4e4d6c9
Revises: 7f65c6d6d2c5
Create Date: 2024-06-05 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "63f7c4e4d6c9"
down_revision = "7f65c6d6d2c5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("postal_code", sa.String(length=20), nullable=True))
    op.execute("UPDATE users SET postal_code = city WHERE city IS NOT NULL")
    op.drop_column("users", "city")


def downgrade():
    op.add_column("users", sa.Column("city", sa.String(length=120), nullable=True))
    op.execute("UPDATE users SET city = postal_code WHERE postal_code IS NOT NULL")
    op.drop_column("users", "postal_code")
