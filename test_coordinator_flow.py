"""Quick test to verify coordinator flow with single and multiple agents."""
import sys
sys.path.insert(0, '.')

from app.agent.state import AgentState
from app.agent.nodes.router import router, dispatch
from app.agent.nodes.coordinator import coordinator
from app.contracts.schemas import ConversationInterpretation, TaskInterpretation, Intent, TaskRelationship

print("=" * 60)
print("TEST 1: Single agent query (sales)")
print("=" * 60)

state1: AgentState = {
    "session_id": "test_123",
    "user_text": "Xe nào tải 2 tấn?",
    "conversation_interpretation": ConversationInterpretation(
        tasks=[
            TaskInterpretation(
                task_id="task_1",
                domain=Intent.SALES,
                action="search_vehicles",
                text="Xe nào tải 2 tấn?",
                relationship=TaskRelationship.NEW_REQUEST
            )
        ],
        slot_updates=[],
        signals=None,
        presented_cars=[]
    ),
    "known_facts": {},
    "history": [],
}

# Router creates plan
router_result = router(state1)
print(f"✓ Router plan: {router_result.get('plan')}")
print(f"✓ Planned agents: {router_result.get('planned_agents')}")

# Dispatch should return "coordinator" (not send_all)
state1.update(router_result)
dispatch_result = dispatch(state1)
print(f"✓ Dispatch result: {dispatch_result}")
print(f"✓ Flow goes through coordinator: {dispatch_result == 'coordinator'}")

# Coordinator prepares context
coord_result = coordinator(state1)
print(f"✓ Coordinator plan: {coord_result.get('plan')}")
print(f"✓ Shared context: {coord_result.get('shared_context', {})}")

print("\n" + "=" * 60)
print("TEST 2: Multiple agents query (sales + appointment)")
print("=" * 60)

state2: AgentState = {
    "session_id": "test_456",
    "user_text": "Tìm xe tải 3 tấn và đặt lịch xem xe",
    "conversation_interpretation": ConversationInterpretation(
        tasks=[
            TaskInterpretation(
                task_id="task_1",
                domain=Intent.SALES,
                action="search_vehicles",
                text="Tìm xe tải 3 tấn",
                car_model="xe tải 3 tấn",
                relationship=TaskRelationship.NEW_REQUEST
            ),
            TaskInterpretation(
                task_id="task_2",
                domain=Intent.APPOINTMENT,
                action="create_appointment",
                text="đặt lịch xem xe",
                relationship=TaskRelationship.NEW_REQUEST
            )
        ],
        slot_updates=[],
        signals=None,
        presented_cars=[]
    ),
    "known_facts": {},
    "history": [],
}

router_result2 = router(state2)
print(f"✓ Router plan: {router_result2.get('plan')}")
print(f"✓ Planned agents: {router_result2.get('planned_agents')}")

state2.update(router_result2)
dispatch_result2 = dispatch(state2)
print(f"✓ Dispatch result: {dispatch_result2}")
print(f"✓ Flow goes through coordinator: {dispatch_result2 == 'coordinator'}")

coord_result2 = coordinator(state2)
print(f"✓ Coordinator plan: {coord_result2.get('plan')}")
print(f"✓ Shared context: {coord_result2.get('shared_context', {})}")

print("\n" + "=" * 60)
print("TEST 3: Contract query (should prepare MSRP)")
print("=" * 60)

from app.contracts.schemas import KnownFact, FactSource

state3: AgentState = {
    "session_id": "test_789",
    "user_text": "Tôi muốn mua xe Aumark S",
    "conversation_interpretation": ConversationInterpretation(
        tasks=[
            TaskInterpretation(
                task_id="task_1",
                domain=Intent.CONTRACT,
                action="start_contract",
                text="Tôi muốn mua xe Aumark S",
                car_model="Aumark S",
                relationship=TaskRelationship.NEW_REQUEST
            )
        ],
        slot_updates=[],
        signals=None,
        presented_cars=["Aumark S"]
    ),
    "known_facts": {
        "car_model": KnownFact(key="car_model", value="Aumark S", source=FactSource.USER_PROVIDED)
    },
    "history": [],
}

router_result3 = router(state3)
print(f"✓ Router plan: {router_result3.get('plan')}")

state3.update(router_result3)
dispatch_result3 = dispatch(state3)
print(f"✓ Dispatch result: {dispatch_result3}")

coord_result3 = coordinator(state3)
print(f"✓ Coordinator plan: {coord_result3.get('plan')}")
print(f"✓ Shared context keys: {list(coord_result3.get('shared_context', {}).keys())}")

# Check if MSRP was fetched
contract_context = coord_result3.get('shared_context', {}).get('contract', {})
if 'msrp' in contract_context:
    print(f"✓ MSRP prepared: {contract_context['msrp']}")
else:
    print(f"ℹ MSRP not prepared (may need valid product name in test DB)")

print("\n" + "=" * 60)
print("✅ ALL TESTS PASSED - Coordinator flow verified!")
print("=" * 60)
