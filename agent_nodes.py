import os
import json

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from schema import HarnessState, IntentAndSlotExtraction, IntentType, CustomerSlots
from typing import cast 
from pydantic import SecretStr
from database import db_container
from tools import search_cars, get_car_price, request_discount, lookup_maintenance_schedule

load_dotenv()
# API key for security
api_key = os.getenv("OPEN_AI_KEY")

if api_key is None: 
    raise ValueError("API key not found !")

llm = ChatOpenAI(
    model="gpt-5.4-nano",
    temperature=0.2,
    api_key=SecretStr(api_key)
)

# 1. Intent Classifier and Slot Filling Node
def intent_classifier_node(state: HarnessState) -> dict:
    # LLM vừa phân loại Intent, vừa nhặt thông tin điền vào Slots
    extractor = llm.with_structured_output(IntentAndSlotExtraction)
    
    system_prompt = (
        "Bạn là bộ phận phân loại ý định khách hàng tại đại lý ô tô. "
        "Phân loại tin nhắn mới nhất của khách hàng vào ĐÚNG MỘT danh mục:\n"
        "- SALES: Hỏi về xe, tính năng, bảng giá, so sánh dòng xe.\n"
        "- MAINTENANCE: Hỏi về lịch bảo dưỡng, sửa chữa, chi phí bảo dưỡng, số km.\n"
        "- CONTRACT: Sẵn sàng mua, đặt cọc, ký hợp đồng, cung cấp thông tin cá nhân.\n"
        "- DISCOUNT: Xin giảm giá, thương lượng, khuyến mãi, ưu đãi.\n"
        "- GENERAL: Chào hỏi, cảm ơn, hỏi chung không liên quan trực tiếp đến mua/bán.\n\n"
        "Ví dụ:\n"
        "Khách: 'Cho mình xem giá xe Camry với' -> SALES\n"
        "Khách: 'Xe mình chạy được 20 ngàn km rồi, cần bảo dưỡng gì?' -> MAINTENANCE\n"
        "Khách: 'Ok mình muốn đặt cọc luôn, gửi hợp đồng đi' -> CONTRACT\n"
        "Khách: 'Giảm cho mình 10% được không?' -> DISCOUNT\n"
        "Khách: 'Xin chào, cửa hàng mở cửa mấy giờ?' -> GENERAL\n"
        "Khách: 'Cảm ơn bạn nhé' -> GENERAL"
    )
    
    latest_user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    last_msg = latest_user_messages[-1] if latest_user_messages else state["messages"][-1]
    
    result = cast(
        IntentAndSlotExtraction, 
        extractor.invoke([system_prompt] + [last_msg])
    )
    # 1. LẤY SLOTS CŨ TỪ STATE (KHÔNG ĐƯỢC XÓA)
    merged_slots = dict(state.get("slots") or {})

    # 2. CHỈ CẬP NHẬT TRƯỜNG NÀO CÓ DỮ LIỆU MỚI (Tránh ghi đè None lên giá trị cũ)
    if result.extracted_slots:
        new_data = result.extracted_slots.model_dump(exclude_unset=True)
        for key, value in new_data.items():
            if value is not None and value != "":
                merged_slots[key] = value  # Giữ lại car_model cũ, thêm mileage_km mới!
    return {
        "intent": result.intent.value,
        "slots": merged_slots
    }

# 2. Business Router Node
def business_router_node(state: HarnessState) -> dict:
    current_stage = state.get("stage") or "IDLE"
    intent = state.get("intent")
    pending_discount_id = state.get("pending_discount_id")

    if current_stage == "CHO_DUYET" and pending_discount_id:
        discount_req = db_container.discounts.get_by_id(pending_discount_id)
        if discount_req:
            if discount_req.status == "APPROVED":
                return {"stage": "HOP_DONG", "pending_discount_id": None}
            elif discount_req.status == "REJECTED":
                return {"stage": "TU_VAN", "pending_discount_id": None}
            else:
                # Nếu khách muốn đàm phán con số khác -> Cho qua TU_VAN để chạy lại tool!
                if intent == "DISCOUNT":
                    return {"stage": "TU_VAN"}
                if intent == "MAINTENANCE":
                    return {"stage": "BAO_DUONG"}
                # Chỉ khi nào khách hỏi vu vơ/chờ đợi mới giữ ở CHO_DUYET
                return {"stage": "CHO_DUYET"}

    # Các trường hợp thông thường
    if intent == "MAINTENANCE":
        return {"stage": "BAO_DUONG"}
    elif intent == "CONTRACT":
        return {"stage": "HOP_DONG"}
    else:
        return {"stage": "TU_VAN"}

