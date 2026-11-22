"""app_settings: cấu hình khung giờ đặt lịch chỉnh được từ trang admin

Revision ID: f6b8d0e2a4c7
Revises: e5a7c9d1f3b6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6b8d0e2a4c7"
down_revision: Union[str, Sequence[str], None] = "e5a7c9d1f3b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
