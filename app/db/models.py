"""
Schema đợt 1 theo docs/schema.dbml: vận hành (quản lý, phiên chat, khách, tin nhắn, chuyển người, showroom, lịch hẹn).
Đợt 2 (danh mục xe: brands, vehicle_variants, variant_prices...) sẽ thay bảng catalog tạm.
Chỉ chạy PostgreSQL (jsonb, timestamptz). Mọi thay đổi schema đi qua Alembic: sửa class ở đây rồi
`alembic revision --autogenerate -m "..."` và `alembic upgrade head`.
Mã trạng thái dạng varchar + CHECK, không dùng native ENUM. Timestamp UTC.
"""
import json
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text,
    UniqueConstraint, func, text, event, DDL, Computed,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _in(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    items = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({items})", name=name or column)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now())


SESSION_MODES = ("BOT", "HUMAN_PENDING", "HUMAN_ACTIVE")
# "system" (thông báo chuyển người/trả về bot/quá hạn) thêm so với schema.dbml; cần cập nhật lại sơ đồ.
MESSAGE_ROLES = ("user", "ai", "tool", "staff", "system")
HANDOVER_REASONS = ("NEGOTIATION", "HUMAN_REQUEST", "COMPLAINT", "COMPLEXITY_HIGH", "OUTPUT_GUARD",
                    "DEAL_CHANGE", "APPOINTMENT_APPROVAL", "OTHER")
TICKET_STATUSES = ("OPEN", "CLAIMED", "RESOLVED")
SLOT_TYPES = ("SERVICE_BAY", "SHOWROOM_VISIT")
SERVICE_TYPES = ("MAINTENANCE", "REPAIR", "SHOWROOM_VISIT")
APPOINTMENT_STATUSES = ("PENDING_APPROVAL", "CONFIRMED", "REJECTED", "CANCELLED", "DONE")


class Manager(Base):
    """Nhân viên nội bộ (bắt buộc đăng nhập). TODO[PLATFORM]: đăng nhập + RBAC cho admin API."""
    __tablename__ = "managers"
    __table_args__ = (_in("role", ("sales_admin", "manager", "technician")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="sales_admin", server_default="sales_admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Customer(Base):
    """Khách do AI trích xuất, không có password. SĐT (dạng +84...) là định danh chính."""
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = chưa OTP
    full_name: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(120))
    birthday: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ChatSession(Base):
    """Phiên chat = trình duyệt của khách. id là UUID trình duyệt tạo, không phải bằng chứng danh tính."""
    __tablename__ = "chat_sessions"
    __table_args__ = (_in("mode", SESSION_MODES),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)  # NULL tới khi biết SĐT
    mode: Mapped[str] = mapped_column(String(20), default="BOT", server_default="BOT", index=True)
    negotiation_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # cờ dính
    current_stage: Mapped[str] = mapped_column(String(40), default="IDLE", server_default="IDLE")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (_in("role", MESSAGE_ROLES), Index(None, "session_id", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(String(20))  # user | ai | tool | staff | system
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("managers.id"))  # chỉ có khi role = staff
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | dict | None] = mapped_column(JSONB)
    trace: Mapped[list | None] = mapped_column(JSONB)  # node đã đi qua trong lượt, dùng cho eval trajectory
    created_at: Mapped[datetime] = _created()


class HandoverTicket(Base):
    """Ticket chuyển cho nhân viên. reason là mã chính; chi tiết đầy đủ nằm ở summary."""
    __tablename__ = "handover_tickets"
    __table_args__ = (_in("reason", HANDOVER_REASONS), _in("status", TICKET_STATUSES),
                      Index(None, "status", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    reason: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="OPEN", server_default="OPEN")
    assigned_manager_id: Mapped[int | None] = mapped_column(ForeignKey("managers.id"))
    summary: Mapped[str | None] = mapped_column(Text)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # quá hạn mà chưa ai nhận -> báo hotline
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Showroom(Base):
    __tablename__ = "showrooms"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    opening_hours: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AppointmentSlot(Base):
    __tablename__ = "appointment_slots"
    __table_args__ = (
        _in("slot_type", SLOT_TYPES),
        CheckConstraint("capacity > 0", name="capacity"),
        CheckConstraint("booked_count >= 0", name="booked_count"),
        CheckConstraint("booked_count <= capacity", name="booked_le_capacity"),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        UniqueConstraint("showroom_id", "slot_type", "starts_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    showroom_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("showrooms.id"))
    slot_type: Mapped[str] = mapped_column(String(20))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(SmallInteger)
    booked_count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")  # tăng trong transaction khi giữ chỗ
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Appointment(Base):
    """Lệch có chủ đích so với schema.dbml: slot_id được NULL khi PENDING_APPROVAL vì bot chưa quy đổi
    thời gian khách nói ("sáng thứ 7") ra slot; nhân viên gán slot khi duyệt. Thời gian khách nói nằm ở note."""
    __tablename__ = "appointments"
    __table_args__ = (
        _in("service_type", SERVICE_TYPES),
        _in("status", APPOINTMENT_STATUSES),
        CheckConstraint("slot_id IS NOT NULL OR status IN ('PENDING_APPROVAL', 'REJECTED', 'CANCELLED')",
                        name="slot_required_when_confirmed"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id"))
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("appointment_slots.id"), index=True)
    service_type: Mapped[str] = mapped_column(String(20))
    # TODO[ĐỢT 3]: vehicle_id -> customer_vehicles.id, variant_id -> vehicle_variants.id khi có các bảng đó
    contact_phone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PENDING_APPROVAL", server_default="PENDING_APPROVAL",
                                        index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# Bảng tạm, giữ nguyên tới đợt 2 (danh mục xe theo schema.dbml).
class CatalogItem(Base):
    __tablename__ = "catalog"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # CAR | TRUCK | PART
    name: Mapped[str] = mapped_column(String(200))
    list_price_vnd: Mapped[int] = mapped_column(Integer)
    price_as_of: Mapped[str] = mapped_column(String(20))
    specs_json: Mapped[str] = mapped_column(Text, default="{}")
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def specs(self) -> dict:
        return json.loads(self.specs_json or "{}")





# ---- RAG: chỉ mục tri thức (track A). File trong data/knowledge/ vẫn là nguồn gốc; bảng này là chỉ mục dùng chung
# giữa các worker: chunk + vector (pgvector) + tsvector (full-text). id = hash nội dung -> đồng bộ tăng dần.
RAG_EMBEDDING_DIM = 1536  # text-embedding-3-small. Đổi model khác chiều -> phải có migration mới (đổi cột + index)

event.listen(Base.metadata, "before_create", DDL("CREATE EXTENSION IF NOT EXISTS vector"))


class RagChunk(Base):
    __tablename__ = "rag_chunks"
    namespace: Mapped[str] = mapped_column(String(40), primary_key=True, default="knowledge", server_default="knowledge")
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # Chunk.id (hash nội dung)
    source: Mapped[str] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(Text, default="", server_default="")
    kind: Mapped[str] = mapped_column(String(10), default="doc", server_default="doc")  # doc | faq
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    text: Mapped[str] = mapped_column(Text)
    index_text: Mapped[str] = mapped_column(Text)
    lex_text: Mapped[str] = mapped_column(Text)  # token đã bỏ dấu/stopword, nối bằng khoảng trắng (cho full-text)
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('simple', lex_text)", persisted=True))
    embedding = mapped_column(Vector(RAG_EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        Index("ix_rag_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_rag_chunks_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
