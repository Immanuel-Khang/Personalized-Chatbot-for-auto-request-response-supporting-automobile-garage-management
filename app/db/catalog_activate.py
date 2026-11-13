"""Bật ACTIVE cho sản phẩm DRAFT đã sẵn sàng và (tùy chọn) RETIRED dữ liệu mẫu cũ.

    python -m app.db.catalog_activate [--ids 3,4,5] [--retire-samples] [--dry-run]

Sẵn sàng = DRAFT, có model, có giá MSRP hiện hành kèm ngày hiệu lực, giá không bị đánh dấu needs_review và
không còn data_issue chưa xử lý ở trường giá/định danh. Vấn đề ở trường khác (vd vòng tua nghi sai) không chặn
vì giá trị đó đã để NULL, không bị trả sai cho khách.
Dữ liệu mẫu = sản phẩm ACTIVE không có dòng product_sources (không đến từ file nguồn nào)."""
import argparse

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.db.models import CatalogProduct, DataIssue, ProductPrice, ProductSource
from app.db.session import SessionLocal

BLOCKING_FIELDS = ("msrp", "model")


def ready_drafts(db: Session) -> list[CatalogProduct]:
    blocked = db.query(DataIssue.product_id).filter(DataIssue.resolved_at.is_(None), DataIssue.field.in_(BLOCKING_FIELDS))
    return (db.query(CatalogProduct)
            .join(ProductPrice, (ProductPrice.product_id == CatalogProduct.id)
                  & (ProductPrice.price_type == "MSRP") & ProductPrice.is_current)
            .filter(CatalogProduct.status == "DRAFT", CatalogProduct.model_id.isnot(None),
                    ProductPrice.effective_date.isnot(None), ProductPrice.needs_review.is_(False),
                    CatalogProduct.id.notin_(blocked))
            .order_by(CatalogProduct.id).all())


def sample_products(db: Session) -> list[CatalogProduct]:
    return (db.query(CatalogProduct)
            .filter(CatalogProduct.status == "ACTIVE", ~exists().where(ProductSource.product_id == CatalogProduct.id))
            .order_by(CatalogProduct.id).all())


def activate(db: Session, ids: set[int] | None = None, retire_samples: bool = False) -> dict[str, list[str]]:
    """Đổi trạng thái trong session (caller commit). ids=None: mọi sản phẩm sẵn sàng."""
    ready = ready_drafts(db)
    chosen = [p for p in ready if ids is None or p.id in ids]
    skipped = [f"{i} (không sẵn sàng hoặc không phải DRAFT)" for i in sorted(ids or set()) if i not in {p.id for p in ready}]
    retired: list[CatalogProduct] = []
    if retire_samples:
        survivors = db.query(CatalogProduct).filter(CatalogProduct.status == "ACTIVE", ~CatalogProduct.id.in_(
            [p.id for p in sample_products(db)])).count()
        if not chosen and not survivors:
            raise SystemExit("Từ chối retire dữ liệu mẫu: sẽ không còn sản phẩm ACTIVE nào. Hãy nạp CSV trước.")
        retired = sample_products(db)
    for p in chosen:
        p.status = "ACTIVE"
    for p in retired:
        p.status = "RETIRED"
    return {"activated": [f"{p.id} {p.name}" for p in chosen], "retired": [f"{p.id} {p.name}" for p in retired],
            "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", type=lambda s: {int(x) for x in s.split(",") if x}, help="chỉ bật các id này (mặc định: mọi sản phẩm sẵn sàng)")
    ap.add_argument("--retire-samples", action="store_true", help="chuyển dữ liệu mẫu (không có nguồn) sang RETIRED")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with SessionLocal() as db:
        result = activate(db, args.ids, args.retire_samples)
        db.rollback() if args.dry_run else db.commit()
    for key, items in result.items():
        print(f"{key}: {len(items)}" + "".join(f"\n  - {i}" for i in items))
    if args.dry_run:
        print("(dry-run, chưa ghi gì)")


if __name__ == "__main__":
    main()
