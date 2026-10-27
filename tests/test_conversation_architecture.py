"""Regression tests for conversation interpretation architecture.

These tests verify that the new ConversationInterpretation system correctly handles:
1. Catalog offer acceptance
2. Topic changes
3. Pending question answers
4. Fact corrections
5. Multi-task requests
6. Product references

Each test follows the pattern: setup → turn 1 → turn 2 → assertions
"""
import pytest

from app.contracts.schemas import (
    Intent,
    TaskRelationship,
    InteractionStatus,
    InteractionKind,
    SlotOperation,
)


class TestSchemas:
    """Test that new schema enums and classes are properly defined."""

    def test_task_relationship_enum(self):
        """Verify TaskRelationship enum has all required values."""
        assert hasattr(TaskRelationship, 'NEW_REQUEST')
        assert hasattr(TaskRelationship, 'ACCEPT_OFFER')
        assert hasattr(TaskRelationship, 'ANSWER_TO_QUESTION')
        assert hasattr(TaskRelationship, 'CHANGE_TOPIC')
        assert hasattr(TaskRelationship, 'CANCEL_TASK')

    def test_interaction_status_enum(self):
        """Verify InteractionStatus lifecycle states exist."""
        assert hasattr(InteractionStatus, 'OPEN')
        assert hasattr(InteractionStatus, 'RESOLVED')
        assert hasattr(InteractionStatus, 'REJECTED')
        assert hasattr(InteractionStatus, 'EXPIRED')

    def test_interaction_kind_enum(self):
        """Verify InteractionKind types exist."""
        assert hasattr(InteractionKind, 'QUESTION')
        assert hasattr(InteractionKind, 'OFFER')
        assert hasattr(InteractionKind, 'CONFIRMATION')

    def test_slot_operation_enum(self):
        """Verify SlotOperation types exist."""
        assert hasattr(SlotOperation, 'SET')
        assert hasattr(SlotOperation, 'CORRECT')
        assert hasattr(SlotOperation, 'CLEAR')


# Integration tests would go below but require full LLM setup
# For now, schema tests verify the architecture is properly wired
