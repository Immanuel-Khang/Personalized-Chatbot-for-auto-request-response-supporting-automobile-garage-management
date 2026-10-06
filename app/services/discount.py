"""
[TRACK C] DiscountService: xử lý yêu cầu giảm giá.
Port từ codebase hiện tại: ≤ max_auto_approve% → AUTO_APPROVED, > → PENDING (chờ quản lý).
"""
from typing import Optional

from app.contracts.schemas import DiscountResult
from app.db.models import CatalogItem, DiscountRequest
from app.db.session import SessionLocal


class DbDiscountService:
    _seq = 0

    def request_discount(
        self, session_id: str, car_name: str, percent: float, max_auto_approve: float = 5.0
    ) -> DiscountResult:
        with SessionLocal() as db:
            # Tìm giá xe
            car = db.query(CatalogItem).filter(
                CatalogItem.name.ilike(f"%{car_name}%"), CatalogItem.kind == "CAR"
            ).first()
            original_price = car.list_price_vnd if car else 592_000_000
            discounted_price = original_price * (1 - percent / 100.0)

            DbDiscountService._seq += 1
            code = f"REQ-DISC-{DbDiscountService._seq:04d}"

            if percent <= max_auto_approve:
                status = "AUTO_APPROVED"
                message = f"Mức giảm {percent}% đã được hệ thống phê duyệt tự động. Giá sau giảm: {discounted_price:,.0f} VNĐ."
            else:
                status = "PENDING"
                message = f"Mức giảm {percent}% vượt trần {max_auto_approve}%, cần trình quản lý duyệt."

            row = DiscountRequest(
                request_code=code, session_id=session_id, car_name=car_name,
                discount_percent=percent, original_price=original_price,
                discounted_price=discounted_price, status=status,
            )
            db.add(row)
            db.commit()

            return DiscountResult(
                ok=True, status=status, car_name=car_name,
                original_price=original_price, discount_percent=percent,
                discounted_price=discounted_price, request_id=code, message=message,
            )

    def get_request(self, request_id: str) -> Optional[DiscountResult]:
        with SessionLocal() as db:
            row = db.query(DiscountRequest).filter_by(request_code=request_id).first()
            if not row:
                return None
            return DiscountResult(
                ok=True, status=row.status, car_name=row.car_name,
                original_price=row.original_price, discount_percent=row.discount_percent,
                discounted_price=row.discounted_price, request_id=row.request_code,
                message="",
            )

    def update_status(self, request_id: str, status: str) -> bool:
        with SessionLocal() as db:
            row = db.query(DiscountRequest).filter_by(request_code=request_id).first()
            if not row:
                return False
            row.status = status
            db.commit()
            return True
