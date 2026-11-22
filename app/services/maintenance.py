"""
[TRACK A] MaintenanceService: tra cứu lịch bảo dưỡng theo mốc km.
Port từ codebase hiện tại: tìm mốc km gần nhất, trả về danh sách công việc + chi phí ước tính.
"""
from typing import Optional

from app.contracts.schemas import MaintenanceResult

MILESTONES = [5000, 10000, 20000, 40000, 60000, 80000, 100000]

# Chi phí ước tính theo mốc km
COST_MAP = {
    5000: 1_500_000,
    10000: 1_500_000,
    20000: 3_800_000,
    40000: 5_500_000,
    60000: 3_800_000,
    80000: 5_500_000,
    100000: 8_000_000,
}

# Công việc theo mốc km
TASKS_MAP = {
    5000: ["Thay dầu động cơ & lọc dầu", "Kiểm tra lốp & áp suất", "Kiểm tra phanh"],
    10000: ["Thay dầu động cơ & lọc dầu", "Đảo lốp", "Kiểm tra hệ thống phanh", "Kiểm tra ắc quy"],
    20000: ["Thay dầu động cơ & lọc dầu", "Thay lọc gió động cơ", "Thay lọc gió điều hòa",
             "Đảo lốp", "Kiểm tra hệ thống treo", "Kiểm tra nước làm mát"],
    40000: ["Thay dầu động cơ & lọc dầu", "Thay lọc gió", "Thay bugi",
             "Thay dầu hộp số", "Kiểm tra hệ thống treo & lái", "Thay dầu phanh"],
    60000: ["Thay dầu động cơ & lọc dầu", "Đảo lốp", "Kiểm tra phanh",
             "Kiểm tra hệ thống treo", "Kiểm tra nước làm mát"],
    80000: ["Thay dầu động cơ & lọc dầu", "Thay lọc gió", "Thay bugi",
             "Thay dầu hộp số", "Thay dầu phanh", "Kiểm tra hệ thống treo & lái"],
    100000: ["Bảo dưỡng lớn toàn diện", "Thay dầu động cơ & lọc dầu", "Thay lọc gió",
              "Thay bugi", "Thay dầu hộp số", "Thay dầu phanh", "Thay nước làm mát",
              "Kiểm tra toàn bộ hệ thống"],
}


class DbMaintenanceService:
    def get_milestone_details(self, model: str, km: int) -> Optional[MaintenanceResult]:
        if km <= 0:
            return None
        milestone = min(MILESTONES, key=lambda x: abs(x - km))
        return MaintenanceResult(
            km_milestone=milestone,
            model_name=model,
            tasks=TASKS_MAP.get(milestone, ["Thay dầu động cơ & lọc dầu", "Kiểm tra lốp", "Kiểm tra phanh"]),
            estimated_cost=COST_MAP.get(milestone, 1_500_000),
        )
