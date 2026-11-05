"""managers.failed_logins / locked_until (khóa tạm khi đăng nhập sai liên tiếp)

Revision ID: e5a7c9d1f3b6
Revises: d4f6b8c0e2a5
Create Date: 2026-11-05 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9d1f3b6"
down_revision: Union[str, Sequence[str], None] = "d4f6b8c0e2a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("managers", sa.Column("failed_logins", sa.SmallInteger(), server_default="0", nullable=False))
    op.add_column("managers", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("managers", "locked_until")
    op.drop_column("managers", "failed_logins")
