# === FILE: test_full_system.py ===
"""
KIỂM THỬ TOÀN DIỆN TOÀN BỘ HỆ THỐNG AGENT HARNESS (FOTON VIETNAM)
Bao gồm:
1. TRACK 1: FAQ & Knowledge Retrieval (RAG từ file Excel: Thông tin chung, Phụ tùng, Kỹ thuật cơ bản)
2. TRACK 2: Sales & Đàm phán Giá (Tra cứu DB, Policy Guard chiết khấu 10% vs 5%, Chốt hợp đồng & KYC)
3. TRACK 3: Đặt Lịch Hẹn Xưởng Dịch Vụ (Kiểm tra Capacity, Tự động xác nhận CONFIRMED vào Database)
"""
import os
import json
from langchain_core.messages import HumanMessage
from rag_pipeline import load_faq_from_excel
from graph_builder import build_graph
from database import db

# ─────────────────────────────────────────
# HÀM GỬI TIN NHẮN VÀ IN LOG TRỰC QUAN
# ─────────────────────────────────────────
def send_chat(app, session_id: str, message: str) -> dict:
    config = {"configurable": {"thread_id": session_id}}
    state_input = {
        "messages": [HumanMessage(content=message)],
        "session_id": session_id,
        "slots": {}
    }

    print(f"\n💬 [CUSTOMER]: {message}")
    result = app.invoke(state_input, config=config)

    intent = result.get("intent")
    stage = result.get("stage")
    slots = {k: v for k, v in (result.get("slots") or {}).items() if v is not None and v != ""}
    bot_reply = result["messages"][-1].content

    print(f"🎯 [INTENT]: {intent} | [STAGE]: {stage}")
    if slots:
        print(f"📋 [ACTIVE SLOTS]: {json.dumps(slots, ensure_ascii=False)}")
    print(f"🤖 [BOT]: {bot_reply}")
    return result


