"""expand recurrence_rule to 1000

Revision ID: 7f65c6d6d2c5
Revises: 6f63b34b8f54
Create Date: 2025-12-10 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7f65c6d6d2c5"
down_revision = "6f63b34b8f54"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("offers", schema=None) as batch_op:
        batch_op.alter_column("recurrence_rule", type_=sa.String(length=1000))


def downgrade():
    with op.batch_alter_table("offers", schema=None) as batch_op:
        batch_op.alter_column("recurrence_rule", type_=sa.String(length=400))
