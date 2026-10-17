"""[TRACK C] Profile Service: tra cứu thông tin khách và lịch sử hội thoại từ DB."""
from app.contracts.schemas import ProfileInfo
from app.db.models import ChatSession, Customer, Message
from app.db.session import SessionLocal


class DbProfileService:
    def get_profile(self, session_id: str) -> ProfileInfo:
        """Trả thông tin khách hàng gắn với phiên (nếu có)."""
        with SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            if not session or not session.customer_id:
                return ProfileInfo()

            customer = db.get(Customer, session.customer_id)
            if not customer:
                return ProfileInfo()

            return ProfileInfo(
                name=customer.full_name,
                phone=customer.phone_number,
                verified=customer.phone_verified_at is not None
            )

    def get_conversation_summary(self, session_id: str, limit: int = 10) -> str:
        """Trả N tin nhắn gần nhất (user/ai) để LLM có ngữ cảnh hội thoại."""
        with SessionLocal() as db:
            messages = (db.query(Message)
                       .filter(Message.session_id == session_id,
                               Message.role.in_(["user", "ai"]))
                       .order_by(Message.created_at.desc())
                       .limit(limit)
                       .all())

            # Đảo ngược để tin cũ trước, mới sau
            messages.reverse()

            lines = []
            for msg in messages:
                role = "Khách" if msg.role == "user" else "Bot"
                lines.append(f"{role}: {msg.content}")

            return "\n".join(lines) if lines else "(Chưa có lịch sử hội thoại)"
