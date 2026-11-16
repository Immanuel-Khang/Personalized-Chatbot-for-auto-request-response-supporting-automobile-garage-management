#!/usr/bin/env python3
"""Rebuild Summary_For_Search from the structured catalog columns so RAG (BM25 + vector) has rich text to match.

Rows whose durability/fuel rating is "chưa đủ dữ liệu / chưa rõ" get an estimated low/medium/high rating derived from
the configuration (engine type and maker, warranty note, power/rpm). Estimated ratings are labelled as estimates.

    python scripts/refine_catalog_summaries.py "catalog_master_with_sales_mota (3).csv" out.csv [--report]
"""
import argparse
import csv
import re
import sys
from pathlib import Path

SIZE = {"siêu nhỏ gọn": "xe tải siêu nhỏ gọn", "nhẹ": "xe tải nhẹ", "cỡ nhẹ–trung": "xe tải nhẹ đến trung",
        "cỡ trung": "xe tải trung", "cỡ trung–nặng": "xe tải trung đến nặng"}
REPUTABLE = re.compile(r"isuzu|hyundai|hino|fuso|mitsubishi|mercedes|cummins|toyota", re.I)
KNOWN_MAKER = re.compile(r"weichai|yuchai|ycy|jac|hfc|foton|4f2|isf|jx493|je493|isuzu", re.I)
LONG_WARRANTY = re.compile(r"bảo hành (tới|đến) \d+ năm", re.I)


def clean(v: str | None) -> str:
    v = (v or "").strip()
    return "" if v in ("-", "--", "---") else v


def parse_old(s: str) -> dict:
    g = lambda pat: (m.group(1).strip() if (m := re.search(pat, s, re.S)) else "")  # noqa: E731
    return {"size": g(r"\):\s*([^,]+?)(?:,|\.)"), "load": g(r"mức chịu tải ([^.]+)\."),
            "price": g(r"Giá ([^.]*?) so với"), "no_price": "Giá chưa có dữ liệu" in s,
            "dur": g(r"Độ bền ([^(.]+?)\s*(?:\(|\.)"), "dur_why": g(r"Độ bền[^(.]*\(([^)]*)\)"),
            "fuel": g(r"Tiết kiệm nhiên liệu ([^.(]+?)\s*(?:\(|\.)"), "uses": g(r"Phù hợp làm: ([^.]*?)\. "),
            "who": g(r"Phù hợp với: (.*?)\. (?:Không hợp|Từ khóa)"), "avoid": g(r"Không hợp: ([^.]*?)\.")}


def power(row: dict) -> tuple[float | None, float | None, int | None]:
    raw = clean(row.get("Max. power"))
    segs = [s.strip() for s in raw.split("/")]
    m = re.match(r"\d+(?:[.,]\d+)?", segs[0]) if segs else None
    kw = float(m.group(0).replace(",", ".")) if m else None
    hp = float(h.group(1)) if (h := re.search(r"\((\d+)\s*hp", raw)) else (round(kw * 1.341) if kw else None)
    rpm_txt = re.sub(r"(?<=\d)[.,](?=\d{3}$)", "", segs[-1]) if len(segs) > 1 else ""
    rpm = int(rpm_txt) if rpm_txt.isdigit() and int(rpm_txt) >= 1000 else None  # bỏ giá trị lỗi như "260", "600"
    return kw, hp, rpm


