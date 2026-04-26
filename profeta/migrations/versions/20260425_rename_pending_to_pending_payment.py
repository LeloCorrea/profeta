"""
Migration: renomeia payment_status "pending" → "pending_payment" em image_requests.

Compatibilidade: linhas criadas antes do novo padrão usavam "pending".
"""
from alembic import op


def upgrade():
    op.execute(
        "UPDATE image_requests SET payment_status = 'pending_payment' "
        "WHERE payment_status = 'pending'"
    )


def downgrade():
    op.execute(
        "UPDATE image_requests SET payment_status = 'pending' "
        "WHERE payment_status = 'pending_payment'"
    )
