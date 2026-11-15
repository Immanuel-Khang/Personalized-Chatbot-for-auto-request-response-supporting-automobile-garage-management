#!/usr/bin/env python3
"""Test sales agent tool selection with actual LLM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent.state import AgentState
from app.agent.nodes.sales import sales


def main():
    """Test with the exact user query that should trigger search_by_description."""

    from types import SimpleNamespace

    # Simulate the state from the actual conversation
    state = {
        "user_text": "mình tên là dương định khang, mình muốn tìm xe rẻ, bền và tiết kiệm nhiên liệu chút",
        "task_text": "tìm xe rẻ, bền và tiết kiệm nhiên liệu",
        "task_ids": ["sales_1"],
        "conversation_interpretation": SimpleNamespace(
            tasks=[SimpleNamespace(
                task_id="sales_1",
                action="sales_inquiry",
                car_model=None
            )]
        ),
        "known_facts": {},
        "history": []
    }

    print("Testing sales agent with query: 'tìm xe rẻ, bền và tiết kiệm nhiên liệu'")
    print("="*60)

    result = sales(state)

    print("\n" + "="*60)
    print("RESULT:")
    print("="*60)

    if result.get("answers"):
        reply = result["answers"][0].get("text", "")
        print(f"\nBot reply:\n{reply}")

    print("\n" + "="*60)
    print("✓ Test complete")

    # Check trace.json for tool calls
    print("\nCheck trace.json to see which tools were called")


if __name__ == "__main__":
    sys.exit(main())
