"""Test that sales agent proactively searches when customer provides criteria."""
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
    session_id = f"proactive-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("TEST: Proactive Product Search with Criteria")
    print(f"Session: {session_id}")
    print("=" * 80)
    print()

    turns = [
        ("Chào bạn", "Initial greeting"),
        ("Tôi muốn tư vấn xe", "Request consultation"),
        ("Chở hàng trên 10 tấn và đường phố", "Provide specific criteria"),
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
                product_count = reply.lower().count('giá niêm yết')
                print(f"Products shown: {product_count}")

                if product_count >= 1:
                    print("✓ Bot shows products instead of asking more questions")
                else:
                    print("✗ Bot did not show products - still asking questions")

                # Check if bot is repeating the same question
                if "loại hàng hoặc khối lượng" in reply.lower() and "ngân sách" in reply.lower():
                    print("✗ Bot is repeating previous questions (frustrating)")
                else:
                    print("✓ Bot is not repeating previous questions")

                if 'sales' in result.get('trace', []):
                    print("✓ Routed to sales agent")
                else:
                    print(f"✗ Expected sales agent, got: {result.get('trace', [])}")
        print()

    print("=" * 80)

if __name__ == "__main__":
    main()
