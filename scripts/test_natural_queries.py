#!/usr/bin/env python3
"""Test various natural language queries with semantic search."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent.llm import generate_with_tools
from app.agent.nodes.sales import PRODUCT_SEARCH_TOOLS, SALES_SYSTEM_PROMPT, _execute_tool


def test_query(query: str):
    """Test a single query."""
    user_msg = f'Tin nhắn của khách: {query}\n\nThông tin đã biết về khách:\nChưa có thông tin về nhu cầu khách.'

    print("="*60)
    print(f"Query: {query}")
    print("="*60)

    result = generate_with_tools(
        system_prompt=SALES_SYSTEM_PROMPT,
        user_message=user_msg,
        tools=PRODUCT_SEARCH_TOOLS,
        tool_executor=_execute_tool
    )

    print(f"\nTools called: {len(result['tool_calls'])}")
    for tc in result['tool_calls']:
        print(f"  - {tc['name']}: {tc['input']}")
        if 'result' in tc and tc['result']:
            if 'products' in tc['result']:
                print(f"    → {len(tc['result']['products'])} products")
            elif 'message' in tc['result']:
                print(f"    → {tc['result']['message']}")

    print(f"\nResponse:\n{result['final_response']}\n")


def main():
    # Test various natural language queries
    queries = [
        "tìm xe rẻ và bền",
        "xe tiết kiệm nhiên liệu",
        "xe chở hàng nặng",
        "xe phù hợp chạy đường dài",
        "xe giá rẻ chất lượng tốt",
    ]

    for query in queries:
        test_query(query)
        print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    sys.exit(main())
