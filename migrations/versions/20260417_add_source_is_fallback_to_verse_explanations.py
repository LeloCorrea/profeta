"""
Migration para adicionar campo 'source' e 'is_fallback' em verse_explanations.
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('verse_explanations', sa.Column('source', sa.String(32), nullable=True, server_default='openai'))
    op.add_column('verse_explanations', sa.Column('is_fallback', sa.Boolean(), nullable=True, server_default=sa.text('0')))

def downgrade():
    op.drop_column('verse_explanations', 'source')
    op.drop_column('verse_explanations', 'is_fallback')
