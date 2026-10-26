"""Helper cho bộ hồi quy hội thoại (tests/test_conv_*.py), xem docs/CONVERSATION_TESTS.md.
Chạy qua API /chat + graph thật ở chế độ rule-based (conftest: LLM_PROVIDER=none, EMBEDDING_PROVIDER=none),
nên câu trả lời là template/tài liệu xác định. Lỗi đã biết đánh dấu @defect (xfail strict): sửa xong test báo XPASS."""
import re
import uuid
from pathlib import Path

import pytest

from app.agent.graph import get_graph
from app.agent.nodes.router import WRITERS
from app.db.models import Appointment, CatalogItem, ChatSession, HandoverTicket
from app.db.session import SessionLocal
from app.identity import attach_phone, get_or_create_session
from app.services.maintenance import COST_MAP

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge"
# Dấu hiệu bot đang thu thập slot đặt lịch
DATE_ASK = ("ngày giờ", "giờ nào", "ngày nào", "mấy giờ")
PHONE_ASK = ("số điện thoại", "sđt")
BOOKING_SLOTS = {"service_type", "appointment_when", "when_text"}
NOT_UNDERSTOOD = "chưa hiểu"
BOOKED = "ghi nhận lịch hẹn"
AMOUNT = re.compile(r"\d{1,3}(?:\.\d{3})+(?=\s*(?:đồng|vnđ))", re.IGNORECASE)


def defect(code: str, why: str):
    """Hành vi mong muốn chưa đạt: giữ nguyên assertion, lỗi được ghi trong docs/CONVERSATION_TESTS.md."""
    return pytest.mark.xfail(strict=True, reason=f"{code}: {why}")


class Conversation:
    """Một phiên chat qua /chat; mỗi lượt kiểm tra hợp đồng API và bất biến của router."""

    def __init__(self, client, token: str):
        self.client, self.id = client, f"{token}-{uuid.uuid4().hex[:12]}"

    def say(self, text: str) -> dict:
        response = self.client.post("/chat", json={"session_id": self.id, "message": text})
        assert response.status_code == 200
        body = response.json()
        assert {"session_id", "reply", "mode", "trace", "message_id"} <= set(body)
        assert body["session_id"] == self.id  # không đổi phiên giữa chừng
        assert len([n for n in body["trace"] if n in WRITERS]) <= 1  # tối đa một agent ghi dữ liệu
        assert len(body["trace"]) == len(set(body["trace"]))  # không tạo task trùng
        return body

    @property
    def state(self) -> dict:
        return get_graph().get_state({"configurable": {"thread_id": self.id}}).values

    def appointments(self) -> list[Appointment]:
        with SessionLocal() as db:
            return db.query(Appointment).filter_by(session_id=self.id).all()

    def tickets(self) -> list[HandoverTicket]:
        with SessionLocal() as db:
            return db.query(HandoverTicket).filter_by(session_id=self.id).all()

    def customer_id(self) -> int | None:
        with SessionLocal() as db:
            session = db.get(ChatSession, self.id)
            return session.customer_id if session else None

    def link_customer(self, phone: str) -> int:
        """Khách đã biết: phiên gắn sẵn SĐT (chưa xác minh OTP), giống identity.attach_phone khi khách để lại số."""
        with SessionLocal() as db:
            customer = attach_phone(db, get_or_create_session(db, self.id), phone)
            db.commit()
            return customer.id


def unique_phone() -> str:
    """SĐT riêng cho mỗi test: customers.phone_number là unique."""
    return "09" + f"{uuid.uuid4().int % 10**8:08d}"


def mentions(reply: str, markers) -> bool:
    return any(m in reply.lower() for m in markers)


def questions(reply: str) -> int:
    return reply.count("?")


def supported_amounts() -> set[str]:
    """Số tiền có nguồn (chỉ so chữ số): giá catalog, chi phí mốc bảo dưỡng, số trong tài liệu RAG."""
    with SessionLocal() as db:
        amounts = {str(price) for (price,) in db.query(CatalogItem.list_price_vnd)}
    amounts |= {str(cost) for cost in COST_MAP.values()}
    for doc in KNOWLEDGE_DIR.glob("*.md"):
        amounts |= {a.replace(".", "") for a in re.findall(r"\d{1,3}(?:\.\d{3})+", doc.read_text(encoding="utf-8"))}
    return amounts


def unsupported_amounts(reply: str) -> list[str]:
    known = supported_amounts()
    return [a for a in AMOUNT.findall(reply) if a.replace(".", "") not in known]
