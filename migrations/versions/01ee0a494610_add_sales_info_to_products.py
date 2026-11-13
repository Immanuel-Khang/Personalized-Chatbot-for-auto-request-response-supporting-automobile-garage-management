"""add sales_info to products

Revision ID: 01ee0a494610
Revises: a7c9e1f3b5d8
Create Date: 2026-11-13 22:48:31.240353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01ee0a494610'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1f3b5d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add sales_info JSONB column to products table for storing sales/marketing data."""
    op.add_column('products', sa.Column('sales_info', sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Remove sales_info column from products table."""
    op.drop_column('products', 'sales_info')
