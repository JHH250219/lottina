"""increase recurrence_rule length

Revision ID: 6f63b34b8f54
Revises: 752c3a430414
Create Date: 2025-12-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6f63b34b8f54"
down_revision = "752c3a430414"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("offers", schema=None) as batch_op:
        batch_op.alter_column("recurrence_rule", type_=sa.String(length=400))


def downgrade():
    with op.batch_alter_table("offers", schema=None) as batch_op:
        batch_op.alter_column("recurrence_rule", type_=sa.String(length=200))
