"""
Migration: adiciona price e payment_status à tabela image_requests.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("image_requests", sa.Column("price", sa.Float, nullable=False, server_default="3.90"))
    op.add_column("image_requests", sa.Column("payment_status", sa.String(32), nullable=False, server_default="pending"))


def downgrade():
    op.drop_column("image_requests", "payment_status")
    op.drop_column("image_requests", "price")
