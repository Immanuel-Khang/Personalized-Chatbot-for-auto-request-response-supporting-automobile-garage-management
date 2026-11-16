"""Khóa tự nhiên ổn định cho sản phẩm (products.sku).

id tự tăng khác nhau giữa các máy (clean/import lại không reset sequence), nên không được dùng để nối dữ liệu
giữa products và rag_chunks. sku lấy từ cột Product_ID của CSV nếu có; CSV hiện để trống cột này nên suy ra từ tên
hiển thị (tên đã được import đảm bảo duy nhất), cùng một file CSV luôn ra cùng sku trên mọi máy."""
import re
import unicodedata


def slugify_sku(value: str) -> str:
    s = (value or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")[:100]
