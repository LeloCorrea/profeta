"""
Migration: cria tabela user_profile para rastreamento de atividade por tipo de interação.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "user_profile",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("verse_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("explanation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reflection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prayer_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_interaction_at", sa.DateTime, nullable=True),
    )


def downgrade():
    op.drop_table("user_profile")
