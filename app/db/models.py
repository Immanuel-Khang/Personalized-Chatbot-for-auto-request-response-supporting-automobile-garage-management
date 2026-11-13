"""
Schema đợt 1 theo docs/schema.dbml: vận hành (quản lý, phiên chat, khách, tin nhắn, chuyển người, showroom, lịch hẹn).
Đợt 2 (danh mục xe) theo docs/catalog_hybrid.dbml: brands, vehicle_models, products, vehicle_specs, product_prices...
Chỉ chạy PostgreSQL (jsonb, timestamptz). Mọi thay đổi schema đi qua Alembic: sửa class ở đây rồi
`alembic revision --autogenerate -m "..."` và `alembic upgrade head`.
Mã trạng thái dạng varchar + CHECK, không dùng native ENUM. Timestamp UTC.
"""
from datetime import date, datetime, time, timezone
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String,
    Text, Time, UniqueConstraint, func, text, event, DDL, Computed,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
SERVICE_TYPES = ("MAINTENANCE", "REPAIR", "SHOWROOM_VISIT", "CONTRACT_SIGN")
APPOINTMENT_STATUSES = ("PENDING_APPROVAL", "CONFIRMED", "REJECTED", "CANCELLED", "DONE")


class Manager(Base):
    """Nhân viên nội bộ (bắt buộc đăng nhập, xem app/auth.py). failed_logins/locked_until: khóa tạm khi sai mật khẩu liên tiếp."""
    __tablename__ = "managers"
    __table_args__ = (_in("role", ("sales_admin", "manager", "technician")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="sales_admin", server_default="sales_admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    failed_logins: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")  # số lần sai liên tiếp
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # khóa đăng nhập tới thời điểm này
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
    """Lệch có chủ đích so với schema.dbml: slot_id được NULL khi PENDING_APPROVAL; nhân viên gán slot khi duyệt.
    appointment_date/start_time: ngày giờ khách hẹn do agent quy đổi ("thứ 7 tuần sau" -> ngày cụ thể); start_time NULL
    khi khách chưa nêu giờ. note chỉ chứa xe và yêu cầu riêng của khách."""
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
    appointment_date: Mapped[date | None] = mapped_column(Date, index=True)  # NULL chỉ ở dòng cũ trước migration
    start_time: Mapped[time | None] = mapped_column(Time)
    status: Mapped[str] = mapped_column(String(20), default="PENDING_APPROVAL", server_default="PENDING_APPROVAL",
                                        index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AppSetting(Base):
    """Cấu hình chỉnh được lúc chạy (key-value), vd booking.slot_capacity. Giá trị mặc định lấy từ biến môi trường."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated()


# ---- Danh mục xe (docs/catalog_hybrid.dbml): cột có kiểu cho thứ bot lọc/so sánh, JSONB cho phần dài đuôi.
PRODUCT_KINDS = ("CAR", "TRUCK", "PART")
PRODUCT_STATUSES = ("DRAFT", "ACTIVE", "RETIRED")  # chỉ ACTIVE mới được bot trả lời
PRICE_TYPES = ("MSRP", "SELLING")
PRICE_BASES = ("CHASSIS", "FLATBED", "TARP", "COMPLETE", "UNKNOWN")
MEDIA_KINDS = ("IMAGE", "VIDEO", "BROCHURE", "SPEC", "PRICE")


def _pos(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} > 0", name=column)


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class VehicleModel(Base):
    """Dòng xe nhóm các cấu hình (vd Aumark-9T). Cấu hình cụ thể (wheelbase, tải trọng) là CatalogProduct."""
    __tablename__ = "vehicle_models"
    __table_args__ = (UniqueConstraint("brand_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("brands.id"))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class CatalogProduct(Base):
    """Một cấu hình bán được (bảng products). Không UNIQUE theo tên/model: cùng model có nhiều cấu hình.
    name vừa để hiển thị vừa để tra bằng ILIKE (DbProductService), nên phải ổn định."""
    __tablename__ = "products"
    __table_args__ = (_in("kind", PRODUCT_KINDS), _in("status", PRODUCT_STATUSES), Index(None, "kind", "status"))
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    brand_id: Mapped[int | None] = mapped_column(SmallInteger, ForeignKey("brands.id"), index=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_models.id"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", server_default="DRAFT")
    video_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    spec: Mapped["VehicleSpec | None"] = relationship(uselist=False)
    prices: Mapped[list["ProductPrice"]] = relationship()


class VehicleSpec(Base):
    """1:1 với products kind CAR/TRUCK. NULL = chưa biết (dấu "-" của nguồn cũng là NULL, không phải 0/false)."""
    __tablename__ = "vehicle_specs"
    __table_args__ = (
        _in("assembly", ("CKD", "CBU")),
        *(_pos(c) for c in ("seats", "curb_mass_kg", "gvm_kg", "wheelbase_mm", "length_mm", "width_mm", "height_mm",
                            "ground_clearance_mm", "turning_radius_m", "fuel_tank_l", "cargo_length_mm",
                            "cargo_width_mm", "cargo_height_mm", "power_kw", "power_rpm", "torque_nm",
                            "torque_rpm_min", "torque_rpm_max", "displacement_cc", "gears_forward", "gears_reverse")),
        CheckConstraint("payload_kg >= 0", name="payload_kg"),
        Index(None, "payload_kg"), Index(None, "gvm_kg"), Index(None, "wheelbase_mm"),
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(40))
    assembly: Mapped[str | None] = mapped_column(String(3))
    wheel_formula: Mapped[str | None] = mapped_column(String(12))
    seats: Mapped[int | None] = mapped_column(SmallInteger)
    curb_mass_kg: Mapped[int | None] = mapped_column(Integer)
    curb_mass_basis: Mapped[str | None] = mapped_column(String(40))
    payload_kg: Mapped[int | None] = mapped_column(Integer)
    gvm_kg: Mapped[int | None] = mapped_column(Integer)
    wheelbase_mm: Mapped[int | None] = mapped_column(Integer)
    length_mm: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    dims_basis: Mapped[str | None] = mapped_column(String(20))
    ground_clearance_mm: Mapped[int | None] = mapped_column(Integer)
    turning_radius_m: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    fuel_tank_l: Mapped[int | None] = mapped_column(SmallInteger)
    cargo_length_mm: Mapped[int | None] = mapped_column(Integer)
    cargo_width_mm: Mapped[int | None] = mapped_column(Integer)
    cargo_height_mm: Mapped[int | None] = mapped_column(Integer)
    engine_model: Mapped[str | None] = mapped_column(String(64))
    power_kw: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    power_rpm: Mapped[int | None] = mapped_column(SmallInteger)
    torque_nm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    torque_rpm_min: Mapped[int | None] = mapped_column(SmallInteger)
    torque_rpm_max: Mapped[int | None] = mapped_column(SmallInteger)
    displacement_cc: Mapped[int | None] = mapped_column(Integer)
    emission_standard: Mapped[str | None] = mapped_column(String(24))
    has_scr: Mapped[bool | None] = mapped_column(Boolean)
    gearbox_model: Mapped[str | None] = mapped_column(String(64))
    gears_forward: Mapped[int | None] = mapped_column(SmallInteger)
    gears_reverse: Mapped[int | None] = mapped_column(SmallInteger)
    tire_size: Mapped[str | None] = mapped_column(String(32))
    has_abs: Mapped[bool | None] = mapped_column(Boolean)
    fuel_type: Mapped[str | None] = mapped_column(String(20))  # Diesel | Xăng; master CSV không có cột này nên để NULL
    spec_extra: Mapped[dict | None] = mapped_column(JSONB)  # đuôi dài đã parse (trục, nhíp, phương án tải...)
    spec_raw: Mapped[dict | None] = mapped_column(JSONB)  # chuỗi gốc theo cột nguồn, để parse lại
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ProductPrice(Base):
    """amount_vnd là đồng (nguồn ghi triệu đồng). Mỗi (product, price_type) chỉ một dòng is_current."""
    __tablename__ = "product_prices"
    __table_args__ = (
        _in("price_type", PRICE_TYPES), _in("basis", PRICE_BASES),
        CheckConstraint("amount_vnd >= 0", name="amount_vnd"),
        Index("uq_current_price", "product_id", "price_type", unique=True, postgresql_where=text("is_current")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    price_type: Mapped[str] = mapped_column(String(12), default="MSRP", server_default="MSRP")
    amount_vnd: Mapped[int] = mapped_column(BigInteger)
    basis: Mapped[str] = mapped_column(String(12), default="UNKNOWN", server_default="UNKNOWN")
    basis_note: Mapped[str | None] = mapped_column(String(120))
    effective_date: Mapped[date | None] = mapped_column(Date)  # ngày hiệu lực; bắt buộc để bot được báo giá
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ProductMedia(Base):
    __tablename__ = "product_media"
    __table_args__ = (_in("kind", MEDIA_KINDS), Index(None, "product_id", "kind"))
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    kind: Mapped[str] = mapped_column(String(10))
    url: Mapped[str] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ProductCompetitor(Base):
    __tablename__ = "product_competitors"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    competitor_name: Mapped[str] = mapped_column(String(200))
    competitor_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    advantage: Mapped[str | None] = mapped_column(Text)
    disadvantage: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class CatalogSource(Base):
    __tablename__ = "catalog_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # metadata file, không phải ngày hiệu lực giá
    imported_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ProductSource(Base):
    __tablename__ = "product_sources"
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("catalog_sources.id"), primary_key=True, index=True)
    sheet: Mapped[str] = mapped_column(String(60), primary_key=True)
    row_ref: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class DataIssue(Base):
    """Giá trị nguồn nghi ngờ: không tự sửa, ghi lại ở đây để người duyệt quyết định."""
    __tablename__ = "data_issues"
    __table_args__ = (Index(None, "code", "resolved_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    raw_value: Mapped[str | None] = mapped_column(Text)
    code: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()



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
