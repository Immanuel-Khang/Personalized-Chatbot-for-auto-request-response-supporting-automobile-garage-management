"""staff_actions: đề xuất cập nhật từ tin nhân viên (duyệt trong hộp thoại) + mốc đã phân tích trên ticket

Revision ID: a7d9f1b3c5e8
Revises: f6b8d0e2a4c7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d9f1b3c5e8"
down_revision: Union[str, Sequence[str], None] = "f6b8d0e2a4c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TYPES = ("BOOK_APPOINTMENT", "RESCHEDULE_APPOINTMENT", "CANCEL_APPOINTMENT", "AGREED_PRICE", "CUSTOMER_INFO",
         "HANDLING_NOTE")
STATUSES = ("PROPOSED", "APPLIED", "DISMISSED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("handover_tickets", sa.Column("actions_scanned_message_id", sa.Integer(), nullable=True))
    op.create_table(
        "staff_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="PROPOSED", nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("action_type", TYPES), name=op.f("ck_staff_actions_action_type")),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_staff_actions_status")),
        sa.ForeignKeyConstraint(["ticket_id"], ["handover_tickets.id"], name=op.f("fk_staff_actions_ticket_id_handover_tickets")),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], name=op.f("fk_staff_actions_session_id_chat_sessions")),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], name=op.f("fk_staff_actions_source_message_id_messages")),
        sa.ForeignKeyConstraint(["decided_by"], ["managers.id"], name=op.f("fk_staff_actions_decided_by_managers")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_actions")),
    )
    op.create_index(op.f("ix_staff_actions_session_id"), "staff_actions", ["session_id"])
    op.create_index(op.f("ix_staff_actions_ticket_id_status"), "staff_actions", ["ticket_id", "status"])


def downgrade() -> None:
    op.drop_table("staff_actions")
    op.drop_column("handover_tickets", "actions_scanned_message_id")