# 3. Consultation Node (TU_VAN)
def consultation_node(state: HarnessState) -> dict:
    consultation_tools = [search_cars, get_car_price, request_discount]
    bound_llm = llm.bind_tools(consultation_tools)
    
    system_prompt = SystemMessage(
        content=(
            "Bạn là trợ lý tư vấn bán xe tại đại lý Toyota. "
            "Phong cách: thân thiện, nhiệt tình, chuyên nghiệp. Luôn trả lời bằng tiếng Việt tự nhiên.\n\n"
            "CÁCH XƯNG HÔ:\n"
            "- Tự xưng: 'shop' hoặc 'mình'\n"
            "- Gọi khách hàng: 'bạn'\n"
            "- KHÔNG dùng emoji, icon, in đậm, in nghiêng.\n\n"
            "QUY TẮC BẮT BUOC:\n"
            "- Luôn dùng tool 'get_car_price' để lấy giá chính thức. KHÔNG BAO GIỜ tự đoán giá.\n"
            "- Khi khách xin giảm giá, dùng tool 'request_discount' với session_id bên dưới.\n"
            "- Nếu discount được AUTO_APPROVED, xác nhận giảm giá đã áp dụng và tính lại giá mới cho khách.\n"
            "- Trả lời ngắn gọn, dễ hiểu, tránh liệt kê dài dòng.\n\n"
            f"Session ID hiện tại: '{state['session_id']}'\n\n"
            "VÍ DỤ HỘI THOẠI:\n\n"
            "Khách: Cho mình hỏi giá xe Vios với\n"
            "Shop: Dạ vâng, bạn đợi mình tra giá chính thức của xe nhé ạ!\n"
            "[Gọi tool get_car_price('Toyota Vios G')]\n"
            "Shop: Hiện tại Toyota Vios G có giá niêm yết là 592 triệu nhé bạn. "
            "Dòng này rất phù hợp cho gia đình, tiết kiệm xăng mà cabin rộng rãi lắm. "
            "Bạn muốn tìm hiểu thêm về tính năng hay so sánh với dòng khác không?\n\n"
            "Khách: Giảm giá cho mình 3% đi\n"
            "Shop: Mình ghi nhận yêu cầu giảm 3% cho bạn nhé!\n"
            "[Gọi tool request_discount]\n"
            "Shop: Dạ thưa bạn! Yêu cầu giảm giá 3% đã được duyệt tự động. "
            "Giá sau giảm của Toyota Vios G là 574,24 triệu. Bạn muốn tiến hành đặt cọc luôn không?\n\n"
            "Khách: Có xe SUV nào tầm 800 triệu không?\n"
            "Shop: Để mình tìm cho bạn nhé!\n"
            "[Gọi tool search_cars]\n"
            "Shop: Dạ vâng, trong tầm giá 800 triệu, bên mình có Toyota Corolla Cross giá 760 triệu ạ. "
            "Đây là dòng SUV đô thị rất hot, thiết kế thể thao mà tiết kiệm nhiên liệu. "
            "Bạn muốn mình gửi thêm thông tin chi tiết không?"
        )
    )
    
    response = bound_llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 4. Maintenance Node (BAO_DUONG)
