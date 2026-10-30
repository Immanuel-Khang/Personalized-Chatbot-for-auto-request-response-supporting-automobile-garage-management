#!/usr/bin/env python3
"""
Check if compose.py calls LLM for single-intent clean responses.
This script patches the LLM call to log when it's invoked.
"""
import sys
from uuid import uuid4

# Patch the LLM call to detect when it's used
from app.agent import llm

original_generate = llm.generate_with_tools

call_log = []

def patched_generate(system_prompt, user_prompt, tools, submit_fn, **kwargs):
    """Log LLM calls and track what triggered them."""
    call_log.append({
        'system_prompt_preview': system_prompt[:100],
        'user_prompt_preview': user_prompt[:150],
    })
    print(f"\n🔴 LLM CALLED in compose!")
    print(f"   System: {system_prompt[:80]}...")
    print(f"   User prompt: {user_prompt[:120]}...")
    return original_generate(system_prompt, user_prompt, tools, submit_fn, **kwargs)

llm.generate_with_tools = patched_generate

# Now run tests
from app.agent.runner import run_turn


def test_single_intent():
    """Test single-intent messages that should skip compose LLM."""

    print("=" * 80)
    print("TEST: Single-Intent Clean Responses (Should NOT call LLM in compose)")
    print("=" * 80)

    test_cases = [
        {
            "name": "Simple greeting",
            "message": "Chào bạn",
            "history": [],
        },
        {
            "name": "Simple thanks",
            "message": "Cảm ơn",
            "history": [
                {"role": "user", "content": "Chào bạn"},
                {"role": "assistant", "content": "Chào bạn"}
            ],
        },
        {
            "name": "Name response",
            "message": "Tên tôi là Minh",
            "history": [
                {"role": "user", "content": "Chào"},
                {"role": "assistant", "content": "Tên bạn là gì ạ?"}
            ],
        },
    ]

    for i, test in enumerate(test_cases, 1):
        session_id = str(uuid4())
        call_log.clear()

        print(f"\n{'─' * 80}")
        print(f"Test {i}: {test['name']}")
        print(f"Message: '{test['message']}'")
        print(f"{'─' * 80}")

        result = run_turn(session_id, None, test['message'], history=test['history'])

        # Check if compose was called
        trace = result.get('trace', [])
        answers = result.get('answers', [])
        draft = result.get('draft_reply', '')

        compose_in_trace = 'compose' in trace
        llm_calls = len(call_log)

        print(f"\n✓ Response: {draft[:100]}...")
        print(f"\n📊 Analysis:")
        print(f"   Compose in trace: {compose_in_trace}")
        print(f"   LLM calls in compose: {llm_calls}")
        print(f"   Answers count: {len(answers)}")

        if llm_calls == 0 and len(answers) == 1:
            print(f"   ✅ PASSED: Single clean answer, no LLM composition")
        elif llm_calls > 0:
            print(f"   ❌ FAILED: LLM was called {llm_calls} time(s) for single-intent response")
        else:
            print(f"   ⚠️  UNCLEAR: Check manually")

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("If you see '🔴 LLM CALLED in compose!' above, the optimization isn't working.")
    print("If you DON'T see it, then single-intent responses are skipping LLM ✅")
    print("=" * 80)


if __name__ == "__main__":
    test_single_intent()