def assess(row: dict, old: dict) -> tuple[str, str, str, bool]:
    """-> (durability, why, fuel, estimated). Known ratings are kept; unknown ones are scored from the configuration."""
    kw, _hp, rpm = power(row)
    gasoline = bool(rpm and rpm >= 5000)
    dur, why, est = old["dur"], old["dur_why"], False
    if dur not in ("cao", "trung bình", "thấp"):
        engine = f"{clean(row.get('Engine_Model'))} {clean(row.get('Durability_Note'))}"
        score = 2 if REPUTABLE.search(engine) else 1 if KNOWN_MAKER.search(engine) else 0
        score += -1 if gasoline else 1  # máy dầu bền hơn máy xăng nhỏ vòng tua cao khi chạy kinh doanh
        score += 1 if LONG_WARRANTY.search(engine) else 0
        dur = "cao" if score >= 4 else "trung bình" if score >= 1 else "thấp"
        bits = ["máy xăng nhỏ vòng tua cao" if gasoline else "máy dầu", f"động cơ {clean(row.get('Engine_Model')) or 'chưa rõ hãng'}"]
        if LONG_WARRANTY.search(engine):
            bits.append("bảo hành dài")
        why, est = "ước tính theo cấu hình: " + ", ".join(bits) + "; chưa có dữ liệu sử dụng thực tế", True
    fuel = old["fuel"]
    if fuel not in ("cao", "trung bình", "thấp"):
        fuel = "thấp" if gasoline and (kw or 0) > 70 else "trung bình"
    return dur, why, fuel, est


def syn(old: dict, dur: str, fuel: str) -> list[str]:
    out = [SIZE.get(old["size"].replace("xe tải ", ""), old["size"]), f"độ bền {dur}"]
    out += {"cao": ["giá cao", "đắt", "đầu tư lớn"], "thấp": ["giá rẻ", "giá thấp", "tiết kiệm vốn"]}.get(
        old["price"], ["giá vừa phải", "giá trung bình"])
    out += {"cao": ["bền", "bền bỉ", "ít hỏng vặt"], "trung bình": ["bền vừa phải"], "thấp": ["độ bền thấp", "cần bảo dưỡng kỹ"]}[dur]
    out += {"cao": ["tiết kiệm nhiên liệu", "ăn ít dầu"], "thấp": ["tốn nhiên liệu", "tiêu hao nhiều"]}.get(fuel, ["tiêu hao trung bình"])
    load = old["load"]
    out += ["chở nặng", "tải trọng lớn"] if "lớn" in load else ["chở nhẹ", "tải nhỏ"] if load.startswith("nhỏ") else ["tải vừa"]
    blob = f"{old['uses']} {old['who']}".lower()  # không đưa "Không hợp" vào từ khóa, nếu không tìm 'xe chạy hẻm' sẽ ra xe không hợp hẻm
    for key, words in (("nội thành", ["nội thành", "nội đô", "chạy phố"]), ("đường dài", ["đường dài", "liên tỉnh", "tuyến dài"]),
                       ("hẻm", ["hẻm", "đường nhỏ"]), ("lạnh", ["hàng lạnh", "đông lạnh"]), ("thương mại điện tử", ["giao hàng", "thương mại điện tử"])):
        if key in blob:
            out += words
    return list(dict.fromkeys(out))


HEAVY_TONNES = 7.0  # "chở hàng nặng" chỉ đúng với xe có tải trọng từ mức này; xe nhẹ hơn ghi "chở hàng tải vừa"


def cargo_use(uses: str, tonnes: float | None) -> str:
    """Nhãn "Phù hợp làm" phải khớp tải trọng thật: nguồn gán 'chở hàng nặng' cho xe 3-4 tấn và bỏ sót xe 7-9 tấn."""
    if tonnes is None:
        return uses
    if tonnes < HEAVY_TONNES:
        return uses.replace("chở hàng nặng", "chở hàng tải vừa")
    return uses if "chở hàng nặng" in uses else f"chở hàng nặng, {uses}" if uses else "chở hàng nặng"


