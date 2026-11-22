#!/usr/bin/env python3
"""Simulate real conversations to test semantic search on live system."""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent.graph import AgentGraph
from app.db.session import SessionLocal
from app.db.models import Session, Message


def create_conversation(customer_name: str, messages: list[str]) -> dict:
    """Create a real conversation session and process messages."""
    db = SessionLocal()

    # Create session
    session = Session(
        customer_name=customer_name,
        customer_phone="0900000000",
        source="web",
        status="ACTIVE"
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    print(f"\n{'='*70}")
    print(f"Cuộc hội thoại với: {customer_name}")
    print(f"Session ID: {session.id}")
    print(f"Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    graph = AgentGraph()
    conversation = []

    for i, user_msg in enumerate(messages, 1):
        print(f"[{i}] Khách: {user_msg}")

        # Save user message
        msg = Message(
            session_id=session.id,
            role="user",
            content=user_msg,
            timestamp=datetime.now()
        )
        db.add(msg)
        db.commit()

        # Process with agent
        result = graph.run(session.id, user_msg)
        response = result.get("response", "")

        # Save assistant response
        msg = Message(
            session_id=session.id,
            role="assistant",
            content=response,
            timestamp=datetime.now()
        )
        db.add(msg)
        db.commit()

        print(f"    Bot: {response}\n")

        conversation.append({
            "user": user_msg,
            "assistant": response
        })

    db.close()
    return {
        "session_id": session.id,
        "customer": customer_name,
        "conversation": conversation
    }


def main():
    """Run multiple test conversations."""

    conversations = [
        {
            "customer": "Anh Minh - Test RAG rẻ và bền",
            "messages": [
                "chào shop, mình muốn tìm xe rẻ và bền chút",
            ]
        },
        {
            "customer": "Chị Lan - Test tiết kiệm nhiên liệu",
            "messages": [
                "xe nào tiết kiệm nhiên liệu nhất vậy shop?",
            ]
        },
        {
            "customer": "Anh Tuấn - Test chở hàng nặng",
            "messages": [
                "mình cần xe chở hàng nặng, shop tư vấn giúp",
            ]
        },
        {
            "customer": "Chị Hương - Test đường dài",
            "messages": [
                "xe nào phù hợp chạy đường dài, bền bỉ?",
            ]
        },
        {
            "customer": "Anh Nam - Test giá rẻ chất lượng",
            "messages": [
                "tìm xe giá rẻ nhưng chất lượng tốt",
            ]
        }
    ]

    results = []
    for conv in conversations:
        try:
            result = create_conversation(conv["customer"], conv["messages"])
            results.append(result)
        except Exception as e:
            print(f"❌ Lỗi khi xử lý cuộc hội thoại {conv['customer']}: {e}")
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "="*70)
    print("TỔNG KẾT KẾT QUẢ TEST")
    print("="*70)
    print(f"Tổng số cuộc hội thoại: {len(results)}")
    for r in results:
        print(f"\n✓ Session {r['session_id']}: {r['customer']}")
        for msg in r['conversation']:
            print(f"  Khách: {msg['user'][:50]}...")
            print(f"  Bot: {msg['assistant'][:100]}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
