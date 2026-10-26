"""Test the full conversation with the new tool-based sales agent."""
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
    session_id = f"tooltest-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("TEST: Full E2E with Tool-Based Sales Agent")
    print(f"Session: {session_id}")
    print("=" * 80)
    print()

    turns = [
        "Chào bạn",
        "Tôi muốn được hỗ trợ tư vấn xe tải",
        "Chở hàng",
        "10 tấn",
        "Tôi muốn chassis",
    ]

    for i, message in enumerate(turns, 1):
        print(f"Turn {i}:")
        print(f"User: {message}")
        result = chat(client, session_id, message)

        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
            break

        reply = result.get('reply', 'NO REPLY')
        print(f"Bot: {reply[:200]}{'...' if len(reply) > 200 else ''}")

        if 'trace' in result:
            print(f"Trace: {' → '.join(result['trace'])}")

        # Check critical expectations
        if i == 4:  # After "10 tấn"
            print("\nExpectations for Turn 4 (10 tấn):")
            if "Dạ không sao ạ" in reply and "catalog bên em có" in reply:
                print("✗ Bot reset to discovery (THIS IS THE BUG)")
            else:
                print("✓ Bot did not reset to discovery")

            if "10" in reply or "tấn" in reply.lower():
                print("✓ Bot acknowledges the payload")
            else:
                print("✗ Bot does not acknowledge payload")

            if "giá niêm yết" in reply.lower():
                print("✓ Bot shows products")
            else:
                print("✗ Bot does not show products")

        if i == 5:  # After "Tôi muốn chassis"
            print("\nExpectations for Turn 5 (chassis selection):")
            if "Dạ không sao ạ" in reply and "catalog bên em có" in reply:
                print("✗ Bot reset to discovery again (BAD)")
            else:
                print("✓ Bot maintains context")

            if "chassis" in reply.lower():
                print("✓ Bot discusses chassis")
            else:
                print("✗ Bot does not discuss chassis")

        print()

    print("=" * 80)

if __name__ == "__main__":
    main()
