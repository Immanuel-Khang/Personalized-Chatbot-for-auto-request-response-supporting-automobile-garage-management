import os
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from graph_builder import build_graph
from database import db_container
from schema import CustomerSlots

def chat(app, session_id: str, message: str):
    config = {"configurable": {"thread_id": session_id}}
    state_input = {
        "messages": [HumanMessage(content=message)],
        "session_id": session_id, 
    }
    print(f"\n[CUSTOMER]: {message}")
    result = app.invoke(state_input, config=config)
    
    print(f"[INTENT]: {result.get('intent')} | [STAGE]: {result.get('stage')}")
    
    # 🟢 KIỂM TRA SLOTS TẠI ĐÂY:
    slots = result.get("slots")
    if isinstance(slots, BaseModel):
        # Nếu slots là Pydantic model
        print(f"[SLOTS]: {slots.model_dump(exclude_none=True)}")
    else:
        # Nếu slots là dictionary
        print(f"[SLOTS]: {slots}")
    print(f"[BOT]: {result['messages'][-1].content}")
    return result


def main():
    app = build_graph()
    session = "SESSION_TEST_107"

    # Turn 2: Sales Intent -> TU_VAN
    print("Lets start:\n")
    while True: 
        message = input()
        
        chat(app, session, message)
        
        if input == "quit": 
            break
    

if __name__ == "__main__":
    main()