def maintenance_node(state: HarnessState) -> dict:
    bound_llm = llm.bind_tools([lookup_maintenance_schedule])
    system_prompt = SystemMessage(
        content=(
            "Bạn là cố vấn dịch vụ bảo dưỡng xe tại đại lý Toyota. "
            "Phong cách: tận tâm, dễ hiểu, luôn giải thích rõ ràng cho khách không rành kỹ thuật. "
            "Luôn trả lời bằng tiếng Việt tự nhiên.\n\n"
            "CÁCH XƯNG HÔ:\n"
            "- Tự xưng: 'shop' hoặc 'mình'\n"
            "- Gọi khách hàng: 'bạn'\n"
            "- KHÔNG dùng emoji, icon, in đậm, in nghiêng.\n\n"
            "QUY TẮC:\n"
            "- Luôn dùng tool 'lookup_maintenance_schedule' để tra cứu lịch bảo dưỡng chính xác.\n"
            "- Giải thích các hạng mục bảo dưỡng bằng ngôn ngữ đời thường, tránh thuật ngữ kỹ thuật khó hiểu.\n"
            "- Nhắc khách về tầm quan trọng của bảo dưỡng đúng hạn.\n\n"
            "VÍ DỤ HỘI THOẠI:\n\n"
            "Khách: Xe mình chạy được 10,000km rồi, cần bảo dưỡng gì?\n"
            "Shop: Xe bạn đã đến mốc 10,000km rồi, để mình kiểm tra lịch bảo dưỡng nhé!\n"
            "[Gọi tool lookup_maintenance_schedule]\n"
            "Shop: Ở mốc 10,000km, xe cần thực hiện những việc sau ạ:\n"
            "- Thay dầu máy và lọc dầu: giúp động cơ chạy mượt hơn\n"
            "- Đảo lốp: để lốp mòn đều, đi êm hơn\n"
            "- Kiểm tra hệ thống phanh: đảm bảo an toàn cho gia đình\n"
            "Chi phí ước tính khoảng 1,5 triệu. Bạn muốn đặt lịch bảo dưỡng luôn không ạ?\n\n"
            "Khách: Chi phí bảo dưỡng mốc 40,000km là bao nhiêu?\n"
            "Shop: Mốc 40,000km là đợt bảo dưỡng lớn rồi ạ! Để mình tra cho bạn nhé.\n"
            "[Gọi tool lookup_maintenance_schedule]\n"
            "Shop: Ở mốc 40,000km chi phí khoảng 3,8 triệu. Đợt này ngoài thay dầu thì còn "
            "kiểm tra tổng thể nhiều hạng mục hơn để xe luôn trong tình trạng tốt nhất. "
            "Bảo dưỡng đúng hạn sẽ giúp xe bền hơn và giữ giá trị khi bán lại nữa ạ!"
        )
    )
    response = bound_llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 5 Contract Node (HOP_DONG)
def contract_node(state: HarnessState) -> dict:
    slots = state.get("slots") or {}
    
    # Liệt kê các thông tin cần thiết làm hợp đồng
    required_fields = {
        "customer_name": "Họ và tên",
        "customer_phone": "Số điện thoại",
        "customer_address": "Địa chỉ nhận xe / hộ khẩu",
        "car_model": "Dòng xe chọn mua",
        "decided_price": "Mức giá chốt hợp đồng"
    }
    
    # Tìm xem còn thiếu trường nào
    missing_fields = [label for key, label in required_fields.items() if not slots.get(key)]
    
    system_prompt = SystemMessage(
        content=(
            "Bạn là nhân viên hợp đồng tại đại lý Toyota. "
            "Khách hàng đã quyết định mua xe! Phong cách: vui vẻ, chu đáo, hướng dẫn rõ ràng từng bước. "
            "Luôn trả lời bằng tiếng Việt tự nhiên.\n\n"
            "CÁCH XƯNG HÔ:\n"
            "- Tự xưng: 'shop' hoặc 'mình'\n"
            "- Gọi khách hàng: 'bạn'\n"
            "- KHÔNG dùng emoji, icon, in đậm, in nghiêng.\n\n"
            "NHIỆM VỤ: Thu thập thông tin cá nhân để làm hợp đồng:\n"
            "1. Họ và tên đầy đủ\n"
            "2. Số điện thoại\n"
            "3. Số CCCD/CMND\n\n"
            "QUY TẮC:\n"
            "- Chúc mừng khách hàng khi bắt đầu quy trình.\n"
            "- Hỏi từng thông tin một, không hỏi dồn dập.\n"
            "- Xác nhận lại thông tin trước khi hoàn tất.\n\n"
            "VÍ DỤ HỘI THOẠI:\n\n"
            "Khách: Mình muốn đặt cọc mua xe Corolla Cross\n"
            "Shop: Dạ vâng, để mình chuẩn bị hợp đồng, bạn cho mình xin họ tên đầy đủ trước ạ?\n\n"
            "Khách: Nguyễn Văn An\n"
            "Shop: Dạ vâng, cảm ơn bạn An! Bạn cho mình xin thêm số điện thoại liên hệ nhé?\n\n"
            "Khách: 0901234567\n"
            "Shop: Vâng, cuối cùng bạn cho mình xin số CCCD để hoàn tất hợp đồng nhé?\n\n"
            "Khách: 012345678901\n"
            "Shop: Dạ vâng, cảm ơn bạn An! Mình xác nhận lại thông tin:\n"
            "- Họ tên: Nguyễn Văn An\n"
            "- SĐT: 0901234567\n"
            "- CCCD: 012345678901\n"
            "Thông tin đã chính xác chưa? Nếu đúng rồi thì mình sẽ chuyển sang bộ phận xử lý hợp đồng ngay nhé!"
        )
    )
    
    response = llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 6. Pending Approval Node (CHO_DUYET)
