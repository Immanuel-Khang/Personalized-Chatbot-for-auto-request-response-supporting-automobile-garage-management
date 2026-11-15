#!/usr/bin/env python3
"""
Import catalog CSV with sales descriptions into the database.
Handles Vietnamese product data with rich natural language descriptions for RAG/embedding search.
"""
import csv
import hashlib
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session

from app.db.models import (
    Brand, CatalogProduct, CatalogSource, ProductPrice, ProductSource, VehicleModel, VehicleSpec, DataIssue
)
from app.db.session import SessionLocal


def parse_int(val: str) -> int | None:
    """Parse integer, handling commas and parentheses."""
    if not val or val in ("-", "---", ""):
        return None
    val = val.strip().replace(",", "").replace(".", "")
    match = re.search(r"(\d+)", val)
    return int(match.group(1)) if match else None


def parse_decimal(val: str) -> Decimal | None:
    """Parse decimal, handling commas."""
    if not val or val in ("-", "---", ""):
        return None
    val = val.strip().replace(",", ".")
    match = re.search(r"(\d+(?:[.,]\d+)?)", val)
    if match:
        try:
            return Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            return None
    return None


def parse_power(val: str) -> tuple[Decimal | None, int | None]:
    """Parse power string like '127 (170hp)/2500' -> (kW, rpm)."""
    if not val or val in ("-", "---"):
        return None, None
    parts = val.split("/")
    kw = parse_decimal(parts[0])
    rpm = parse_int(parts[1]) if len(parts) > 1 else None
    return kw, rpm


def parse_torque(val: str) -> tuple[Decimal | None, int | None, int | None]:
    """Parse torque like '520/1500 - 2500' -> (Nm, rpm_min, rpm_max)."""
    if not val or val in ("-", "---"):
        return None, None, None
    parts = val.split("/")
    nm = parse_decimal(parts[0])
    if len(parts) > 1:
        rpm_range = parts[1].replace(" ", "")
        if "-" in rpm_range:
            rpm_parts = rpm_range.split("-")
            rpm_min = parse_int(rpm_parts[0])
            rpm_max = parse_int(rpm_parts[1])
        else:
            rpm_min = parse_int(rpm_range)
            rpm_max = None
    else:
        rpm_min, rpm_max = None, None
    return nm, rpm_min, rpm_max


def parse_dimensions(val: str) -> tuple[int | None, int | None, int | None]:
    """Parse dimensions like '8040 x 2340 x 3440' -> (length, width, height)."""
    if not val or val in ("-", "---"):
        return None, None, None
    parts = [p.strip() for p in val.replace("x", " ").replace("X", " ").split()]
    nums = [parse_int(p) for p in parts if p]
    if len(nums) >= 3:
        return nums[0], nums[1], nums[2]
    return None, None, None


def parse_cargo_dimensions(val: str) -> tuple[int | None, int | None, int | None]:
    """Parse cargo dimensions, handling formats like '6100 x 2220 x 780/2100'."""
    if not val or val in ("-", "---"):
        return None, None, None
    val = val.replace("/", " ")
    parts = [p.strip() for p in val.replace("x", " ").replace("X", " ").split()]
    nums = [parse_int(p) for p in parts if p]
    if len(nums) >= 3:
        return nums[0], nums[1], nums[2]
    return None, None, None


def parse_gears(val: str) -> tuple[int | None, int | None]:
    """Parse gears like '06 số tiến + 01 số lùi' -> (forward, reverse)."""
    if not val or val in ("-", "---"):
        return None, None
    forward_match = re.search(r"(\d+)\s*số\s*tiến", val)
    reverse_match = re.search(r"(\d+)\s*số\s*lùi", val)
    forward = int(forward_match.group(1)) if forward_match else None
    reverse = int(reverse_match.group(1)) if reverse_match else None
    return forward, reverse


