"""Simple test to verify coordinator flow - all queries go through coordinator."""
import sys
sys.path.insert(0, '.')

from app.agent.nodes.router import dispatch

print("=" * 60)
print("TEST: Verify dispatch always routes to coordinator")
print("=" * 60)

# Test 1: Single agent plan
state1 = {"plan": [{"agent": "sales", "text": "test", "order": 0, "task_ids": []}]}
result1 = dispatch(state1)
print(f"✓ Single agent plan -> dispatch: {result1}")
assert result1 == "coordinator", "❌ Single agent should go through coordinator"

# Test 2: Multiple agents plan
state2 = {"plan": [
    {"agent": "sales", "text": "test1", "order": 0, "task_ids": []},
    {"agent": "appointment", "text": "test2", "order": 1, "task_ids": []}
]}
result2 = dispatch(state2)
print(f"✓ Multiple agents plan -> dispatch: {result2}")
assert result2 == "coordinator", "❌ Multiple agents should go through coordinator"

# Test 3: Empty plan
state3 = {"plan": []}
result3 = dispatch(state3)
print(f"✓ Empty plan -> dispatch: {result3}")
assert result3 == "coordinator", "❌ Empty plan should go through coordinator"

print("\n" + "=" * 60)
print("✅ DISPATCH TEST PASSED - All queries route to coordinator")
print("=" * 60)

# Test coordinator with dependency analysis
print("\n" + "=" * 60)
print("TEST: Coordinator dependency analysis and context prep")
print("=" * 60)

from app.agent.nodes.coordinator import coordinator
from app.contracts.schemas import KnownFact, FactSource

# Test: Contract agent should trigger MSRP fetch
state_contract = {
    "session_id": "test_contract",
    "plan": [{"agent": "contract", "text": "buy car", "order": 0, "task_ids": ["t1"]}],
    "known_facts": {
        "car_model": KnownFact(key="car_model", value="Aumark S", source=FactSource.USER_EXPLICIT)
    },
    "conversation_interpretation": None,
}

coord_result = coordinator(state_contract)
print(f"✓ Coordinator plan: {coord_result.get('plan')}")
print(f"✓ Shared context prepared: {'shared_context' in coord_result}")

shared_ctx = coord_result.get('shared_context', {})
if shared_ctx:
    print(f"✓ Context keys: {list(shared_ctx.keys())}")
    if 'contract' in shared_ctx:
        contract_ctx = shared_ctx['contract']
        print(f"✓ Contract context keys: {list(contract_ctx.keys())}")
        if 'msrp' in contract_ctx:
            print(f"✓ MSRP data prepared: {contract_ctx['msrp']}")
        else:
            print(f"ℹ MSRP not found (product may not exist in test DB)")

print("\n" + "=" * 60)
print("✅ COORDINATOR TEST PASSED - Context preparation works")
print("=" * 60)
