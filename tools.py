import json
from langchain_core.tools import tool
from database import db_container

@tool
def search_cars(keyword: str = "", max_price: float = 0.0) -> str:
    """Search for vehicles matching keywords or maximum budget limit."""
    budget = None if max_price <= 0 else max_price
    cars = db_container.cars.search_cars(keyword=keyword, max_price=budget)
    if not cars:
        return "No vehicles matched the criteria."
    return json.dumps([c.model_dump() for c in cars], ensure_ascii=False)

@tool
def get_car_price(car_name: str) -> str:
    """Fetch verified official price directly from the database. Never hallucinate price."""
    car = db_container.cars.get_car_by_name(car_name)
    if not car:
        return f"Car '{car_name}' not found in official stock."
        
    return json.dumps({"car": car.name, "official_price_vnd": car.price}, ensure_ascii=False)

@tool
def request_discount(car_name: str, requested_percent: float) -> str:
    """Tạo hoặc cập nhật yêu cầu chiết khấu/giảm giá cho xe."""
    car = db_container.cars.get_car_by_name(car_name)
    car_price = car.price if car else 592_000_000
    
    # Chính sách: Giảm <= 5% thì chatbot tự chốt luôn
    if requested_percent <= 5.0:
        discounted_price = car_price * (1 - requested_percent / 100.0)
        return json.dumps({
            "status": "AUTO_APPROVED",
            "car": car_name,
            "discount_percent": requested_percent,
            "discounted_price": discounted_price,
            "requires_manager_approval": False,
            "message": f"Mức giảm {requested_percent}% đã được hệ thống phê duyệt tự động. Giá sau giảm là {discounted_price:,.0f} VNĐ."
        }, ensure_ascii=False)
    else:
        # Nếu > 5% thì mới cần quản lý
        req = db_container.discounts.create_request("current_session", car_name, requested_percent)
        return json.dumps({
            "status": "PENDING",
            "request_id": req.id,
            "car": car_name,
            "discount_percent": requested_percent,
            "requires_manager_approval": True,
            "message": f"Mức giảm {requested_percent}% vượt trần 5%, cần trình quản lý duyệt."
        }, ensure_ascii=False)

@tool
def lookup_maintenance_schedule(car_model: str, mileage_km: int) -> str:
    """Lookup recommended maintenance package and estimated cost based on mileage."""
    result = db_container.maintenance.get_milestone_details(car_model, mileage_km)
    if not result:
        return "No maintenance package found for this model."
    return json.dumps(result.model_dump(), ensure_ascii=False)

ALL_TOOLS = [search_cars, get_car_price, request_discount, lookup_maintenance_schedule]