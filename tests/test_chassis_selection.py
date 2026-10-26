"""Test that bot handles chassis selection without resetting context."""
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
    session_id = f"chassis-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("TEST: Chassis Selection Without Context Reset")
    print(f"Session: {session_id}")
    print("=" * 80)
    print()

    turns = [
        ("Chào bạn", "Initial greeting"),
        ("Tôi muốn được hỗ trợ tư vấn xe tải", "Request truck consultation"),
        ("Chở hàng", "Confirm cargo use"),
        ("Tôi muốn chassis", "Select chassis from previously shown options"),
    ]

    for i, (message, description) in enumerate(turns, 1):
        print(f"Turn {i}: {description}")
        print(f"Bạn: {message}")
        result = chat(client, session_id, message)

        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
            break

        reply = result.get('reply', 'NO REPLY')
        print(f"Bot: {reply[:200]}...")
        if 'trace' in result:
            print(f"[BOT] {' → '.join(result['trace'])}")
        print()

        # Check Turn 4 expectations
        if i == 4:
            print("=" * 80)
            print("VERIFICATION:")
            print("=" * 80)

            # Check if bot is asking discovery questions again
            is_reset = ("dự định sử dụng xe" in reply.lower() or
                       "chủ yếu để làm gì" in reply.lower())

            if is_reset:
                print("✗ ISSUE: Bot reset context and asks discovery questions again")
                print("   Customer already said 'chở hàng' but bot forgot")
            else:
                print("✓ Bot maintains context and provides chassis details")

            # Check if bot mentions chassis/ISUZU
            if "chassis" in reply.lower() or "isuzu" in reply.lower() or "frr90ne5" in reply.lower():
                print("✓ Bot discusses the chassis product customer selected")
            else:
                print("✗ Bot does not discuss chassis")

            if 'sales' in result.get('trace', []):
                print("✓ Routes to sales agent")
            else:
                print(f"✗ Routes to {result.get('trace', [])} instead of sales")

if __name__ == "__main__":
    main()
