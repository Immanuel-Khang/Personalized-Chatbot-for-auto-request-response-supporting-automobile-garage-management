"""appointments.appointment_date / start_time (date and time no longer only in note)

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c0e1
Create Date: 2026-11-01 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f4"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: existing rows only have the customer's phrase in note, which cannot be converted reliably.
    op.add_column("appointments", sa.Column("appointment_date", sa.Date(), nullable=True))
    op.add_column("appointments", sa.Column("start_time", sa.Time(), nullable=True))
    op.create_index(op.f("ix_appointments_appointment_date"), "appointments", ["appointment_date"])


def downgrade() -> None:
    op.drop_index(op.f("ix_appointments_appointment_date"), table_name="appointments")
    op.drop_column("appointments", "start_time")
    op.drop_column("appointments", "appointment_date")
