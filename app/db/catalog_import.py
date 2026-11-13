"""Nạp CKD_MASTER_PRODUCT_DATABASE.csv vào danh mục (products, vehicle_specs, product_prices...) ở trạng thái DRAFT.

    python -m app.db.catalog_import FILE.csv --price-as-of 2026-10-18 [--dry-run] [--report out.md]

Quy tắc (docs/catalog_hybrid.dbml): "-" là NULL; giá trị nhiều phương án ("990/1210") để NULL và giữ ở spec_extra;
giá trị không tin được thì để NULL và ghi data_issues, không tự sửa. Chuỗi gốc luôn nằm ở spec_raw.
Đọc nguồn: dòng 2 là header, dòng 3-4 là mô tả cột, dữ liệu bắt đầu từ dòng 5 (dòng có Brand)."""
import argparse
import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.db.models import (Brand, CatalogProduct, CatalogSource, DataIssue, ProductCompetitor, ProductPrice,
                           ProductSource, VehicleModel, VehicleSpec)
from app.db.session import SessionLocal

BLANK = {"", "-", "--", "---"}
BRAND_ALIASES = {"ĐÔ THÀNH": "DOTHANH"}
FIRST_DATA_LINE = 5
RPM_RANGE = (1000, 8000)
PRICE_RANGE_MILLION = (Decimal(100), Decimal(5000))  # nguồn ghi triệu đồng; ngoài khoảng là lệch thang
BASIS_WORDS = (("chassis", "CHASSIS"), ("lửng", "FLATBED"), ("mui bạt", "TARP"))


@dataclass
class Issue:
    field: str
    raw: str
    code: str
    note: str
    resolution: str | None = None  # có nội dung = đã xử lý theo quy tắc, không cần người duyệt


