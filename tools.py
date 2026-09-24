from langchain_core.tools import tool
from db_repositories import db

@tool
def search_cars_tool(keyword: str = "", max_budget: float = 0.0) -> str:
    """Tra cứu danh sách xe theo từ khóa (loại xe, tên xe) hoặc khoảng ngân sách tối đa."""
    budget = None if max_budget <= 0 else max_budget
    cars = db.cars.search(keyword=keyword if keyword else None, max_price=budget)
    if not cars:
        return "Không tìm thấy xe phù hợp trong kho."
    
    output = []
    for c in cars:
        output.append(f"- {c.name} ({c.car_type}): Giá niêm yết {c.price:,.0f} VNĐ. Video: {c.video_url or 'Chưa có'}")
    return "\n".join(output)

@tool
def get_car_price_tool(car_name: str) -> str:
    """Tra cứu GIÁ CHUẨN XÁC của xe từ Database. KHÔNG ĐƯỢC TỰ BỊA GIÁ."""
    car = db.cars.get_by_name(car_name)
    if not car:
        return f"Không tìm thấy dữ liệu xe '{car_name}' trong kho."
    return f"XE: {car.name} | ID: {car.id} | GIÁ_CHUẨN: {car.price:,.0f} VNĐ"

@tool
def request_discount_tool(car_name: str, percent: float, session_id: str) -> str:
    """Tạo yêu cầu giảm giá hoặc chiết khấu cho khách hàng."""
    car = db.cars.get_by_name(car_name)
    if not car:
        return f"Không thể tạo yêu cầu: Không tìm thấy xe '{car_name}'."
    
    # Policy: Giảm <= 5% được duyệt tự động. Giảm > 5% chuyển quản lý phê duyệt.
    req = db.discounts.create_request(session_id=session_id, car_id=car.id, percent=percent)
    
    if percent > 5.0:
        return (
            f"POLICY_DISCOUNT_TRIGGERED: Mã yêu cầu {req.id}. "
            f"Mức giảm {percent}% vượt thẩm quyền tự động (tối đa 5%). "
            f"Hồ sơ đã chuyển sang trạng thái CHỜ DUYỆT từ Quản lý đại lý."
        )
    else:
        req.status = "APPROVED"
        return f"Mã yêu cầu {req.id}: Giảm giá {percent}% đã được hệ thống tự động chấp thuận!"

@tool
def lookup_maintenance_tool(model: str, current_km: int) -> str:
    """Tra cứu các hạng mục bảo dưỡng định kỳ và chi phí dự kiến theo số km."""
    item = db.maintenance.get_milestone_tasks(model=model, km=current_km)
    if not item:
        return "Chưa có thông tin bảo dưỡng cho dòng xe này."
    tasks_str = ", ".join(item.tasks)
    return (
        f"Mốc bảo dưỡng khuyến nghị: {item.km_milestone:,} km.\n"
        f"Hạng mục: {tasks_str}.\n"
        f"Chi phí dự kiến: {item.estimated_cost:,.0f} VNĐ."
    )

ALL_TOOLS = [search_cars_tool, get_car_price_tool, request_discount_tool, lookup_maintenance_tool]