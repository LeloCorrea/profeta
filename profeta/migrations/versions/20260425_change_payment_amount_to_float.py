"""
Migration: altera coluna amount da tabela payments de VARCHAR(32) para FLOAT.
Usa batch_alter_table para compatibilidade com SQLite.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.alter_column("amount", type_=sa.Float(), existing_nullable=True)


def downgrade():
    with op.batch_alter_table("payments", recreate="always") as batch_op:
        batch_op.alter_column("amount", type_=sa.String(32), existing_nullable=True)
