"""Test the exact conversation sequence the user reported as frustrating."""
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
    session_id = f"user-issue-{uuid.uuid4().hex[:8]}"

    print("=" * 80)
    print("USER REPORTED ISSUE: Exact Conversation Sequence")
    print(f"Session: {session_id}")
    print("=" * 80)
    print()

    # Exact conversation from user's report
    turns = [
        "Chào bạn",
        "Tôi muốn tư vấn xe",
        "Chở hàng trên 10 tấn và đường phố",
    ]

    for i, message in enumerate(turns, 1):
        print(f"Bạn: {message}")
        result = chat(client, session_id, message)

        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
            break

        reply = result.get('reply', 'NO REPLY')
        print(f"Bot: {reply}")
        print(f"[BOT] {' → '.join(result.get('trace', []))}")
        print()

        # After turn 3, check if the issue is fixed
        if i == 3:
            print("=" * 80)
            print("VERIFICATION:")
            print("=" * 80)

            product_count = reply.lower().count('giá niêm yết')
            print(f"✓ Products shown: {product_count}")

            # Check if the bot is asking the same questions again
            is_repeating = ("loại hàng hoặc khối lượng" in reply.lower() and
                           "ngân sách dự kiến" in reply.lower() and
                           product_count == 0)

            if is_repeating:
                print("✗ ISSUE NOT FIXED: Bot is still asking for the same information")
                print("   Customer said 'trên 10 tấn' but bot asks for 'khối lượng thường chở'")
            else:
                print("✓ ISSUE FIXED: Bot shows products instead of repeating questions")
                print("   Bot proactively searched and displayed relevant vehicles")
            print()

if __name__ == "__main__":
    main()
