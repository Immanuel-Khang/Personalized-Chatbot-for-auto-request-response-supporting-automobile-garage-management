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


def is_full_name(name: str | None) -> bool:
    """Họ và tên đầy đủ: ít nhất hai từ chữ cái ("Khang" là tên gọi, "Dương Định Khang" là họ tên)."""
    words = [w for w in (name or "").split() if any(c.isalpha() for c in w)]
    return len(words) >= 2


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


def sync_contact(db: Session, session: ChatSession, raw_phone: str | None = None, name: str | None = None,
                 name_corrected: bool = False) -> Customer | None:
    """Đưa SĐT/tên mới nhất khách nêu trong chat vào customers của phiên.
    Khách chưa xác minh OTP là dữ liệu AI trích xuất -> khách đính chính thì ghi đè. SĐT đã xác minh là bằng chứng
    danh tính, chat không được đổi. SĐT mới đã thuộc khách khác -> gắn phiên sang khách đó (như lần gắn đầu tiên)."""
    customer = db.get(Customer, session.customer_id) if session.customer_id else None
    phone = normalize_phone(raw_phone) if raw_phone else None
    switched = False  # phiên vừa chuyển sang hồ sơ có sẵn của người khác -> không đổi tên hồ sơ đó
    if phone and customer is None:
        customer = attach_phone(db, session, phone)
    elif phone and customer is not None and customer.phone_number != phone and customer.phone_verified_at is None:
        owner = db.scalars(select(Customer).where(Customer.phone_number == phone)).first()
        if owner is None:
            customer.phone_number = phone
        else:
            session.customer_id, customer, switched = owner.id, owner, True
    if name and not is_full_name(name):
        name = None  # tên gọi chỉ dùng để xưng hô trong phiên, không ghi vào hồ sơ khách
    if name and customer is not None and customer.full_name != name and (
            not is_full_name(customer.full_name) or (name_corrected and not switched and customer.phone_verified_at is None)):
        customer.full_name = name
    return customer


def verified_customer_id(db: Session, session: ChatSession) -> int | None:
    """customer_id chỉ khi SĐT đã xác minh; agent dùng id này để đọc dữ liệu cá nhân."""
    if session.customer_id is None:
        return None
    customer = db.get(Customer, session.customer_id)
    return customer.id if customer and customer.phone_verified_at else None
