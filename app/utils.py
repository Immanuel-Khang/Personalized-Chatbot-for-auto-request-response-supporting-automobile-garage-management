def fmt_vnd(n: int) -> str:
    """850000000 -> '850.000.000 đồng'. Dùng MỘT hàm này ở mọi nơi để Output Guard so khớp được."""
    return f"{n:,}".replace(",", ".") + " đồng"
