from langchain_core.messages import HumanMessage
from graph_builder import build_agent_harness
from db_repositories import db
from dotenv import load_dotenv

def run_chat_turn(app, session_id: str, user_text: str):
    config = {"configurable": {"thread_id": session_id}}
    
    inputs = {
        "messages": [HumanMessage(content=user_text)],
        "session_id": session_id,
        "slots": {}
    }
    
    print(f"\n💬 KHÁCH HÀNG: {user_text}")
    result = app.invoke(inputs, config=config)
    
    agent_reply = result["messages"][-1].content
    print(f"🤖 BOT [{result.get('stage')}]: {agent_reply}")
    return result

def main():
    app = build_agent_harness()
    session_id = "session_khach_001"

    print("Welcome to our website!")
    print("Please enter your command: ")
    while True: # a loop to mimic real conversation
        request = input()
        
        if request == "quit": 
            break
        
        result = run_chat_turn(app, session_id, request)
        
if __name__ == "__main__":
    main()