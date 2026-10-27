"""Test appointment booking with product reference."""
import os
import sys
import uuid

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

os.environ['LLM_PROVIDER'] = 'openai'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://garage:garage@127.0.0.1:5432/garage?connect_timeout=5'
os.environ['EMBEDDING_PROVIDER'] = 'none'

from fastapi.testclient import TestClient
from app.main import app

def chat(client, session_id, message):
    response = client.post('/chat', json={'session_id': session_id, 'message': message})
    if response.status_code != 200:
        return {"error": response.text, "status": response.status_code}
    return response.json()

def main():
    client = TestClient(app)
    session_id = f"appointment-product-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("APPOINTMENT WITH PRODUCT REFERENCE TEST")
    print("=" * 80)
    print()

    turns = [
        "Chào bạn",
        "Tôi muốn tư vấn xe",
        "Chở hàng",
        "Gửi tôi danh sách tham khảo",
        "Tôi muốn đặt lịch để đến xem xe số 1",
        "Xe số 1 là xe FUSO Canter FI170",
        "Đúng rồi",
        "Sáng thứ 7 tuần sau",
        "0987654321",
    ]

    for i, message in enumerate(turns, 1):
        print(f"Turn {i}")
        print(f"User: {message}")
        result = chat(client, session_id, message)
        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
        else:
            print(f"Bot: {result.get('reply', 'NO REPLY')[:300]}...")
            if 'trace' in result:
                agents = [node for node in result['trace'] if node in ['sales', 'contract', 'appointment', 'knowledge', 'conversation']]
                print(f"Agents: {', '.join(agents)}")
        print()

    print("=" * 80)
    print("Check trace.json for the last turn's preprocessing")
    print("=" * 80)

if __name__ == "__main__":
    main()
