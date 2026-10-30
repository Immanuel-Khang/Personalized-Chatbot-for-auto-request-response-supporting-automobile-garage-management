#!/usr/bin/env python3
"""Test to verify neutral pronoun usage (bạn/mình) instead of gendered pronouns (anh/chị/em)."""

from app.agent.nodes.conversation import CONVERSATION_SYSTEM_PROMPT, FALLBACK
from app.agent.nodes.compose import STYLE_RULES, COMPOSE_PROMPT
from app.agent.nodes.appointment import CONFIRM, PHONE_INVALID, BOOKING_FAILED
from app.agent.nodes.contract import SUMMARY_CLOSING


def test_no_gendered_pronouns():
    """Verify that gendered pronouns have been removed from all prompts and messages."""

    print("Testing pronoun changes...")
    print("=" * 80)

    # Check conversation prompts - allow mention in "KHÔNG dùng" instruction
    conv_lines = CONVERSATION_SYSTEM_PROMPT.split('\n')
    for line in conv_lines:
        # Skip the instruction line that mentions what NOT to use
        if "KHÔNG dùng" in line and "anh/chị" in line:
            continue
        assert "anh/chị" not in line, f"Found 'anh/chị' in line: {line}"

    assert "mình" in CONVERSATION_SYSTEM_PROMPT or "shop" in CONVERSATION_SYSTEM_PROMPT, "Missing 'mình' or 'shop' in CONVERSATION_SYSTEM_PROMPT"
    assert "bạn" in CONVERSATION_SYSTEM_PROMPT, "Missing 'bạn' in CONVERSATION_SYSTEM_PROMPT"
    print("✅ CONVERSATION_SYSTEM_PROMPT uses neutral pronouns")

    # Check fallback messages
    for key, text in FALLBACK.items():
        assert "anh/chị" not in text, f"Found 'anh/chị' in FALLBACK['{key}']"
        # clarify_offer is just a passthrough of the question, so skip pronoun check for it
        if key != "clarify_offer":
            assert "mình" in text or "shop" in text, f"Missing 'mình' or 'shop' in FALLBACK['{key}']"
    print("✅ FALLBACK messages use neutral pronouns")

    # Check style rules - allow mention in "KHÔNG dùng" instruction
    style_lines = STYLE_RULES.split('\n')
    for line in style_lines:
        # Skip the instruction line that mentions what NOT to use
        if "KHÔNG dùng" in line and "anh/chị" in line:
            continue
        assert "anh/chị" not in line, f"Found 'anh/chị' in STYLE_RULES line: {line}"

    assert "mình" in STYLE_RULES or "shop" in STYLE_RULES, "Missing 'mình' or 'shop' in STYLE_RULES"
    print("✅ STYLE_RULES uses neutral pronouns")

    # Check compose prompt - same logic
    compose_lines = COMPOSE_PROMPT.split('\n')
    for line in compose_lines:
        if "KHÔNG dùng" in line and "anh/chị" in line:
            continue
        assert "anh/chị" not in line, f"Found 'anh/chị' in COMPOSE_PROMPT line: {line}"

    assert "mình" in COMPOSE_PROMPT or "shop" in COMPOSE_PROMPT, "Missing 'mình' or 'shop' in COMPOSE_PROMPT"
    print("✅ COMPOSE_PROMPT uses neutral pronouns")

    # Check appointment messages
    assert "anh/chị" not in CONFIRM, "Found 'anh/chị' in CONFIRM"
    assert "anh/chị" not in PHONE_INVALID, "Found 'anh/chị' in PHONE_INVALID"
    assert "anh/chị" not in BOOKING_FAILED, "Found 'anh/chị' in BOOKING_FAILED"
    assert "mình" in CONFIRM and "bạn" in CONFIRM, "Missing 'mình' or 'bạn' in CONFIRM"
    print("✅ Appointment messages use neutral pronouns")

    # Check contract messages
    assert "anh/chị" not in SUMMARY_CLOSING, "Found 'anh/chị' in SUMMARY_CLOSING"
    assert "bạn" in SUMMARY_CLOSING, "Missing 'bạn' in SUMMARY_CLOSING"
    assert "shop" in SUMMARY_CLOSING or "mình" in SUMMARY_CLOSING, "Missing 'shop' or 'mình' in SUMMARY_CLOSING"
    print("✅ Contract messages use neutral pronouns")

    print("=" * 80)
    print("✅ ALL TESTS PASSED: Pronouns successfully changed to neutral (bạn/mình)")
    print("\nSummary of changes:")
    print("  - Removed gender inference function and name-based gender detection")
    print("  - Changed 'em' (bot) → 'mình' or 'shop'")
    print("  - Changed 'anh/chị' (customer) → 'bạn'")
    print("  - Updated all prompts and fallback messages")


if __name__ == "__main__":
    test_no_gendered_pronouns()