@dataclass
class Parsed:
    line: int
    brand: str
    model: str
    spec: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    price: dict | None = None
    sales_info: dict | None = None
    competitors: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def clean(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return None if value in BLANK else value


def int_field(raw: str, name: str, issues: list[Issue]) -> int | None:
    """'2865' -> 2865; '2.865' -> 2865 (dấu chấm hàng nghìn, ghi lại); còn lại -> NULL + issue."""
    s = raw.replace(" ", "")
    if re.fullmatch(r"\d+", s):
        return int(s)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        value = int(s.replace(".", ""))
        issues.append(Issue(name, raw, "thousands_separator", f"đọc {raw} là {value}", f"dấu chấm hàng nghìn -> {value}"))
        return value
    issues.append(Issue(name, raw, "unparsed", "không đọc được thành số nguyên"))
    return None


def dec_field(raw: str, name: str, issues: list[Issue]) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        issues.append(Issue(name, raw, "unparsed", "không đọc được thành số"))
        return None


def rpm_field(raw: str, name: str, issues: list[Issue]) -> int | None:
    if (value := clean(raw)) is None:
        return None
    rpm = int_field(value, name, issues)
    if rpm is not None and not RPM_RANGE[0] <= rpm <= RPM_RANGE[1]:
        issues.append(Issue(name, raw, "scale_suspect", f"vòng tua {rpm} ngoài khoảng {RPM_RANGE}, để NULL"))
        return None
    return rpm


def parse_mass(raw: str, issues: list[Issue]) -> tuple[int | None, str | None]:
    m = re.fullmatch(r"([\d.]+)\s*(?:[-(]\s*([^)]+?)\s*\)?)?", raw)
    if not m:
        issues.append(Issue("curb_mass_kg", raw, "unparsed", "không đọc được khối lượng"))
        return None, None
    return int_field(m.group(1), "curb_mass_kg", issues), (m.group(2) or "").lower() or None


def parse_power(raw: str, issues: list[Issue]) -> tuple[Decimal | None, int | None]:
    parts = [p.strip() for p in re.sub(r"\([^)]*\)", "", raw).split("/")]
    kw = dec_field(parts[0], "power_kw", issues) if parts[0] else None
    rpm = rpm_field(parts[-1], "power_rpm", issues) if len(parts) > 1 else None
    return kw, rpm


def parse_torque(raw: str, issues: list[Issue]) -> tuple[Decimal | None, int | None, int | None]:
    nm_raw, _, rest = raw.partition("/")
    nm = dec_field(nm_raw.strip(), "torque_nm", issues) if nm_raw.strip() else None
    ends = [rpm_field(p, "torque_rpm", issues) for p in re.split(r"\s*[-–]\s*", rest.strip()) if clean(p)]
    return nm, (ends[0] if ends else None), (ends[-1] if ends else None)


def parse_triple(raw: str, name: str, issues: list[Issue]) -> tuple[list[int | None], str | None]:
    """'7025 x 2325 x 2350 (chassis)' -> ([7025, 2325, 2350], 'chassis'). Mỗi số có '/' (phương án) -> None."""
    m = re.fullmatch(r"([\d/]+)\s*[x×]\s*([\d/]+)\s*[x×]\s*([\d/]+)\s*(?:\((\w+)\))?", raw, re.IGNORECASE)
    if not m:
        issues.append(Issue(name, raw, "unparsed", "không đọc được kích thước"))
        return [None, None, None], None
    return [int(g) if g.isdigit() else None for g in m.groups()[:3]], (m.group(4) or "").lower() or None


def parse_price(raw: str, issues: list[Issue]) -> dict | None:
    m = re.match(r"([\d.,]+)\s*(.*)", raw)
    if not m:
        issues.append(Issue("msrp", raw, "unparsed", "không đọc được giá"))
        return None
    value = dec_field(m.group(1), "msrp", issues)
    if value is None:
        return None
    if not PRICE_RANGE_MILLION[0] <= value <= PRICE_RANGE_MILLION[1]:
        issues.append(Issue("msrp", raw, "scale_suspect",
                            f"{value} triệu đồng ngoài khoảng {PRICE_RANGE_MILLION[0]}-{PRICE_RANGE_MILLION[1]}; "
                            "không tạo giá, cần người xác nhận"))
        return None
    rest = m.group(2).lower()
    price = {"amount_vnd": int(value * 1_000_000), "basis_note": raw[:120], "needs_review": False,
             "basis": next((code for word, code in BASIS_WORDS if word in rest), "UNKNOWN")}
    if re.search(r"\(\s*[\d.,]+\s*\)", rest):
        price["needs_review"] = True
        issues.append(Issue("msrp", raw, "multiple_prices", "nguồn ghi 2 mức giá; lấy mức đầu, đánh dấu cần duyệt"))
    return price


def parse_sales_info(row: dict[str, str]) -> dict | None:
    """Parse sales/marketing columns (Key_Selling_Point, Customer_Benefit, etc.)."""
    g = lambda col: clean(row.get(col))  # noqa: E731
    info = {}

    # Collect selling points
    points = [p for i in range(1, 4) if (p := g(f"Key_Selling_Point_{i}"))]
    if points:
        info["selling_points"] = points

    # Customer benefit and competitive advantage
    if benefit := g("Customer_Benefit"):
        info["customer_benefit"] = benefit
    if advantage := g("Competitive_Advantage"):
        info["competitive_advantage"] = advantage
    if adv_over := g("Advantage_Over_Competitor"):
        info["advantage_over_competitor"] = adv_over
    if disadv := g("Disadvantage_Over_Competitor"):
        info["disadvantage_over_competitor"] = disadv

    # Use cases and suitability
    if use_case := g("Primary_Use_Case"):
        info["primary_use_case"] = use_case
    if suitable := g("Suitable_For"):
        info["suitable_for"] = suitable

    # FAQs
    faqs = []
    for i in range(1, 4):
        if q := g(f"FAQ_{i}_Question"):
            faq = {"question": q}
            if a := g(f"FAQ_{i}_Answer"):
                faq["answer"] = a
            faqs.append(faq)
    if faqs:
        info["faqs"] = faqs

    # Target customer
    if target := g("Target_Customer"):
        info["target_customer"] = target

    return info if info else None


def parse_competitors(row: dict[str, str]) -> list[dict]:
    """Parse competitor columns (Competitor_1/2/3)."""
    g = lambda col: clean(row.get(col))  # noqa: E731
    competitors = []

    for i in range(1, 4):
        if name := g(f"Competitor_{i}"):
            competitors.append({"name": name})

    return competitors


def parse_row(line: int, row: dict[str, str]) -> Parsed:
    g = lambda col: clean(row.get(col))  # noqa: E731
    brand = " ".join((row["Brand"] or "").split()).upper()
    p = Parsed(line=line, brand=BRAND_ALIASES.get(brand, brand), model=g("Model") or "")
    iss, spec, extra = p.issues, p.spec, p.extra
    p.raw = {k: v.strip() for k, v in row.items() if clean(v)}
    spec["vehicle_type"] = g("Vehicle Type")
    spec["assembly"] = "CKD" if (g("CKD-CBU") or "").upper() == "CKD" else None
    spec["wheel_formula"] = g("Wheel formula")
    spec["tire_size"] = g("Tire_Size")
    spec["engine_model"] = g("Engine_Model")
    spec["gearbox_model"] = g("Transmission")
    spec["emission_standard"] = g("Emission")
    spec["has_scr"] = True if (g("Exhaust gas treatment") or "").upper() == "SCR" else None
    spec["has_abs"] = True if (g("ABS systerm") or "").lower() in ("có", "co") else None
    for col, key in (("Number of Seats", "seats"), ("Wheelbase (WB)", "wheelbase_mm"),
                     ("Ground Clearance (HH)", "ground_clearance_mm")):
        spec[key] = int_field(v, key, iss) if (v := g(col)) else None
    spec["displacement_cc"] = int_field(v, "displacement_cc", iss) if (v := g("Piston displacement")) else None
    for col, key, alt in (("Payload", "payload_kg", "load_alternatives"), ("Gross Vehicle Mass", "gvm_kg", "gvm_alternatives")):
        v = g(col)
        if v and "/" in v:
            extra[alt], spec[key] = v, None  # nhiều phương án: không chọn hộ
        else:
            spec[key] = int_field(v, key, iss) if v else None
    if v := g("Vehicle Mass"):
        spec["curb_mass_kg"], spec["curb_mass_basis"] = parse_mass(v, iss)
    if v := g("Max. power"):
        spec["power_kw"], spec["power_rpm"] = parse_power(v, iss)
    if v := g("Torque"):
        spec["torque_nm"], spec["torque_rpm_min"], spec["torque_rpm_max"] = parse_torque(v, iss)
    if v := g("Turning Radius"):
        spec["turning_radius_m"] = dec_field(v, "turning_radius_m", iss)
    if v := g("Overall Dimension"):
        (spec["length_mm"], spec["width_mm"], spec["height_mm"]), spec["dims_basis"] = parse_triple(v, "overall_dimension", iss)
    if v := g("Cargo (Length/Width/Height)"):
        (spec["cargo_length_mm"], spec["cargo_width_mm"], spec["cargo_height_mm"]), _ = parse_triple(v, "cargo", iss)
        if "/" in v:
            extra["cargo_alternatives"] = v
    if v := g("Transmission Type"):
        spec["gears_forward"] = int(m.group(1)) if (m := re.search(r"(\d+)\s*số tiến", v)) else None
        spec["gears_reverse"] = int(m.group(1)) if (m := re.search(r"(\d+)\s*số lùi", v)) else None
    if v := g("Fuel_Tank"):
        extra["fuel_tank_raw"] = v  # nguồn không ghi đơn vị, chưa điền fuel_tank_l
    for col, key in (("Front – Rear Tread (AW / CW)", "treads_mm"), ("Cabin Width", "cabin_width_mm"),
                     ("Front Overhang (FOH", "front_overhang_mm"), ("Rear Overhang(ROH)", "rear_overhang_mm"),
                     ("Axle_Ratio", "axle_ratio"), ("Transmission Ratio", "gear_ratios"),
                     ("Suspension System Front – Rear", "suspension"), ("leaf spring", "leaf_springs"),
                     ("Brake System Front – Rear", "brakes"), ("Steering System", "steering")):
        if v := g(col):
            extra[key] = v
    if v := g("MSRP"):
        p.price = parse_price(v, iss)

    # Parse sales/marketing data
    p.sales_info = parse_sales_info(row)
    p.competitors = parse_competitors(row)

    if not p.model:
        iss.append(Issue("model", "", "identity_missing", "dòng không có tên model; giữ DRAFT"))
    return p


def read_rows(path: Path) -> list[Parsed]:
    raw = list(csv.reader(path.open(encoding="utf-8-sig", newline="")))
    header = [h.strip() for h in raw[1]]
    out: list[Parsed] = []
    for n, cells in enumerate(raw, start=1):
        if n >= FIRST_DATA_LINE and len(cells) > 6 and clean(cells[header.index("Brand")]):
            out.append(parse_row(n, dict(zip(header, cells))))
    return out


def display_names(groups: dict[tuple, list[Parsed]]) -> dict[tuple, str]:
    """name = '<HÃNG> <Model>'; cùng model nhiều cấu hình thì thêm WB, rồi tải trọng, rồi số thứ tự; model trống -> mã dòng."""
    names: dict[tuple, str] = {}
    for (brand, model), rows in groups.items():
        for i, p in enumerate(rows, start=1):
            if not model:
                names[(brand, model, p.line)] = f"{brand} (chưa rõ model, dòng {p.line})"
                continue
            base = f"{brand} {model}"
            wbs = {r.spec.get("wheelbase_mm") for r in rows}
            payloads = {r.spec.get("payload_kg") for r in rows}
            if len(rows) == 1:
                names[(brand, model, p.line)] = base
            elif len(wbs) == len(rows):
                names[(brand, model, p.line)] = f"{base} (WB {p.spec.get('wheelbase_mm')})"
            elif len(payloads) == len(rows):
                names[(brand, model, p.line)] = f"{base} (tải {p.spec.get('payload_kg')} kg)"
            else:
                names[(brand, model, p.line)] = f"{base} (cấu hình {i})"
    return names


def import_csv(path: Path, price_as_of: date, dry_run: bool = False) -> list[dict]:
    """Ghi toàn bộ trong một transaction; dry_run thì rollback. Trả về bản tóm tắt từng sản phẩm để duyệt."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    first_by_key: dict[tuple, Parsed] = {}
    dup_lines: dict[int, list[int]] = defaultdict(list)
    unique: list[Parsed] = []
    for p in read_rows(path):  # dòng trùng hệt (mọi ô giống nhau) gộp thành một sản phẩm
        if (first := first_by_key.setdefault(tuple(sorted(p.raw.items())), p)) is p:
            unique.append(p)
        else:
            dup_lines[first.line].append(p.line)
    groups: dict[tuple, list[Parsed]] = defaultdict(list)
    for p in unique:
        groups[(p.brand, p.model)].append(p)
    names = display_names(groups)
    summary: list[dict] = []
    with SessionLocal() as db:
        if db.query(CatalogSource).filter_by(sha256=sha).first():
            raise SystemExit(f"{path.name} (sha256 {sha[:12]}) đã được nạp trước đó")
        source = CatalogSource(file_name=path.name, sha256=sha,
                               source_modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
        db.add(source)
        db.flush()
        brands: dict[str, Brand] = {b.name: b for b in db.query(Brand)}
        models: dict[tuple, VehicleModel] = {}
        used_names = {n for (n,) in db.query(CatalogProduct.name)}
        now = datetime.now(timezone.utc)
        for p in unique:
            brand = brands.get(p.brand)
            if brand is None:
                brand = brands[p.brand] = Brand(name=p.brand, aliases=[])
                db.add(brand)
                db.flush()
            spelled = " ".join(p.raw["Brand"].split()).upper()
            if spelled != p.brand and spelled not in brand.aliases:
                brand.aliases = [*brand.aliases, spelled]
            model = None
            if p.model:
                if (brand.id, p.model) not in models:
                    models[(brand.id, p.model)] = existing = (
                        db.query(VehicleModel).filter_by(brand_id=brand.id, name=p.model).first()
                        or VehicleModel(brand_id=brand.id, name=p.model))
                    db.add(existing)
                    db.flush()
                model = models[(brand.id, p.model)]
            name = names[(p.brand, p.model, p.line)]
            if name in used_names:
                name = f"{name} (dòng {p.line})"
            used_names.add(name)
            product = CatalogProduct(kind="TRUCK", name=name, brand_id=brand.id, model_id=model.id if model else None,
                                     status="DRAFT", sales_info=p.sales_info)
            db.add(product)
            db.flush()
            db.add(VehicleSpec(product_id=product.id, spec_extra=p.extra or None, spec_raw=p.raw,
                               **{k: v for k, v in p.spec.items() if v is not None}))
            if p.price:
                db.add(ProductPrice(product_id=product.id, price_type="MSRP", effective_date=price_as_of, **p.price))

            # Add competitor data
            adv_over = p.sales_info.get("advantage_over_competitor") if p.sales_info else None
            disadv = p.sales_info.get("disadvantage_over_competitor") if p.sales_info else None
            for comp in p.competitors:
                db.add(ProductCompetitor(
                    product_id=product.id,
                    competitor_name=comp["name"],
                    advantage=adv_over,
                    disadvantage=disadv,
                    source=path.name
                ))

            for line in (p.line, *dup_lines[p.line]):
                db.add(ProductSource(product_id=product.id, source_id=source.id, sheet="csv", row_ref=f"line:{line}"))
            if dup_lines[p.line]:
                p.issues.append(Issue("row", "", "duplicate_row", f"dòng {dup_lines[p.line]} trùng hệt dòng {p.line}",
                                      "gộp thành một sản phẩm"))
            for i in p.issues:
                db.add(DataIssue(product_id=product.id, field=i.field, raw_value=i.raw, code=i.code, note=i.note,
                                 resolution=i.resolution, resolved_at=now if i.resolution else None))
            summary.append({"id": product.id, "name": name, "line": p.line, "spec": p.spec, "price": p.price,
                            "open_issues": [i for i in p.issues if not i.resolution]})
        db.rollback() if dry_run else db.commit()
    return summary


def write_report(summary: list[dict], out: Path, price_as_of: date, dry_run: bool) -> None:
    fmt = lambda v: "" if v is None else str(v)  # noqa: E731
    lines = [f"# Kết quả nạp CSV ({'dry-run, đã rollback' if dry_run else 'đã ghi, trạng thái DRAFT'})",
             f"Ngày hiệu lực giá: {price_as_of}. {len(summary)} sản phẩm.", "",
             "| id | dòng | tên | WB | tải trọng | tổng tải | giá (đồng) | cơ sở giá | cần duyệt |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        sp, pr = s["spec"], s["price"] or {}
        flag = "; ".join(f"{i.field}:{i.code}" for i in s["open_issues"]) + ("; msrp:needs_review" if pr.get("needs_review") else "")
        lines.append(f"| {s['id']} | {s['line']} | {s['name']} | {fmt(sp.get('wheelbase_mm'))} | {fmt(sp.get('payload_kg'))} | "
                     f"{fmt(sp.get('gvm_kg'))} | {fmt(pr.get('amount_vnd'))} | {fmt(pr.get('basis'))} | {flag} |")
    lines += ["", "## Vấn đề chưa xử lý", ""]
    lines += [f"- dòng {s['line']} ({s['name']}): `{i.field}` = `{i.raw}` [{i.code}] {i.note}"
              for s in summary for i in s["open_issues"]]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--price-as-of", type=date.fromisoformat, required=True, help="ngày hiệu lực của MSRP, YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    summary = import_csv(args.csv, args.price_as_of, args.dry_run)
    if args.report:
        write_report(summary, args.report, args.price_as_of, args.dry_run)
    print(f"{len(summary)} sản phẩm, {sum(bool(s['price']) for s in summary)} có giá, "
          f"{sum(len(s['open_issues']) for s in summary)} vấn đề chưa xử lý" + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
