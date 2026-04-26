"""
Migration para adicionar campo 'depth' em verse_explanations.

Inclui:
- Adição da coluna com server_default 'balanced'
- Backfill explícito de linhas com NULL para 'balanced'
- Índice composto (book, chapter, verse, depth) para queries filtradas por depth
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column(
        "verse_explanations",
        sa.Column("depth", sa.String(32), nullable=True, server_default="balanced"),
    )

    op.execute(
        "UPDATE verse_explanations SET depth = 'balanced' WHERE depth IS NULL"
    )

    op.create_index(
        "ix_verse_explanations_book_chapter_verse_depth",
        "verse_explanations",
        ["book", "chapter", "verse", "depth"],
    )


def downgrade():
    op.drop_index(
        "ix_verse_explanations_book_chapter_verse_depth",
        table_name="verse_explanations",
    )
    op.drop_column("verse_explanations", "depth")
