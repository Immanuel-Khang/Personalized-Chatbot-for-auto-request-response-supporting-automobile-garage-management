"""
Agent Tool Definitions.
All tools are pure functions that interact only with the database layer.
session_id is NEVER a tool parameter (prevents LLM hallucination).
Nodes inject session_id via system prompt or via a closure wrapper.
"""
import json
from langchain_core.tools import tool
from database import db
from rag_pipeline import get_vector_store



# ═══════════════════════════════════════════════════════════════
# TRACK A: SALES TOOLS
# ═══════════════════════════════════════════════════════════════

@tool
def search_cars(keyword: str = "", max_price: float = 0.0) -> str:
    """Search for available vehicles by keyword (model name or category) and optional max budget."""
    budget = None if max_price <= 0 else max_price
    kw = keyword.strip() if keyword.strip() else None
    cars = db.cars.search(keyword=kw, max_price=budget)

    if not cars:
        return json.dumps({"status": "NO_RESULTS", "message": "Không tìm thấy xe phù hợp trong kho hàng."})

    results = [
        {"id": c.id, "name": c.name, "category": c.category,
         "price_vnd": c.price, "video_url": c.video_url}
        for c in cars
    ]
    return json.dumps({"status": "SUCCESS", "count": len(results), "cars": results}, ensure_ascii=False)


@tool
def get_car_price(car_name: str) -> str:
    """
    Fetch the OFFICIAL price of a car from the database.
    NEVER hallucinate a price — always call this tool.
    If not found, returns smart suggestions from available stock.
    """
    car = db.cars.get_by_name(car_name)

    if car:
        return json.dumps({
            "status": "SUCCESS",
            "car_id": car.id,
            "car_name": car.name,
            "official_price_vnd": car.price,
            "video_url": car.video_url,
        }, ensure_ascii=False)

    # NOT FOUND: generate suggestions using token search
    tokens = [t.lower() for t in car_name.split() if len(t) > 2]
    all_cars = db.cars.search()
    suggestions = [
        f"{c.name} — {c.price:,.0f} VNĐ"
        for c in all_cars
        if any(tok in c.name.lower() for tok in tokens)
    ]
    if not suggestions:
        suggestions = [f"{c.name} — {c.price:,.0f} VNĐ" for c in all_cars[:3]]

    return json.dumps({
        "status": "NOT_FOUND",
        "searched_query": car_name,
        "available_suggestions": suggestions,
        "instruction_for_bot": (
            "Không dùng từ kỹ thuật 'not found'. Thông báo lịch sự và gợi ý dòng xe tương tự từ 'available_suggestions'."
        )
    }, ensure_ascii=False)