def parse_price_basis(val: str) -> str:
    """Determine price basis from Vietnamese notes."""
    if not val:
        return "UNKNOWN"
    val_lower = val.lower()
    if "chassis" in val_lower or "sát xi" in val_lower:
        return "CHASSIS"
    if "flatbed" in val_lower or "lửng" in val_lower:
        return "FLATBED"
    if "bạt" in val_lower or "tarp" in val_lower:
        return "TARP"
    if "complete" in val_lower or "hoàn chỉnh" in val_lower or "thùng" in val_lower:
        return "COMPLETE"
    return "UNKNOWN"


def import_csv(csv_path: str, effective_date: date | None = None) -> None:
    """Import catalog CSV into database."""
    if effective_date is None:
        effective_date = date.today()

    with open(csv_path, "rb") as f:
        content = f.read()
        file_hash = hashlib.sha256(content).hexdigest()

    db = SessionLocal()
    try:
        existing = db.query(CatalogSource).filter_by(sha256=file_hash).first()
        if existing:
            print(f"File already imported: {existing.file_name} (source_id={existing.id})")
            return

        source = CatalogSource(
            file_name=Path(csv_path).name,
            sha256=file_hash,
            source_modified_at=datetime.fromtimestamp(Path(csv_path).stat().st_mtime)
        )
        db.add(source)
        db.flush()

        with open(csv_path, encoding="utf-8") as f:
            lines = f.readlines()
            # Skip first 3 rows (category headers, English headers, Vietnamese headers)
            # Row 4 onwards may have descriptive rows before actual data
            # Use row 2 (index 1) as header - English column names
            header = lines[1].strip()
            data_lines = [header] + lines[10:]  # Skip to row 11 where first product data starts
            reader = csv.DictReader(data_lines)
            rows = list(reader)

        print(f"Processing {len(rows)} rows from {csv_path}")

        brands_cache = {}
        models_cache = {}
        products_created = 0
        products_skipped = 0

        for idx, row in enumerate(rows, start=1):
            brand_name = row.get("Brand", "").strip()
            model_name = row.get("Model", "").strip()
            version = row.get("Version", "").strip()

            if not brand_name or not model_name:
                products_skipped += 1
                continue

            if brand_name not in brands_cache:
                brand = db.query(Brand).filter_by(name=brand_name).first()
                if not brand:
                    brand = Brand(name=brand_name)
                    db.add(brand)
                    db.flush()
                brands_cache[brand_name] = brand
            brand = brands_cache[brand_name]

            model_key = (brand.id, model_name)
            if model_key not in models_cache:
                model = db.query(VehicleModel).filter_by(brand_id=brand.id, name=model_name).first()
                if not model:
                    model = VehicleModel(brand_id=brand.id, name=model_name)
                    db.add(model)
                    db.flush()
                models_cache[model_key] = model
            model = models_cache[model_key]

            product_name = f"{brand_name} {model_name}"
            if version:
                product_name += f" {version}"

            product = CatalogProduct(
                kind="TRUCK",
                name=product_name,
                brand_id=brand.id,
                model_id=model.id,
                status="ACTIVE"
            )

            summary_text = row.get("Summary_For_Search", "").strip()
            if summary_text:
                product.sales_info = {"summary": summary_text}

            db.add(product)
            db.flush()

            spec = VehicleSpec(product_id=product.id)
            spec.vehicle_type = row.get("Vehicle Type", "").strip() or None
            spec.assembly = row.get("CKD-CBU", "").strip() or None
            spec.wheel_formula = row.get("Wheel formula", "").strip() or None

            spec.curb_mass_kg = parse_int(row.get("Vehicle Mass", ""))
            curb_mass_raw = row.get("Vehicle Mass", "").strip()
            if "chassis" in curb_mass_raw.lower() or "sát xi" in curb_mass_raw.lower():
                spec.curb_mass_basis = "chassis"

            spec.payload_kg = parse_int(row.get("Payload", ""))
            spec.gvm_kg = parse_int(row.get("Gross Vehicle Mass", ""))
            spec.seats = parse_int(row.get("Number of Seats", ""))

            overall_dim = row.get("Overall Dimension", "").strip()
            if overall_dim:
                length, width, height = parse_dimensions(overall_dim)
                spec.length_mm = length
                spec.width_mm = width
                spec.height_mm = height
                if "chassis" in overall_dim.lower():
                    spec.dims_basis = "chassis"

            spec.wheelbase_mm = parse_int(row.get("Wheelbase (WB)", ""))
            spec.ground_clearance_mm = parse_int(row.get("Ground Clearance (HH)", ""))
            spec.turning_radius_m = parse_decimal(row.get("Turning Radius", ""))
            spec.fuel_tank_l = parse_int(row.get("Fuel_Tank", ""))

            cargo_dim = row.get("Cargo (Length/Width/Height)", "").strip()
            if cargo_dim:
                c_len, c_wid, c_hei = parse_cargo_dimensions(cargo_dim)
                spec.cargo_length_mm = c_len
                spec.cargo_width_mm = c_wid
                spec.cargo_height_mm = c_hei

            spec.engine_model = row.get("Engine_Model", "").strip() or None
            power_str = row.get("Max. power", "").strip()
            if power_str:
                kw, rpm = parse_power(power_str)
                spec.power_kw = kw
                spec.power_rpm = rpm

            torque_str = row.get("Torque", "").strip()
            if torque_str:
                nm, rpm_min, rpm_max = parse_torque(torque_str)
                spec.torque_nm = nm
                spec.torque_rpm_min = rpm_min
                spec.torque_rpm_max = rpm_max

            spec.displacement_cc = parse_int(row.get("Piston displacement", ""))
            spec.emission_standard = row.get("Emission", "").strip() or None

            scr_val = row.get("Exhaust gas treatment", "").strip().upper()
            spec.has_scr = True if scr_val == "SCR" else None

            spec.gearbox_model = row.get("Transmission", "").strip() or None
            gears_str = row.get("Transmission Type", "").strip()
            if gears_str:
                forward, reverse = parse_gears(gears_str)
                spec.gears_forward = forward
                spec.gears_reverse = reverse

            spec.tire_size = row.get("Tire_Size", "").strip() or None

            abs_val = row.get("ABS systerm", "").strip().lower()
            spec.has_abs = True if abs_val in ("có", "yes", "có abs") else None

            db.add(spec)

            msrp_str = row.get("MSRP", "").strip()
            if msrp_str and msrp_str not in ("-", "---"):
                msrp_val = parse_decimal(msrp_str)
                if msrp_val:
                    price_vnd = int(msrp_val * 1_000_000)
                    basis = parse_price_basis(curb_mass_raw + " " + overall_dim)
                    price = ProductPrice(
                        product_id=product.id,
                        price_type="MSRP",
                        amount_vnd=price_vnd,
                        basis=basis,
                        effective_date=effective_date,
                        is_current=True
                    )
                    db.add(price)

            prod_source = ProductSource(
                product_id=product.id,
                source_id=source.id,
                sheet="Sheet1",
                row_ref=f"Row{idx}"
            )
            db.add(prod_source)

            products_created += 1

        db.commit()
        print(f"✓ Imported {products_created} products, skipped {products_skipped} invalid rows")
        print(f"  Source ID: {source.id}")

    except Exception as e:
        db.rollback()
        print(f"✗ Error during import: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Import catalog CSV with sales descriptions")
    parser.add_argument("csv_path", help="Path to CSV file")
    parser.add_argument("--effective-date", help="Effective date for prices (YYYY-MM-DD)")
    args = parser.parse_args()

    eff_date = None
    if args.effective_date:
        eff_date = datetime.strptime(args.effective_date, "%Y-%m-%d").date()

    import_csv(args.csv_path, eff_date)
