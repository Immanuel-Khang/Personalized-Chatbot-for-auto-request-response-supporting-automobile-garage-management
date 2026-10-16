"""[TRACK C] Identity: chat_sessions.id (UUID trình duyệt) -> phiên; SĐT -> customers.
Phiên chỉ dùng để nhận ra trình duyệt, KHÔNG phải bằng chứng danh tính: dữ liệu cá nhân (lịch sử xe...) chỉ đưa cho
agent khi SĐT đã xác minh OTP. TODO[PLATFORM]: OTP để set phone_verified_at, gộp khách (soft merge)."""
import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ChatSession, Customer


def normalize_phone(raw: str) -> str | None:
    """Chuẩn hóa SĐT Việt Nam về +84xxxxxxxxx. Trả None nếu không hợp lệ."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+84"):
        digits = "0" + digits[3:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    return "+84" + digits[1:] if re.fullmatch(r"0\d{9}", digits) else None


def get_or_create_session(db: Session, session_id: str) -> ChatSession:
    # ON CONFLICT DO NOTHING: hai request đầu tiên cùng lúc của một trình duyệt không bị lỗi trùng khóa
    db.execute(insert(ChatSession).values(id=session_id).on_conflict_do_nothing(index_elements=["id"]))
    return db.get(ChatSession, session_id)


def get_or_create_customer(db: Session, phone: str) -> Customer:
    """phone phải đã chuẩn hóa."""
    db.execute(insert(Customer).values(phone_number=phone).on_conflict_do_nothing(index_elements=["phone_number"]))
    return db.scalars(select(Customer).where(Customer.phone_number == phone)).one()


def attach_phone(db: Session, session: ChatSession, raw_phone: str) -> Customer | None:
    """Gắn phiên với khách theo SĐT. Phiên đã gắn khách rồi thì giữ nguyên, tránh đổi chủ phiên giữa chừng."""
    phone = normalize_phone(raw_phone)
    if phone is None:
        return None
    if session.customer_id is not None:
        return db.get(Customer, session.customer_id)
    customer = get_or_create_customer(db, phone)
    session.customer_id = customer.id
    return customer


def verified_customer_id(db: Session, session: ChatSession) -> int | None:
    """customer_id chỉ khi SĐT đã xác minh; agent dùng id này để đọc dữ liệu cá nhân."""
    if session.customer_id is None:
        return None
    customer = db.get(Customer, session.customer_id)
    return customer.id if customer and customer.phone_verified_at else None
