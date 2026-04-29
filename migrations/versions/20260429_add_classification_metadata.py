"""
Migration: adiciona campos de metadados de classificação ao verses.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("verses") as batch_op:
        batch_op.add_column(sa.Column("confidence", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("classified_at", sa.DateTime, nullable=True))
        batch_op.add_column(sa.Column("classified_by", sa.String(32), nullable=True))


def downgrade():
    with op.batch_alter_table("verses") as batch_op:
        batch_op.drop_column("classified_by")
        batch_op.drop_column("classified_at")
        batch_op.drop_column("confidence")
