"""
Schema P0 (đủ để demo). Bảng P1/P2 trong sơ đồ (consent, outbox, audit_logs, service_history,
maintenance_rules, workshop_capacity...) thêm vào đây khi tới lượt, mỗi bảng 1 class.
Dùng SQLite lúc dev; đổi DATABASE_URL sang Postgres là chạy tiếp, không sửa code.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    visitor_token: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    mode: Mapped[str] = mapped_column(String(20), default="BOT") # BOT, HUMAN_PENDING
    negotiation_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | bot | staff | system (thông báo chuyển người/trả về bot)
    content: Mapped[str] = mapped_column(Text)
    trace: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CatalogItem(Base):
    __tablename__ = "catalog"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # CAR | PART
    name: Mapped[str] = mapped_column(String(200))
    list_price_vnd: Mapped[int] = mapped_column(Integer)
    price_as_of: Mapped[str] = mapped_column(String(20))
    specs_json: Mapped[str] = mapped_column(Text, default="{}")
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def specs(self) -> dict:
        return json.loads(self.specs_json or "{}")


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    service_type: Mapped[str] = mapped_column(String(20))
    when_text: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="PENDING_APPROVAL")


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HumanTask(Base):
    __tablename__ = "human_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    reason: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN | CLAIMED | RESOLVED
    assignee: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

