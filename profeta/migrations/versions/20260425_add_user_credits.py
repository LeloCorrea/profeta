"""
Migration: cria tabela user_credits para saldo de créditos de imagem por usuário.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "user_credits",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("credits_balance", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_user_credits_user_id", "user_credits", ["user_id"], unique=True)


def downgrade():
    op.drop_index("ix_user_credits_user_id", table_name="user_credits")
    op.drop_table("user_credits")
