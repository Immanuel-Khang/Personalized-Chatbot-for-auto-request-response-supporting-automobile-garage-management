#!/usr/bin/env python3
"""Direct test of LLM tool calling with logging."""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Setup logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from app.agent.llm import generate_with_tools
from app.agent.nodes.sales import PRODUCT_SEARCH_TOOLS, SALES_SYSTEM_PROMPT, _execute_tool


def main():
    """Test LLM tool calling directly."""

    user_message = """Tin nhắn của khách: mình muốn tìm xe rẻ, bền và tiết kiệm nhiên liệu chút

Thông tin đã biết về khách:
Chưa có thông tin về nhu cầu khách.
"""

    print("="*60)
    print("Testing LLM tool calling")
    print("="*60)
    print(f"\nUser message:\n{user_message}\n")
    print(f"Available tools: {[t['name'] for t in PRODUCT_SEARCH_TOOLS]}\n")
    print("="*60)
    print("Calling LLM...")
    print("="*60)

    result = generate_with_tools(
        system_prompt=SALES_SYSTEM_PROMPT,
        user_message=user_message,
        tools=PRODUCT_SEARCH_TOOLS,
        tool_executor=_execute_tool,
        history=[],
        max_turns=3,
    )

    print("\n" + "="*60)
    print("RESULT")
    print("="*60)

    if result.get("error"):
        print(f"\nError: {result['error']}")

    if result.get("tool_calls"):
        print(f"\nTool calls made: {len(result['tool_calls'])}")
        for i, call in enumerate(result['tool_calls'], 1):
            print(f"\n{i}. Tool: {call['name']}")
            print(f"   Input: {call['input']}")
            if 'result' in call:
                products = call['result'].get('products', [])
                print(f"   Result: {len(products)} products")
    else:
        print("\nNo tool calls made!")

    print(f"\nFinal response:\n{result.get('final_response', 'No response')}")

    print("\n" + "="*60)


if __name__ == "__main__":
    sys.exit(main())
