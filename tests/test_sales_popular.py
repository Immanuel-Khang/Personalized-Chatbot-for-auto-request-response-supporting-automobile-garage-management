"""Test sales agent handling of popular/prominent product requests."""
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
    session_id = f"sales-popular-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("TEST: Sales Agent - Popular/Prominent Product Requests")
    print(f"Session: {session_id}")
    print("=" * 80)
    print()

    turns = [
        ("Chào bạn tôi muốn mua xe", "Initial greeting + purchase intent"),
        ("Chở hàng", "Clarify cargo purpose"),
        ("Tôi chỉ muốn xe chở hàng cho tôi 3 mẫu nổi bật nhất", "Request 3 prominent models"),
    ]

    for i, (message, description) in enumerate(turns, 1):
        print(f"Turn {i}: {description}")
        print(f"User: {message}")
        result = chat(client, session_id, message)
        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
        else:
            reply = result.get('reply', 'NO REPLY')
            print(f"Bot: {reply}")
            if 'trace' in result:
                print(f"Trace: {', '.join(result['trace'])}")

            # Check Turn 3 expectations
            if i == 3:
                print("\nExpectations for Turn 3:")
                if "3 mẫu" in reply or len(reply.split("giá niêm yết")) <= 4:
                    print("✓ Returned 3 models or less")
                else:
                    lines_with_price = [line for line in reply.split('\n') if 'giá niêm yết' in line]
                    print(f"✗ Expected 3 models, got {len(lines_with_price)} models")

                if 'sales' in result.get('trace', []):
                    print("✓ Routed to sales agent")
                else:
                    print(f"✗ Expected sales agent, got: {result.get('trace', [])}")
        print()

    print("=" * 80)

if __name__ == "__main__":
    main()
