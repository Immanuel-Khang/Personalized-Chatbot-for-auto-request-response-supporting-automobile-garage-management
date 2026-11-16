"""products.sku: khóa ổn định thay cho id tự tăng khi nối products <-> rag_chunks

Revision ID: a8b0c2d4e6f1
Revises: b4d0a3c4bc80
Create Date: 2026-11-16 10:00:00
"""
import re
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b0c2d4e6f1"
down_revision: Union[str, Sequence[str], None] = "b4d0a3c4bc80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slug(value: str) -> str:
    s = (value or "").replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")[:100]


def upgrade() -> None:
    op.add_column("products", sa.Column("sku", sa.String(120), nullable=True))
    conn = op.get_bind()
    used: set[str] = set()
    for pid, name in conn.execute(sa.text("SELECT id, name FROM products ORDER BY id")).all():
        sku = _slug(name) or f"P{pid}"
        if sku in used:
            sku = f"{sku}-{pid}"
        used.add(sku)
        conn.execute(sa.text("UPDATE products SET sku=:s WHERE id=:i"), {"s": sku, "i": pid})
    op.create_unique_constraint("uq_products_sku", "products", ["sku"])
    # Chunk cũ đặt tên theo id tự tăng (product_<id>.catalog) -> bỏ để dựng lại theo sku lúc khởi động
    conn.execute(sa.text("DELETE FROM rag_chunks WHERE namespace='products'"))


def downgrade() -> None:
    op.drop_constraint("uq_products_sku", "products", type_="unique")
    op.drop_column("products", "sku")
