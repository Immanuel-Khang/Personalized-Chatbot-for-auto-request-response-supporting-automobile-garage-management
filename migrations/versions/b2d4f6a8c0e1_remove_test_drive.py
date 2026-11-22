"""remove TEST_DRIVE service/slot type (garage has no test-drive service)

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-30 10:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, Sequence[str], None] = "a1c3e5f7b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, f"{column} IN ({values})")


def upgrade() -> None:
    # Fails loudly if any TEST_DRIVE rows exist instead of silently rewriting them.
    _replace("appointment_slots", "slot_type", "'SERVICE_BAY', 'SHOWROOM_VISIT'")
    _replace("appointments", "service_type", "'MAINTENANCE', 'REPAIR', 'SHOWROOM_VISIT'")


def downgrade() -> None:
    _replace("appointment_slots", "slot_type", "'SERVICE_BAY', 'TEST_DRIVE', 'SHOWROOM_VISIT'")
    _replace("appointments", "service_type", "'MAINTENANCE', 'REPAIR', 'TEST_DRIVE', 'SHOWROOM_VISIT'")
