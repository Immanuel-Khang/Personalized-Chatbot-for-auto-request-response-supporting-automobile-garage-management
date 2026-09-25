import os
from langchain_core.messages import HumanMessage
from graph_builder import build_graph
from database import db_container

def chat(app, session_id: str, message: str):
    config = {"configurable": {"thread_id": session_id}}
    state_input = {
        "messages": [HumanMessage(content=message)],
        "session_id": session_id
    }
    print(f"\n[CUSTOMER]: {message}")
    result = app.invoke(state_input, config=config)
    print(f"[INTENT]: {result.get('intent')} | [STAGE]: {result.get('stage')}")
    print(f"[BOT]: {result['messages'][-1].content}")
    return result

def main():
    app = build_graph()
    session = "SESSION_TEST_101"

    # Turn 1: Maintenance Intent -> BAO_DUONG
    chat(app, session, "Xe Vios đi được 10,000 km thì cần bảo dưỡng những gì em?")

    # Turn 2: Sales Intent -> TU_VAN
    chat(app, session, "Cho anh hỏi giá xe Corolla Cross bản tiêu chuẩn hiện tại là bao nhiêu?")

    # Turn 3: Discount Intent (> 5%) -> Policy Guard triggers CHO_DUYET
    res3 = chat(app, session, "Bớt cho anh 8% được không? Anh lấy xe trong tuần.")
    pending_id = res3.get("pending_discount_id")
    print(f"--> Pending Ticket Generated: {pending_id}")

    # Turn 4: Follow up while still PENDING
    chat(app, session, "Có kết quả duyệt chưa em ơi?")

    # Scenario: Admin REJECTS the request
    print("\n--- [ADMIN PORTAL]: Manager REJECTS the request ---")
    db_container.discounts.update_status(pending_id, "REJECTED")

    # Turn 5: Customer chats -> Router detects REJECT -> switches back to TU_VAN
    chat(app, session, "Alo em?")

    # Scenario: Customer accepts standard price, asks for contract
    # Turn 6: Contract Intent -> HOP_DONG
    chat(app, session, "Thôi được rồi, anh đồng ý giá niêm yết. Làm thủ tục ký hợp đồng cho anh.")

if __name__ == "__main__":
    main()