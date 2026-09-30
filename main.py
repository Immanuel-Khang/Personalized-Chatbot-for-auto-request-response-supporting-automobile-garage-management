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
    session = "SESSION_TEST_105"

    # Turn 2: Sales Intent -> TU_VAN
    chat(app, session, "Cho anh hỏi giá xe Toyota Corolla Cross hiện tại là bao nhiêu?")

    # Turn 3: Discount Intent (> 5%) -> Policy Guard triggers CHO_DUYET
    res1 = chat(app, session, "Bớt cho anh 8% được không? Anh lấy xe trong tuần.")
    pending_id = res1.get("pending_discount_id")
    
    res2 = chat(app, session, "Vậy còn giảm 4% thì sao? Anh lấy xe trong tuần.")
    pending_id = res2.get("pending_discount_id")
    

if __name__ == "__main__":
    main()