@tool
def request_discount(car_name: str, requested_percent: float) -> str:
    """
    Submit a discount request for a specific car.
    Discounts <= 5%: auto-approved immediately.
    Discounts > 5%: requires manager approval (returns PENDING with ticket ID).
    """
    car = db.cars.get_by_name(car_name)
    base_price = car.price if car else 0.0
    resolved_name = car.name if car else car_name

    if requested_percent <= 5.0:
        discounted_price = base_price * (1 - requested_percent / 100.0)
        return json.dumps({
            "status": "AUTO_APPROVED",
            "car": resolved_name,
            "discount_percent": requested_percent,
            "original_price_vnd": base_price,
            "discounted_price_vnd": discounted_price,
            "requires_manager_approval": False,
            "message": (
                f"Mức giảm {requested_percent}% đã được hệ thống tự động duyệt. "
                f"Giá sau giảm: {discounted_price:,.0f} VNĐ."
            )
        }, ensure_ascii=False)
    else:
        req = db.discounts.create_request(
            session_id="__pending__", car_name=resolved_name, percent=requested_percent
        )
        return json.dumps({
            "status": "PENDING",
            "request_id": req.id,
            "car": resolved_name,
            "discount_percent": requested_percent,
            "requires_manager_approval": True,
            "message": (
                f"Mức giảm {requested_percent}% vượt trần thẩm quyền tự động (5%). "
                f"Đã tạo phiếu yêu cầu duyệt Mã: {req.id}. Quản lý sẽ xem xét sớm nhất."
            )
        }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# TRACK A: MAINTENANCE TOOLS
# ═══════════════════════════════════════════════════════════════

@tool
def lookup_maintenance_schedule(car_model: str, mileage_km: int) -> str:
    """
    Look up the recommended maintenance package and estimated cost for a car at a given mileage.
    """
    item = db.maintenance.get_milestone_details(model=car_model, km=mileage_km)
    if not item:
        return json.dumps({"status": "NOT_FOUND", "message": "Không tìm thấy thông tin bảo dưỡng cho dòng xe này."})

    return json.dumps({
        "status": "SUCCESS",
        "car_model": car_model,
        "km_milestone": item.km_milestone,
        "tasks": item.tasks,
        "estimated_cost_vnd": item.estimated_cost,
        "summary": (
            f"Tại mốc {item.km_milestone:,} km, xe cần thực hiện {len(item.tasks)} hạng mục. "
            f"Chi phí dự kiến: {item.estimated_cost:,.0f} VNĐ."
        )
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# TRACK B: UNIFIED APPOINTMENT TOOL
# ═══════════════════════════════════════════════════════════════

@tool
def schedule_appointment(
    customer_name: str,
    customer_phone: str,
    car_model: str,
    appointment_date: str,
    appointment_time: str,
    session_id: str,
    service_type: str = "MAINTENANCE"
) -> str:
    """
    Book a service appointment at the workshop.
    Automatically confirms if the time slot has capacity, otherwise sets PENDING_ADMIN for manual scheduling.
    service_type options: MAINTENANCE, REPAIR, TEST_DRIVE, WARRANTY_CHECK.
    """
    # 1. Resolve customer from existing records or create new
    # Note: session_id is handled by the node after calling this tool
    # We use phone as a secondary lookup key here
    customer = db.customers.get_by_phone(customer_phone)
    customer_id = customer.id if customer else None

    # 2. Check workshop capacity for the requested slot
    booked_count = db.appointments.count_by_slot(appointment_date, appointment_time)
    has_capacity = booked_count < db.workshop_max_capacity

    # 3. Basic business hours check (8:00 - 17:00)
    try:
        hour = int(appointment_time.split(":")[0])
        is_business_hours = 8 <= hour < 17
    except (ValueError, IndexError):
        is_business_hours = True   # If format is unclear, let admin decide

    # 4. Decide status
    if has_capacity and is_business_hours:
        status = "CONFIRMED"
    else:
        status = "PENDING_ADMIN"

    apt = db.appointments.create(
        customer_id=customer_id,
        session_id=session_id,  
        car_model=car_model,
        date=appointment_date,
        time=appointment_time,
        service_type=service_type,
        status=status
    )

    if status == "CONFIRMED":
        return json.dumps({
            "status": "CONFIRMED",
            "appointment_id": apt.id,
            "customer_name": customer_name,
            "car_model": car_model,
            "datetime": f"{appointment_time} ngày {appointment_date}",
            "service_type": service_type,
            "message": (
                f"Lịch hẹn đã được XÁC NHẬN THÀNH CÔNG! "
                f"Mã lịch: {apt.id}. "
                f"Thời gian: {appointment_time} ngày {appointment_date}. "
                f"Khách đến xưởng vui lòng báo mã này cho lễ tân."
            )
        }, ensure_ascii=False)
    else:
        reason = "Xưởng đang kín cầu nâng trong khung giờ này" if not has_capacity else "Ngoài giờ làm việc tiêu chuẩn"
        return json.dumps({
            "status": "PENDING_ADMIN",
            "appointment_id": apt.id,
            "customer_name": customer_name,
            "car_model": car_model,
            "datetime": f"{appointment_time} ngày {appointment_date}",
            "service_type": service_type,
            "reason": reason,
            "message": (
                f"Yêu cầu đặt lịch lúc {appointment_time} ngày {appointment_date} đã được ghi nhận (Mã: {apt.id}). "
                f"Lý do chờ duyệt: {reason}. "
                f"Cố vấn dịch vụ sẽ liên hệ số {customer_phone} để xác nhận lịch cho mình nhé ạ!"
            )
        }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# TRACK A: CONTRACT / KYC TOOLS
# ═══════════════════════════════════════════════════════════════

@tool
def save_customer_profile(
    session_id: str,
    name: str,
    phone: str,
    address: str = ""
) -> str:
    """
    Save or update a customer's KYC profile in the database.
    Returns the customer_id for session continuity linkage.
    Call this after collecting name, phone, and address in the contract stage.
    """
    existing = db.customers.get_by_session(session_id)

    if existing:
        updated = db.customers.update(
            existing.id,
            name=name or existing.name,
            phone=phone or existing.phone,
            address=address or existing.address
        )
        
        if updated is None:
            return json.dumps({
                "status": "ERROR",
                "message": f"Không tìm thấy khách hàng với ID: {existing.id}."
            }, ensure_ascii=False)

        return json.dumps({
            "status": "UPDATED",
            "customer_id": updated.id,
            "message": f"Hồ sơ khách hàng đã được cập nhật (ID: {updated.id})."
        }, ensure_ascii=False)
        
    else:
        created = db.customers.create(session_id=session_id, name=name, phone=phone, address=address)
        return json.dumps({
            "status": "CREATED",
            "customer_id": created.id,
            "message": f"Hồ sơ khách hàng mới đã được tạo (ID: {created.id})."
        }, ensure_ascii=False)


@tool
def create_lead(
    session_id: str,
    customer_name: str,
    phone: str,
    car_model: str,
    lead_type: str = "PRICE_QUOTE"
) -> str:
    """
    Record a sales lead from a customer interested in a test drive or price quote.
    lead_type: PRICE_QUOTE or TEST_DRIVE.
    """
    lead = db.leads.create(
        session_id=session_id,
        customer_name=customer_name,
        phone=phone,
        car_model=car_model,
        address=None,
        lead_type=lead_type
    )
    return json.dumps({
        "status": "SUCCESS",
        "lead_id": lead.id,
        "lead_type": lead_type,
        "message": (
            f"Đã ghi nhận thông tin khách hàng {customer_name} quan tâm đến {car_model}. "
            f"Đại lý ủy quyền sẽ liên hệ SĐT {phone} sớm nhất."
        )
    }, ensure_ascii=False)
    
# Similarity score threshold — tune this for your FAQ content
FAQ_SIMILARITY_THRESHOLD = 0.78

@tool
def query_foton_faq(question: str) -> str:
    """
    Tra cứu câu trả lời chuẩn xác trong ngân hàng FAQ Foton (bảo hành, phụ tùng, xuất xứ, kỹ thuật cơ bản).
    """
    try:
        store = get_vector_store()
        results = store.similarity_search_with_score(question, k=1)
    except Exception as e:
        return json.dumps({
            "status": "STORE_NOT_READY",
            "message": f"Chưa khởi tạo hoặc chưa nạp file Excel FAQ: {e}"
        }, ensure_ascii=False)
    if not results:
        db.unanswered_logs.append(question)
        return json.dumps({
            "status": "OUT_OF_FAQ",
            "instruction": "Không tìm thấy trong FAQ Foton. Hãy trả lời theo kiến thức xe phổ thông kèm khuyến cáo liên hệ đại lý."
        }, ensure_ascii=False)
    doc, score = results[0]
    # L2 distance -> similarity
    similarity = max(0.0, min(1.0, 1.0 - (score / 2.0)))
    FAQ_THRESHOLD = 0.65  # Ngưỡng tin cậy cho câu hỏi tương đồng ngữ nghĩa
    if similarity >= FAQ_THRESHOLD:
        return json.dumps({
            "status": "EXACT_MATCH",
            "matched_question": doc.metadata.get("question"),
            "answer": doc.metadata.get("answer"),
            "category": doc.metadata.get("category"),
            "action_type": doc.metadata.get("action_type", "NONE"),
            "required_slots": doc.metadata.get("required_slots", "").split(",") if doc.metadata.get("required_slots") else []
        }, ensure_ascii=False)
    else:
        db.unanswered_logs.append(question)
        return json.dumps({
            "status": "OUT_OF_FAQ",
            "closest_question": doc.metadata.get("question"),
            "instruction": "Độ tương đồng thấp. Trả lời thận trọng kèm disclaimer hoặc hướng dẫn gọi hotline."
        }, ensure_ascii=False)

# ═══════════════════════════════════════════════════════════════
# TOOL GROUPS (for selective binding per node)
# ═══════════════════════════════════════════════════════════════

ALL_SALES_TOOLS = [search_cars, get_car_price, request_discount]
ALL_MAINTENANCE_TOOLS = [lookup_maintenance_schedule]
ALL_APPOINTMENT_TOOLS = [schedule_appointment]
ALL_CONTRACT_TOOLS = [save_customer_profile]
ALL_LEAD_TOOLS = [create_lead]
ALL_FAQ_TOOLS = [query_foton_faq]
ALL_TOOLS = [
    search_cars, get_car_price, request_discount,
    lookup_maintenance_schedule, schedule_appointment,
    save_customer_profile, save_customer_profile, create_lead,
    save_customer_profile, create_lead, query_foton_faq,
]
