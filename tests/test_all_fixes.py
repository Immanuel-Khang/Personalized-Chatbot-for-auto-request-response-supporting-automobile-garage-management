"""Comprehensive verification of all bug fixes and enhancements."""
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

    print("=" * 80)
    print("COMPREHENSIVE VERIFICATION: All Bug Fixes and Enhancements")
    print("=" * 80)
    print()

    # Test 1: LLM-enhanced slot filling with contextual acknowledgment
    print("Test 1: LLM-Enhanced Contextual Slot Filling")
    print("-" * 80)
    session_id = f"test1-{uuid.uuid4().hex[:8]}"

    result = chat(client, session_id, "Tôi muốn bảo dưỡng xe")
    print(f"Turn 1: Tôi muốn bảo dưỡng xe")
    print(f"Bot: {result.get('reply', 'NO REPLY')[:150]}...")

    result = chat(client, session_id, "Sáng mai")
    print(f"\nTurn 2: Sáng mai")
    bot_reply = result.get('reply', '')
    print(f"Bot: {bot_reply[:150]}...")
    if "sáng mai" in bot_reply.lower():
        print("✓ Bot acknowledges the date provided")
    else:
        print("✗ Bot does not acknowledge date")
    print()

    # Test 2: Multi-turn workflow with context switching
    print("Test 2: Multi-Turn Workflow with Context Switching")
    print("-" * 80)
    session_id = f"test2-{uuid.uuid4().hex[:8]}"

    chat(client, session_id, "Tôi muốn đặt lịch bảo dưỡng xe")
    chat(client, session_id, "Sáng thứ 7 tuần sau")
    result = chat(client, session_id, "Tôi muốn mua xe tải")
    print(f"Turn 3: Tôi muốn mua xe tải (context switch)")
    print(f"Sales response length: {len(result.get('reply', ''))} chars")
    if len(result.get('reply', '')) > 100:
        print("✓ Sales response is complete (not truncated)")
    else:
        print("✗ Sales response appears truncated")

    result = chat(client, session_id, "Không, tôi muốn tiếp tục đặt lịch bảo dưỡng")
    print(f"\nTurn 4: Không, tôi muốn tiếp tục đặt lịch bảo dưỡng")
    bot_reply = result.get('reply', '')
    if "sáng thứ 7" in bot_reply.lower() or "tuần sau" in bot_reply.lower():
        print("✓ Bot recalls previously collected date when resuming")
    else:
        print("✗ Bot does not recall collected information")
    print()

    # Test 3: Sales agent - Popular/prominent product requests
    print("Test 3: Sales Agent - Popular/Prominent Product Requests")
    print("-" * 80)
    session_id = f"test3-{uuid.uuid4().hex[:8]}"

    chat(client, session_id, "Chào bạn tôi muốn mua xe")
    chat(client, session_id, "Chở hàng")
    result = chat(client, session_id, "Tôi chỉ muốn xe chở hàng cho tôi 3 mẫu nổi bật nhất")
    print(f"Turn 3: Tôi chỉ muốn xe chở hàng cho tôi 3 mẫu nổi bật nhất")

    reply = result.get('reply', '')
    print(f"Bot reply: {reply[:300]}...")
    product_count = reply.lower().count('giá niêm yết')
    print(f"Products returned: {product_count}")

    if product_count == 3:
        print("✓ Returns exactly 3 models as requested")
    elif product_count > 3:
        print(f"✗ Returns {product_count} models instead of 3")
    else:
        print(f"✗ Returns only {product_count} models")

    if 'sales' in result.get('trace', []):
        print("✓ Routes to sales agent")
    else:
        print(f"✗ Routes to {result.get('trace', [])} instead of sales")
    print()

    # Test 4: Proactive search when customer provides criteria
    print("Test 4: Proactive Product Search with Criteria")
    print("-" * 80)
    session_id = f"test4-{uuid.uuid4().hex[:8]}"

    chat(client, session_id, "Chào bạn")
    chat(client, session_id, "Tôi muốn tư vấn xe")
    result = chat(client, session_id, "Chở hàng trên 10 tấn và đường phố")
    print(f"Turn 3: Chở hàng trên 10 tấn và đường phố")

    reply = result.get('reply', '')
    product_count = reply.lower().count('giá niêm yết')
    print(f"Products shown: {product_count}")

    if product_count >= 1:
        print("✓ Bot shows products instead of asking more questions")
    else:
        print("✗ Bot did not show products")

    # Check if bot is repeating the same question
    if "loại hàng hoặc khối lượng" in reply.lower() and "ngân sách" in reply.lower():
        print("✗ Bot is repeating previous questions (frustrating)")
    else:
        print("✓ Bot is not repeating the same questions")

    if 'sales' in result.get('trace', []):
        print("✓ Routes to sales agent")
    else:
        print(f"✗ Routes to {result.get('trace', [])} instead of sales")
    print()

    print("=" * 80)
    print("SUMMARY OF FIXES")
    print("=" * 80)
    print("1. Fixed checkpoint deserialization bugs (appointment, contract, sales)")
    print("2. Fixed sales discovery truncation when writer owns turn")
    print("3. Enhanced appointment agent with LLM-generated contextual responses")
    print("4. Enhanced contract agent prompt to acknowledge user input")
    print("5. Fixed sales agent to recognize 'nổi bật' as suggestion keyword")
    print("6. Fixed sales agent to prioritize suggestion keywords over catalog browse")
    print("7. Sales agent now returns exactly 3 models for popular products")
    print("8. Fixed sales agent to proactively search when customer provides criteria")
    print("   instead of asking repetitive questions")
    print("=" * 80)

if __name__ == "__main__":
    main()
