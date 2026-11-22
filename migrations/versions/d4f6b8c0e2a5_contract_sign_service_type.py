"""appointments.service_type CONTRACT_SIGN (customer comes to sign the purchase contract)

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
Create Date: 2026-11-04 10:00:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4f6b8c0e2a5"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace(values: str) -> None:
    op.drop_constraint(op.f("ck_appointments_service_type"), "appointments", type_="check")
    op.create_check_constraint(op.f("ck_appointments_service_type"), "appointments", f"service_type IN ({values})")


def upgrade() -> None:
    _replace("'MAINTENANCE', 'REPAIR', 'SHOWROOM_VISIT', 'CONTRACT_SIGN'")


def downgrade() -> None:
    # Fails loudly if CONTRACT_SIGN rows exist instead of silently rewriting them.
    _replace("'MAINTENANCE', 'REPAIR', 'SHOWROOM_VISIT'")
