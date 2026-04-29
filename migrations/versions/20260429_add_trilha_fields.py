"""
Migration: adiciona selected_trilha ao users, trilha e tags ao verses.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("selected_trilha", sa.String(64), nullable=True))

    with op.batch_alter_table("verses") as batch_op:
        batch_op.add_column(sa.Column("trilha", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("tags", sa.Text, nullable=True))
        batch_op.create_index("ix_verses_trilha", ["trilha"])


def downgrade():
    with op.batch_alter_table("verses") as batch_op:
        batch_op.drop_index("ix_verses_trilha")
        batch_op.drop_column("tags")
        batch_op.drop_column("trilha")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("selected_trilha")
