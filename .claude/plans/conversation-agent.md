# Plan: Add Conversation Agent for Greeting, Memory, and Flexible Appointment Slot-Filling

## Problem

Current implementation uses fixed reply patterns:
1. **Greeting, out-of-scope, cancel** → hardcoded strings in `router.py:24-26`
2. **Appointment slot-filling** → rigid templates in `appointment.py:7-11`
3. **No conversation memory** → "Tôi tên là gì?" fails because there's no memory lookup

This breaks down when users expect contextual responses or natural conversation flow.

## Goal

Replace fixed patterns with LLM-based agents that:
1. Handle greeting/out-of-scope/cancel conversationally with access to session history
2. Look up previously stated information (name, phone) from the database
3. Make appointment slot-filling natural and flexible instead of template-driven

## Architecture Overview

Current flow:
```
preprocess → policy_guard → router → [specialists] → compose → output_guard → respond
```

After changes:
```
preprocess → policy_guard → router → [specialists + conversation] → compose → output_guard → respond
```

The `conversation` agent joins the existing specialists (sales, knowledge, after_sales, contract, appointment) as a read-only agent.

## Implementation Plan

### 1. Create Profile Service (stores/retrieves user info)

**File: `app/services/profile.py`**
- `DbProfileService` class implementing `ProfileService` protocol
- Methods:
  - `get_profile(session_id: str) -> ProfileInfo | None` — returns name/phone from session → customer lookup
  - `get_conversation_summary(session_id: str, limit: int = 10) -> str` — last N messages formatted for LLM context

**File: `app/contracts/interfaces.py`** (update)
- Add `ProfileService` protocol
- Add `ProfileInfo` schema (name, phone, verified status)

**File: `app/services/__init__.py`** (update)
- Add `profile: DbProfileService` to `Services` dataclass

### 2. Create Conversation Agent

**File: `app/agent/nodes/conversation.py`**
- Handle GREETING, OTHER (out-of-scope), and CANCEL intents
- Access conversation history via ProfileService
- Use LLM to generate natural responses that:
  - Greet warmly on GREETING
  - Politely redirect on OTHER
  - Confirm cancellation on CANCEL
  - Answer memory questions ("tôi tên là gì?") by querying the profile service

System prompt:
```
You are a friendly automotive service assistant for Foton garage.
Current conversation context: {recent_messages}
Customer profile: {name}, {phone}

Handle:
- Greetings warmly
- Out-of-scope questions by redirecting to: pricing, appointments, technical questions
- Cancel requests by confirming cancellation
- Memory questions by looking up the customer profile
```

Returns an `answer()` dict just like other specialists.

### 3. Update Router to Dispatch Conversation Agent

**File: `app/agent/nodes/router.py`** (update)
- Remove hardcoded `GREETING_REPLY`, `OUT_OF_SCOPE_REPLY`, `CANCEL_REPLY` (lines 24-26)
- Add `"conversation"` to `AGENTS` dict for GREETING, OTHER intents
- Mark `conversation` as READ_ONLY
- When `_cancelled()` returns True:
  - Instead of returning `_default(state, update, CANCEL_REPLY)`
  - Create a task for the conversation agent with intent=CANCEL
  - Let conversation agent generate the cancellation message

**Changes:**
```python
AGENTS = {
    Intent.CONTRACT.value: "contract",
    Intent.APPOINTMENT.value: "appointment",
    Intent.AFTER_SALES.value: "after_sales",
    Intent.MAINTENANCE.value: "knowledge",
    Intent.SALES.value: "sales",
    Intent.KNOWLEDGE.value: "knowledge",
    Intent.GREETING.value: "conversation",
    Intent.OTHER.value: "conversation",
    # CANCEL handled specially in router logic
}
READ_ONLY = ("sales", "knowledge", "after_sales", "conversation")
```

### 4. Make Appointment Slot-Filling Flexible

