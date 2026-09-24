from langchain_core.messages import HumanMessage
from graph_builder import build_agent_harness
from db_repositories import db


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

    # --- TURN 1: Hỏi giá xe ---
    run_chat_turn(app, session_id, "Cho anh hỏi giá xe Toyota Vios G hiện tại bao nhiêu?")

    # --- TURN 2: Yêu cầu giảm giá 10% (Kích hoạt Policy Guard -> CHO_DUYET) ---
    res2 = run_chat_turn(app, session_id, "Anh ưng xe này rồi, bớt cho anh 10% được không em?")
    print(f">> Trạng thái hiện tại: {res2.get('stage')}")
    print(f">> Mã giảm giá đang chờ duyệt: {res2.get('pending_discount_id')}")

    # --- TURN 3: Khách tiếp tục giục trong khi chưa duyệt ---
    run_chat_turn(app, session_id, "Duyệt nhanh giúp anh để anh còn chuẩn bị tiền.")

    # --- SIMULATE ADMIN ACTION TRÊN TRANG QUẢN TRỊ ---
    print("\n-----------------------------------------------------------")
    print("🔔 [ADMIN ACTION]: Quản lý bấm nút 'DUYỆT CHIẾT KHẤU' trên Web Admin")
    discount_id = res2.get("pending_discount_id")
    if discount_id:
        db.discounts.update_status(discount_id, "APPROVED")
    print("-----------------------------------------------------------")

    # --- TURN 4: Khách nhắn tiếp sau khi Admin đã duyệt -> Tự động chuyển HOP_DONG ---
    run_chat_turn(app, session_id, "Có kết quả duyệt chưa em ơi?")

if __name__ == "__main__":
    main()