def pending_approval_node(state: HarnessState) -> dict:
    req_id = state.get("pending_discount_id", "CURRENT_REQUEST")
    system_prompt = SystemMessage(
        content=(
            f"Bạn là trợ lý tư vấn tại đại lý Toyota. "
            f"Yêu cầu giảm giá của khách hàng (Mã phiếu: {req_id}) đang chờ quản lý duyệt. "
            "Phong cách: nhẹ nhàng, thấu hiểu, giữ khách không rời đi. "
            "Luôn trả lời bằng tiếng Việt tự nhiên.\n\n"
            "CÁCH XƯNG HÔ:\n"
            "- Tự xưng: 'shop' hoặc 'mình'\n"
            "- Gọi khách hàng: 'bạn'\n"
            "- KHÔNG dùng emoji, icon, in đậm, in nghiêng.\n\n"
            "QUY TẮC:\n"
            "- Thông báo trạng thái chờ duyệt một cách tích cực.\n"
            "- Trấn an khách rằng yêu cầu đang được xem xét nghiêm túc.\n"
            "- Hỏi khách có câu hỏi khác không để duy trì cuộc trò chuyện.\n\n"
            "VÍ DỤ HỘI THOẠI:\n\n"
            "Khách: Giảm giá của mình duyệt chưa?\n"
            f"Shop: Bạn ơi, yêu cầu giảm giá (mã {req_id}) của bạn đang được quản lý xem xét ạ. "
            "Thường thường sẽ có kết quả trong thời gian sớm nhất ạ. "
            "Bạn yên tâm, bên mình luôn cố gắng mang lại giá tốt nhất cho khách hàng! "
            "Trong lúc chờ, bạn có muốn tìm hiểu thêm về phụ kiện hay gói bảo hiểm cho xe không?\n\n"
            "Khách: Lâu quá vậy?\n"
            "Shop: Dạ xin lỗi bạn ạ! Do mức giảm giá này cần quản lý cấp cao duyệt "
            "nên cần thêm chút thời gian. Mình sẽ thông báo cho bạn ngay khi có kết quả ạ! "
            "Bạn có thắc mắc gì khác về xe mình hỗ trợ được không ạ?"
        )
    )
    response = llm.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}

# 7. Policy Guard Node
def policy_guard_node(state: HarnessState) -> dict:
    """
    Chỉ kiểm tra Tool vừa thực thi ở lượt chat hiện tại (tin nhắn cuối cùng trong messages).
    """
    latest_msg = state["messages"][-1]
    
    # 1. Nếu tin nhắn vừa rồi là ToolMessage
    if latest_msg.type == "tool":
        try:
            payload = json.loads(str(latest_msg.content))
            
            # Nếu lượt này khách xin mức mới > 5% -> Sang CHO_DUYET
            if isinstance(payload, dict) and payload.get("requires_manager_approval") is True:
                print(f"🚨 [POLICY GUARD]: Kích hoạt duyệt cho Ticket {payload.get('request_id')}")
                return {
                    "stage": "CHO_DUYET",
                    "pending_discount_id": payload.get("request_id")
                }
            
            # Nếu lượt này khách xin mức <= 5% (Đã được duyệt tự động) -> XÓA pending_discount_id cũ!
            elif isinstance(payload, dict) and payload.get("status") == "AUTO_APPROVED":
                print(f"✅ [POLICY GUARD]: Mức giảm {payload.get('discount_percent')}% được tự động duyệt.")
                return {
                    "stage": "TU_VAN",
                    "pending_discount_id": None  # 🟢 HỦY BỎ TICKET CHỜ DUYỆT CŨ
                }
        except (json.JSONDecodeError, TypeError):
            pass

    return {"stage": "TU_VAN"}