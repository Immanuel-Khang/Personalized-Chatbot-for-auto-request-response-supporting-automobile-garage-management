#!/usr/bin/env python3
"""
Update product descriptions from CSV file.
Maps CSV data to existing products and fills in the description column.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Brand, CatalogProduct, VehicleModel
from app.db.session import SessionLocal


def update_descriptions(csv_path: str) -> None:
    """Update product descriptions from CSV."""

    with open(csv_path, encoding="utf-8") as f:
        lines = f.readlines()
        # Use row 2 (English headers) and skip to row 11 (data starts)
        header = lines[1].strip()
        data_lines = [header] + lines[10:]
        reader = csv.DictReader(data_lines)
        rows = list(reader)

    print(f"Processing {len(rows)} rows from {csv_path}")

    db = SessionLocal()
    try:
        updated_count = 0
        not_found_count = 0
        skipped_count = 0

        for idx, row in enumerate(rows, start=1):
            brand_name = row.get("Brand", "").strip()
            model_name = row.get("Model", "").strip()
            version = row.get("Version", "").strip()
            summary = row.get("Summary_For_Search", "").strip()

            if not brand_name or not model_name:
                skipped_count += 1
                continue

            if not summary:
                skipped_count += 1
                continue

            # Find brand
            brand = db.query(Brand).filter_by(name=brand_name).first()
            if not brand:
                print(f"  Row {idx}: Brand '{brand_name}' not found")
                not_found_count += 1
                continue

            # Find model
            model = db.query(VehicleModel).filter_by(
                brand_id=brand.id,
                name=model_name
            ).first()
            if not model:
                print(f"  Row {idx}: Model '{model_name}' not found for brand '{brand_name}'")
                not_found_count += 1
                continue

            # Find matching products
            products = db.query(CatalogProduct).filter_by(
                brand_id=brand.id,
                model_id=model.id
            ).all()

            if not products:
                print(f"  Row {idx}: No products found for {brand_name} {model_name}")
                not_found_count += 1
                continue

            # Update all matching products with this description
            for product in products:
                if version:
                    # If version specified, only update if product name contains it
                    if version.lower() in product.name.lower():
                        product.description = summary
                        updated_count += 1
                        print(f"  ✓ Updated: {product.name}")
                else:
                    # No version specified, update all products of this model
                    product.description = summary
                    updated_count += 1
                    print(f"  ✓ Updated: {product.name}")

        db.commit()
        print(f"\n=== Summary ===")
        print(f"Updated: {updated_count} products")
        print(f"Not found: {not_found_count}")
        print(f"Skipped: {skipped_count}")

    except Exception as e:
        db.rollback()
        print(f"✗ Error during update: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Update product descriptions from CSV")
    parser.add_argument("csv_path", help="Path to CSV file")
    args = parser.parse_args()

    update_descriptions(args.csv_path)
