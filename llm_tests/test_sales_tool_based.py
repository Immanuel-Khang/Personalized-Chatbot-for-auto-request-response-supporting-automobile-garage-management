"""Test the new tool-based sales agent with the user's reported issue."""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

os.environ['LLM_PROVIDER'] = 'openai'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://garage:garage@127.0.0.1:5432/garage?connect_timeout=5'
os.environ['EMBEDDING_PROVIDER'] = 'none'

from app.agent.state import AgentState
from app.agent.nodes.sales import sales

def test_scenario():
    """Test the exact scenario the user reported."""

    print("=" * 80)
    print("TEST: Tool-Based Sales Agent - User Reported Scenario")
    print("=" * 80)
    print()

    # Simulate state after the conversation history where bot asked about tonnage
    # and customer replied "10 tấn"

    history = [
        {"role": "user", "content": "Chào bạn tôi muốn mua xe"},
        {"role": "ai", "content": "Dạ mình chào bạn ạ 😊 Bạn đang muốn mình hỗ trợ tư vấn xe tải chở hàng hay cần báo giá/đặt lịch xem xe ạ?"},
        {"role": "user", "content": "Tôi muốn được hỗ trợ tư vấn xe tải"},
        {"role": "ai", "content": "Dạ, mình có thể hỗ trợ bạn chọn xe phù hợp ạ. Bạn dự định sử dụng xe chủ yếu để làm gì, ví dụ chở hàng, phục vụ gia đình hay đi lại hằng ngày?"},
        {"role": "user", "content": "Chở hàng"},
        {"role": "ai", "content": "Dạ với nhu cầu chở hàng... Bạn cho mình biết bạn dự định lắp thùng theo dạng nào (mui bạt/thùng kín/cẩu tự hành…) và chở khoảng bao nhiêu tấn mỗi chuyến để mình hướng dẫn cấu hình lắp thùng phù hợp hơn."},
    ]

    state = AgentState(
        session_id="test_tools",
        user_text="10 tấn",
        task_text="10 tấn",
        history=history,
        known_facts={
            "intended_use": {"key": "intended_use", "value": "chở hàng", "source": "USER_EXPLICIT"},
            "payload_tons": {"key": "payload_tons", "value": 10, "source": "USER_EXPLICIT"}
        },
        trace=[]
    )

    print("User: 10 tấn")
    print("Known facts: intended_use=chở hàng, payload_tons=10")
    print()
    print("Calling sales...")
    print()

    result = sales(state)

    print("Bot response:")
    print("-" * 80)
    for ans in result.get("answers", []):
        print(ans.get("text", "NO TEXT"))
    print("-" * 80)
    print()

    # Check if response is appropriate
    response_text = result.get("answers", [{}])[0].get("text", "")

    if "Dạ không sao ạ" in response_text and "catalog bên em có" in response_text:
        print("✗ Bot reset to discovery mode (BAD)")
    elif "10 tấn" in response_text or "tải" in response_text.lower():
        print("✓ Bot acknowledges the payload")
    else:
        print("? Response unclear")

    if any(p_dict for p_dict in result.get("selected_entities", [])):
        print(f"✓ Bot showed {len(result.get('selected_entities', []))} products")
    else:
        print("✗ Bot did not show any products")

    print()
    print("=" * 80)

if __name__ == "__main__":
    test_scenario()