def build(row: dict, old: dict) -> str:
    dur, why, fuel, est = assess(row, old)
    kw, hp, rpm = power(row)
    brand, model = clean(row.get("Brand")).upper(), clean(row.get("Model")) or clean(row.get("Version"))
    kind = "xe sát xi (chassis), cần đóng thùng riêng" if re.search(r"sát xi|chassis", clean(row.get("Vehicle Type")), re.I) else "xe tải có thùng"
    t = re.search(r"([\d.,]+)\s*tấn", clean(row.get("Cargo_Weight")))
    old["uses"] = cargo_use(old["uses"], float(t.group(1).replace(",", ".")) if t and re.fullmatch(r"\d+(?:[.,]\d+)?", t.group(1)) else None)
    gvm = clean(row.get("Gross Vehicle Mass"))
    gears = re.search(r"(\d+)\s*số tiến", clean(row.get("Transmission Type")))
    bk = [b.strip().lower().removeprefix("phanh ") for b in clean(row.get("Brake System Front – Rear")).split("/")]
    brake = f"{bk[0]}, sau {bk[1]}" if len(bk) == 2 and all(bk) else ""
    abs_ = clean(row.get("ABS systerm")).lower() in ("có", "co", "có abs")
    parts = [f"{model} ({brand}): {kind}, {SIZE.get(old['size'].replace('xe tải ', ''), old['size'] or 'xe tải')}, mức chịu tải {old['load']}"
             + (f", tải trọng khoảng {t.group(1)} tấn" if t else "") + (f", tổng trọng lượng {f'{int(gvm) / 1000:g}'.replace('.', ',')} tấn" if gvm.isdigit() else "") + "."]
    eng = [f"động cơ {clean(row.get('Engine_Model'))}" if clean(row.get("Engine_Model")) else "", f"{hp:.0f} mã lực" if hp else "",
           ("máy xăng" if rpm and rpm >= 5000 else "máy dầu" if rpm else ""), "tiêu chuẩn khí thải Euro 5" if clean(row.get("Emission")) == "V" else "",
           f"hộp số {int(gears.group(1))} số tiến" if gears else "", f"phanh trước {brake}" if brake else "", "có ABS" if abs_ else "",
           f"bình nhiên liệu {clean(row.get('Fuel_Tank'))} lít" if clean(row.get("Fuel_Tank")).isdigit() else "",
           f"bán kính quay vòng {clean(row.get('Turning Radius'))} m" if clean(row.get("Turning Radius")) else ""]
    parts.append("Cấu hình: " + ", ".join(e for e in eng if e) + ".")
    parts.append("Chưa có giá niêm yết, cần liên hệ báo giá." if old["no_price"] else f"Giá {old['price']} so với xe cùng phân khúc.")
    parts.append(f"Độ bền {dur}" + (f" ({why})" if why else "") + ".")
    fuel_txt = {"cao": "Tiết kiệm nhiên liệu cao, tiêu hao ít", "trung bình": "Mức tiêu hao nhiên liệu trung bình",
                "thấp": "Tiêu hao nhiên liệu nhiều, kém tiết kiệm"}[fuel]
    parts.append(fuel_txt + (" (ước tính theo loại máy và công suất)" if old["fuel"] != fuel else "") + ".")
    for label, key in (("Phù hợp làm", "uses"), ("Phù hợp với", "who"), ("Không hợp", "avoid")):
        if old[key]:
            parts.append(f"{label}: {old[key]}.")
    parts.append("Từ khóa: " + ", ".join(syn(old, dur, fuel)) + ".")
    return " ".join(parts), (model, brand, dur, fuel, est)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path), ap.add_argument("dst", type=Path)
    args = ap.parse_args()
    raw = list(csv.reader(args.src.open(encoding="utf-8-sig", newline="")))
    head = [h.strip() for h in raw[1]]
    ib, isum = head.index("Brand"), head.index("Summary_For_Search")
    changes = []
    for n, r in enumerate(raw, 1):
        if n >= 5 and len(r) > ib and clean(r[ib]):
            r += [""] * (len(head) - len(r))
            row = dict(zip(head, r))
            if not clean(r[isum]):
                continue
            r[isum], info = build(row, parse_old(r[isum]))
            changes.append(info)
    with args.dst.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(raw)
    est = [c for c in changes if c[4]]
    print(f"{len(changes)} summaries rebuilt, {len(est)} with estimated durability")
    for model, brand, dur, fuel, _ in est:
        print(f"  {brand} {model}: độ bền {dur}, nhiên liệu {fuel}")


if __name__ == "__main__":
    sys.exit(main())
