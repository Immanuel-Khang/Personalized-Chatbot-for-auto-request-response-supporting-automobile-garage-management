#!/usr/bin/env python3
"""Test response timing to measure latency improvements."""
import time
from uuid import uuid4

from app.agent.runner import run_turn


def test_greeting_timing():
    """Test timing for simple greeting (should skip compose LLM)."""
    session_id = str(uuid4())

    print("Testing greeting latency...")
    start = time.time()
    result = run_turn(session_id, None, "Chào bạn", history=[])
    elapsed = time.time() - start

    print(f"\n✅ Response received in {elapsed:.2f}s")
    print(f"Bot: {result.get('draft_reply', '[No reply]')[:200]}...")
    print(f"Trace: {result.get('trace', [])}")

    # Check if compose was called
    trace = result.get('trace', [])
    if 'compose' in trace:
        print("\n⚠️  Compose node was called (expected for greeting)")
    else:
        print("\n❌ Compose node was skipped (unexpected)")


def test_simple_single_intent():
    """Test simple single-intent message that should skip compose."""
    session_id = str(uuid4())
    history = [
        {"role": "user", "content": "Chào bạn"},
        {"role": "assistant", "content": "Dạ chào anh/chị. Anh/chị cần hỗ trợ gì ạ?"}
    ]

    print("\n\nTesting simple query latency...")
    start = time.time()
    result = run_turn(session_id, None, "Giá xe bao nhiêu?", history=history)
    elapsed = time.time() - start

    print(f"\n✅ Response received in {elapsed:.2f}s")
    print(f"Bot: {result.get('draft_reply', '[No reply]')[:200]}...")
    print(f"Trace: {result.get('trace', [])}")

    trace = result.get('trace', [])
    answers_count = len(result.get('answers', []))

    print(f"\nAnswers generated: {answers_count}")
    if 'compose' in trace:
        print("⚠️  Compose node was called")
    else:
        print("✅ Compose node was skipped (latency saved!)")


if __name__ == "__main__":
    test_greeting_timing()
    test_simple_single_intent()