**File: `app/agent/nodes/appointment.py`** (refactor)
- Remove hardcoded `ASK` dict (lines 7-11)
- Replace with LLM call that:
  - Reviews conversation history
  - Identifies missing slots
  - Generates natural questions considering what user already said
  - Handles corrections ("không phải thứ 3, thứ 4 cơ")

New flow:
```python
def appointment(state: AgentState) -> dict:
    slots = _normalize_slots(state.get("slots", {}))
    missing = _missing_slots(slots)
    
    if missing:
        # Use LLM to ask for missing slots naturally
        question = _generate_slot_question(state, slots, missing)
        return {"answers": [answer(state, "appointment", question)],
                "active_flow": "appointment", "slots": slots,
                "trace": step(state, "appointment")}
    
    # All slots present → create appointment
    result = get_services().booking.create_appointment(...)
    ...
```

Helper `_generate_slot_question()`:
- System prompt explains the slot-filling task
- Provides conversation history
- Lists which slots are filled and which are missing
- Asks LLM to generate a natural follow-up question

### 5. Update Graph

**File: `app/agent/graph.py`** (update)
- Import `conversation` from `app/agent/nodes/conversation`
- Add to `SPECIALISTS` dict: `"conversation": conversation`
- Add to graph nodes

### 6. Update Schemas

**File: `app/contracts/schemas.py`** (update if needed)
- Add `ProfileInfo` model:
  ```python
  class ProfileInfo(BaseModel):
      name: str | None
      phone: str | None
      verified: bool
  ```

### 7. Migration (if needed)

No DB schema changes required — Customer and ChatSession tables already exist with name/phone fields.

## Testing Strategy

### Unit tests
- `tests/test_conversation_agent.py`:
  - Greeting intent → warm greeting
  - OTHER intent → redirect to valid topics
  - CANCEL during active flow → confirmation + cleared slots
  - Memory question "tôi tên là gì?" → returns stored name

- `tests/test_appointment_flexible.py`:
  - Missing slot → natural question (not template)
  - User correction → LLM updates slot
  - Ambiguous time → LLM asks for clarification

### Integration test
- Multi-turn appointment booking with corrections
- Memory recall across turns

## Rollout Plan

1. **Phase 1: Conversation agent for greeting/other** (lower risk)
   - Add ProfileService
   - Add conversation node
   - Route GREETING and OTHER to conversation agent
   - Keep hardcoded CANCEL for now

2. **Phase 2: Flexible appointment** (higher risk)
   - Refactor appointment.py to use LLM
   - Test heavily before deploying

3. **Phase 3: Conversation agent handles CANCEL**
   - Replace hardcoded cancel logic with conversation agent

## Backward Compatibility

- Existing router logic preserved for all other intents
- Policy guard, compose, output guard unchanged
- Contract and other agents unaffected

## Risks & Mitigations

**Risk 1: LLM generates off-topic replies**
- Mitigation: Strict system prompts + output_guard still filters sensitive content

**Risk 2: Higher latency/cost**
- Mitigation: Conversation agent only fires for GREETING/OTHER/CANCEL (low traffic intents)

**Risk 3: Slot-filling loops indefinitely**
- Mitigation: Add max_turns check in appointment node, fallback to human handover after N attempts

## Files to Create
1. `app/services/profile.py`
2. `app/agent/nodes/conversation.py`
3. `tests/test_conversation_agent.py`
4. `tests/test_appointment_flexible.py`

## Files to Modify
1. `app/agent/nodes/router.py`
2. `app/agent/nodes/appointment.py`
3. `app/agent/graph.py`
4. `app/services/__init__.py`
5. `app/contracts/interfaces.py`
6. `app/contracts/schemas.py` (minor)

## Estimated Complexity
- Conversation agent: ~150 lines
- Flexible appointment: ~100 lines modified
- Profile service: ~80 lines
- Tests: ~200 lines
- Total: ~530 lines across 10 files

## Success Criteria
1. "Tôi tên là gì?" returns the stored name from earlier in the conversation
2. Greeting feels natural, not robotic
3. Appointment slot-filling handles corrections without breaking
4. No regression in existing sales/knowledge/contract agents
5. All tests pass
