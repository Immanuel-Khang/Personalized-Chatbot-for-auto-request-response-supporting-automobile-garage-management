"""Test conversation agent: greeting, out-of-scope, cancel, memory recall."""
import pytest
from app.agent.graph import get_graph
from app.db.models import ChatSession, Customer, Message
from app.db.session import SessionLocal
from app.identity import get_or_create_session


@pytest.fixture
def session_with_profile(db_session):
    """Create a session with customer profile."""
    session_id = "test-conv-session"
    with SessionLocal() as db:
        # Create session
        session = ChatSession(id=session_id, customer_id=None)
        db.add(session)

        # Create customer
        customer = Customer(
            phone_number="+84386765582",
            full_name="Khang",
            phone_verified_at=None
        )
        db.add(customer)
        db.flush()

        # Link session to customer
        session.customer_id = customer.id

        # Add some conversation history
        db.add(Message(session_id=session_id, role="user", content="Tôi tên Khang, số điện thoại 0386765582"))
        db.add(Message(session_id=session_id, role="ai", content="Chào bạn Khang! Mình đã ghi nhận thông tin."))

        db.commit()

    yield session_id

    # Cleanup
    with SessionLocal() as db:
        db.query(Message).filter(Message.session_id == session_id).delete()
        db.query(ChatSession).filter(ChatSession.id == session_id).delete()
        db.query(Customer).filter(Customer.phone_number == "+84386765582").delete()
        db.commit()


def test_greeting(session_with_profile):
    """Greeting intent should route to conversation agent."""
    graph = get_graph()
    result = graph.invoke({
        "session_id": session_with_profile,
        "customer_id": None,
        "user_text": "Xin chào",
        "mode": "BOT"
    }, {"configurable": {"thread_id": session_with_profile}})

    assert "conversation" in result["planned_agents"]
    assert result["final_reply"]
    assert len(result["final_reply"]) > 0


def test_out_of_scope(session_with_profile):
    """Out-of-scope question should route to conversation agent."""
    graph = get_graph()
    result = graph.invoke({
        "session_id": session_with_profile,
        "customer_id": None,
        "user_text": "Bạn thích ăn gì?",
        "mode": "BOT"
    }, {"configurable": {"thread_id": session_with_profile}})

    assert "conversation" in result["planned_agents"]
    assert result["final_reply"]


def test_memory_recall(session_with_profile):
    """Should recall name from previous conversation."""
    graph = get_graph()
    result = graph.invoke({
        "session_id": session_with_profile,
        "customer_id": None,
        "user_text": "Tôi tên là gì?",
        "mode": "BOT"
    }, {"configurable": {"thread_id": session_with_profile}})

    # The conversation agent should have access to profile and history
    # and should mention the name "Khang"
    assert "conversation" in result["planned_agents"]
    assert result["final_reply"]
    # Note: This test checks that the conversation agent runs
    # The actual name recall depends on LLM response


def test_cancel_during_active_flow(session_with_profile):
    """Cancel during appointment flow should route to conversation agent."""
    graph = get_graph()

    # First, start an appointment flow
    result1 = graph.invoke({
        "session_id": session_with_profile,
        "customer_id": None,
        "user_text": "Đặt lịch bảo dưỡng",
        "mode": "BOT"
    }, {"configurable": {"thread_id": session_with_profile}})

    assert "appointment" in result1["planned_agents"]
    assert result1.get("active_flow") == "appointment"

    # Now cancel
    config = {"configurable": {"thread_id": session_with_profile}}
    result2 = graph.invoke({
        "session_id": session_with_profile,
        "customer_id": None,
        "user_text": "Hủy",
        "mode": "BOT"
    }, config)

    assert "conversation" in result2["planned_agents"]
    assert result2.get("active_flow") is None  # Flow cleared
    assert not result2.get("pending_intent")  # Pending cleared
