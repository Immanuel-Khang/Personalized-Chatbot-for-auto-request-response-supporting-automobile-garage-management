"""remove description column from products

Revision ID: b4d0a3c4bc80
Revises: 521aa8cf9846
Create Date: 2026-11-15 23:21:32.288634

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d0a3c4bc80'
down_revision: Union[str, Sequence[str], None] = '521aa8cf9846'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('products', 'description')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('products', sa.Column('description', sa.Text(), nullable=True))
