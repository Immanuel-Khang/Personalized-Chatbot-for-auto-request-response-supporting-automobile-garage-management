#!/usr/bin/env python3
"""
Reset database and import fresh data from CSV.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal

# Import the CSV import function
from import_catalog_csv import import_csv


def reset_database():
    """Delete all products and related data."""
    db = SessionLocal()
    try:
        print("Deleting all existing data...")
        # Delete in correct order due to foreign keys
        db.execute(text("DELETE FROM data_issues"))
        db.execute(text("DELETE FROM product_sources"))
        db.execute(text("DELETE FROM product_prices"))
        db.execute(text("DELETE FROM product_media"))
        db.execute(text("DELETE FROM product_competitors"))
        db.execute(text("DELETE FROM vehicle_specs"))
        db.execute(text("DELETE FROM products"))
        db.execute(text("DELETE FROM vehicle_models"))
        db.execute(text("DELETE FROM brands"))
        db.execute(text("DELETE FROM catalog_sources"))

        db.commit()
        print("✓ Deleted all existing data")
        return True
    except Exception as e:
        db.rollback()
        print(f"✗ Error during reset: {e}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Reset database and import from CSV")
    parser.add_argument("csv_path", help="Path to CSV file")
    parser.add_argument("--effective-date", help="Effective date for prices (YYYY-MM-DD)")
    args = parser.parse_args()

    # Reset database
    if not reset_database():
        sys.exit(1)

    # Import fresh data
    print("\nImporting fresh data from CSV...")
    from datetime import datetime
    eff_date = None
    if args.effective_date:
        eff_date = datetime.strptime(args.effective_date, "%Y-%m-%d").date()

    import_csv(args.csv_path, eff_date)