# ─────────────────────────────────────────
# TOÀN BỘ KỊCH BẢN KIỂM THỬ HỆ THỐNG
# ─────────────────────────────────────────
def main():
    print("=" * 75)
    print("🚀 BẮT ĐẦU KIỂM THỬ TOÀN HỆ THỐNG (FULL-SYSTEM INTEGRATION TEST)")
    print("=" * 75)

    # Khởi tạo Compiled LangGraph App
    app = build_graph()

    # ═══════════════════════════════════════════════════════════════
    # TRACK 1: KIỂM THỬ FAQ & TRI THỨC DỊCH VỤ (RAG)
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "═" * 75)
    print("🔷 PHẦN 1: KIỂM THỬ TRACK FAQ & TRI THỨC (RAG Q&A)")
    print("═" * 75)
    s_faq = "SESSION_FAQ_TEST_01"

    # # 1A. Câu hỏi thông tin xuất xứ (Sheet Thong_tin_chung)
    # res_1a = send_chat(app, s_faq, "Xe Foton là thương hiệu của nước nào sản xuất vậy em?")
    # assert res_1a.get("stage") == "TU_VAN", "Lỗi: Router không chuyển vào stage KNOWLEDGE!"

    # # 1B. Câu hỏi kỹ thuật / sự cố xe (Sheet Ho_tro_ky_thuat_co_ban)
    # res_1b = send_chat(app, s_faq, "Xe anh tự nhiên xả khói xanh và hao dầu thì phải xử lý thế nào?")
    # assert "buồng đốt" in res_1b["messages"][-1].content.lower() or "dầu" in res_1b["messages"][-1].content.lower(), "Lỗi: Không trả lời đúng câu hỏi khói xanh từ FAQ!"

    # # ═══════════════════════════════════════════════════════════════
    # # TRACK 2: KIỂM THỬ BÁN XE, POLICY GUARD & CHỐT HỢP ĐỒNG
    # # ═══════════════════════════════════════════════════════════════
    # print("\n" + "═" * 75)
    # print("🔷 PHẦN 2: KIỂM THỬ TRACK TƯ VẤN BÁN XE & CHÍNH SÁCH GIÁ (SALES TRACK)")
    # print("═" * 75)
    # s_sales = "SESSION_SALES_TEST_02"

    # # 2A. Khách hỏi giá xe chính thức từ Database
    # res_2a = send_chat(app, s_sales, "Cho anh hỏi giá niêm yết xe Toyota Vios G hiện tại bao nhiêu?")
    # assert "592" in res_2a["messages"][-1].content, "Lỗi: Không lấy đúng giá xe Vios G từ Database!"

    # # 2B. Khách đòi giảm giá 10% (> 5% -> Kích hoạt Policy Guard -> CHO_DUYET)
    # res_2b = send_chat(app, s_sales, "Anh ưng xe này rồi, bớt cho anh 10% được không em?")
    # assert res_2b.get("stage") == "CHO_DUYET", "Lỗi: Policy Guard không kích hoạt CHO_DUYET khi giảm 10%!"
    # print(f"   -> Vé chờ duyệt đã tạo: {res_2b.get('pending_discount_id')}")

    # # 2C. Khách hạ giá đàm phán xuống 5% (<= 5% -> Auto-Approved -> Quay về TU_VAN)
    # res_2c = send_chat(app, s_sales, "Thôi vậy bớt 5% được không em?")
    # assert res_2c.get("stage") == "TU_VAN", "Lỗi: Mức 5% không được tự động duyệt về TU_VAN!"
    # assert res_2c.get("pending_discount_id") is None, "Lỗi: Chưa xóa vé chờ duyệt cũ khi chấp thuận mức 5%!"

    # # 2D. Khách chốt mua và cung cấp thông tin KYC làm hợp đồng
    # res_2d = send_chat(app, s_sales, "Ok anh chốt giá này nhé. Giao xe về số 10 đường Trần Phú, Hà Đông giúp anh, anh tên Hùng, SĐT 0912345678")
    # assert res_2d.get("stage") == "HOP_DONG", "Lỗi: Không chuyển sang stage HOP_DONG khi chốt hợp đồng!"

    # # Kiểm tra Database khách hàng đã được lưu
    # customer_record = db.customers.get_by_session(s_sales)
    # assert customer_record is not None, "Lỗi: Chưa lưu hồ sơ khách hàng vào Database customers!"
    # print(f"   ✅ Đã lưu khách hàng vào Database: ID={customer_record.id}, Tên={customer_record.name}, SĐT={customer_record.phone}")

    # ═══════════════════════════════════════════════════════════════
    # TRACK 3: KIỂM THỬ ĐẶT LỊCH HẸN XƯỞNG DỊCH VỤ (BOOKING TRACK)
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "═" * 75)
    print("🔷 PHẦN 3: KIỂM THỬ TRACK ĐẶT LỊCH HẸN XƯỞNG (BOOKING TRACK)")
    print("═" * 75)
    s_booking = "SESSION_BOOKING_TEST_07"

    # Khách đặt lịch trong giờ làm việc (Thứ 6, 10:00 AM)
    res_3 = send_chat(app, s_booking, "Anh muốn đặt lịch bảo dưỡng xe Toyota Vios vào Thứ 6 lúc 10:00 sáng. Anh tên Hùng, SĐT 0912345678")
    assert res_3.get("stage") == "DAT_LICH", "Lỗi: Không chuyển sang stage DAT_LICH!"
    
    # Kiểm tra Database cuộc hẹn đã được tạo với trạng thái CONFIRMED
    appointments = db.appointments.get_by_session(s_booking)
    assert len(appointments) > 0, "Lỗi: Chưa tạo lịch hẹn trong Database appointments!"
    apt = appointments[0]
    print(f"   ✅ Lịch hẹn trong DB: ID={apt.id}, Thời gian={apt.appointment_time} {apt.appointment_date}, Trạng thái={apt.status}")
    assert apt.status == "CONFIRMED", "Lỗi: Lịch hẹn trong giờ hợp lệ phải có trạng thái CONFIRMED!"

if __name__ == "__main__":
    main()
