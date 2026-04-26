"""
Migration: adiciona asaas_customer_id e last_interaction_at na tabela users.

- asaas_customer_id: mapeia customer Asaas ao User para ativação proativa em renovações
- last_interaction_at: rastreia última atividade para detecção de dormência
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        "users",
        sa.Column("asaas_customer_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_users_asaas_customer_id", "users", ["asaas_customer_id"])

    op.add_column(
        "users",
        sa.Column("last_interaction_at", sa.DateTime, nullable=True),
    )


def downgrade():
    op.drop_column("users", "last_interaction_at")
    op.drop_index("ix_users_asaas_customer_id", table_name="users")
    op.drop_column("users", "asaas_customer_id")
