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
def request_discount(car_name: str, requested_percent: float, session_id: str) -> str:
    """Submit a discount request. Discounts over 5% require management approval."""
    car = db_container.cars.get_car_by_name(car_name)
    if not car:
        return f"Invalid vehicle name '{car_name}'."
    
    req = db_container.discounts.create_request(session_id, car.name, requested_percent)
    
    # Policy threshold: > 5% must go through approval workflow
    requires_approval = requested_percent > 5.0
    
    return json.dumps({
        "request_id": req.id,
        "car": car.name,
        "discount_percent": requested_percent,
        "requires_manager_approval": requires_approval,
        "status": "PENDING" if requires_approval else "AUTO_APPROVED"
    })

@tool
def lookup_maintenance_schedule(car_model: str, mileage_km: int) -> str:
    """Lookup recommended maintenance package and estimated cost based on mileage."""
    result = db_container.maintenance.get_milestone_details(car_model, mileage_km)
    if not result:
        return "No maintenance package found for this model."
    return json.dumps(result.model_dump(), ensure_ascii=False)

ALL_TOOLS = [search_cars, get_car_price, request_discount, lookup_maintenance_schedule]