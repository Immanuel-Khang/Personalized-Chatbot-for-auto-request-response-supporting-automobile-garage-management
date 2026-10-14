"""[TRACK A] After-sales Service. P1: kết nối bảng service_history khi được triển khai."""
from app.contracts.schemas import ServiceRecord


class DbAfterSalesService:
    def get_history(self, customer_id: int, limit: int = 5) -> list[ServiceRecord]:
        # Chưa có nguồn dữ liệu service_history, không tạo lịch sử giả.
        return []
