"""
Migration: UNIQUE constraint em subscriptions.user_id.

Garante que cada usuário tenha no máximo uma Subscription.
Antes de criar o índice, deduplica registros existentes preservando
o de maior id por user_id (o mais recente).
"""
from alembic import op


def upgrade():
    # Remove duplicatas mantendo o registro mais recente (maior id) por user_id.
    # Esta operação é idempotente: se não há duplicatas, nada é deletado.
    op.execute("""
        DELETE FROM subscriptions
        WHERE id NOT IN (
            SELECT MAX(id) FROM subscriptions GROUP BY user_id
        )
    """)

    # Cria índice único. Em SQLite, índice único é a única forma de enforcar
    # uniqueness sem recriar a tabela inteira.
    op.create_index(
        "uq_subscription_user",
        "subscriptions",
        ["user_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_subscription_user", table_name="subscriptions")
