"""add description column to products

Revision ID: 521aa8cf9846
Revises: 01ee0a494610
Create Date: 2026-11-15 22:44:58.342693

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '521aa8cf9846'
down_revision: Union[str, Sequence[str], None] = '01ee0a494610'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('products', sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'description')
