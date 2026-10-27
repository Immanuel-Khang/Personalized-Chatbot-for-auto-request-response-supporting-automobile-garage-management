"""Test proper sales-to-contract transition flow."""
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
    session_id = f"sales-to-contract-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("SALES → CONTRACT TRANSITION TEST")
    print("=" * 80)
    print()

    turns = [
        ("User", "Tôi muốn mua xe tải"),
        ("Expected", "Sales discovery questions"),
        ("User", "Ngân sách 500 triệu"),
        ("Expected", "Sales shows vehicles in budget or asks more details"),
        ("User", "Tôi chọn Aumark S"),
        ("Expected", "Sales confirms selection or shows Aumark S details"),
        ("User", "Tôi muốn mua xe này"),
        ("Expected", "NOW contract should start - asking for name, phone, address, etc."),
    ]

    conversation = [
        "Tôi muốn mua xe tải",
        "Ngân sách 500 triệu",
        "Tôi chọn Aumark S",
        "Tôi muốn mua xe này",
    ]

    for i, message in enumerate(conversation, 1):
        print(f"Turn {i}")
        print(f"User: {message}")
        result = chat(client, session_id, message)
        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
        else:
            print(f"Bot: {result.get('reply', 'NO REPLY')[:200]}...")
            if 'trace' in result:
                agents = [node for node in result['trace'] if node in ['sales', 'contract', 'appointment', 'knowledge', 'conversation']]
                print(f"Agents: {', '.join(agents)}")
        print()

    print("=" * 80)
    print("Expected flow: sales → sales → sales → contract")
    print("Contract should ONLY start after explicit purchase decision on specific model")
    print("=" * 80)

if __name__ == "__main__":
    main()
