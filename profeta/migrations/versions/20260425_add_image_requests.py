"""
Migration: cria tabela image_requests para pedidos manuais de imagem.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "image_requests",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("telegram_id", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("content_text", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("image_path", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_image_requests_user_id", "image_requests", ["user_id"])
    op.create_index("ix_image_requests_telegram_id", "image_requests", ["telegram_id"])


def downgrade():
    op.drop_index("ix_image_requests_telegram_id", table_name="image_requests")
    op.drop_index("ix_image_requests_user_id", table_name="image_requests")
    op.drop_table("image_requests")
