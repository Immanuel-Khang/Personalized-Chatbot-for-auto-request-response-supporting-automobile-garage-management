#!/usr/bin/env python3
"""Map extracted sales data to CSV products and fill in sales columns."""
import csv
import json
from pathlib import Path
from typing import Any

EXTRACTED_JSON = Path("data/sales_data_extracted.json")
INPUT_CSV = Path("GarageChatbotDocs/CKD_MASTER_PRODUCT_DATABASE.csv")
OUTPUT_CSV = Path("data/catalog_master_with_sales.csv")


def load_extracted_data() -> dict:
    """Load parsed PowerPoint and Word data."""
    return json.loads(EXTRACTED_JSON.read_text())


def parse_competitor_tables(slides: list[dict]) -> dict[str, dict]:
    """Extract competitor comparison data from PowerPoint slides.

    Returns: {product_model: {competitor: data}}
    """
    comparisons = {}

    for slide in slides:
        content = slide.get("content", [])
        title = ""

        # Find title line with "vs"
        for item in content:
            if isinstance(item, str) and "vs" in item:
                title = item
                break

        if not title:
            continue

        # Extract product name from title (e.g., "Wonder vs Thaco TF230| Tera 100S | Dothanh Missu T26")
        parts = title.split("vs")
        our_product = parts[0].strip()
        competitors_part = parts[1] if len(parts) > 1 else ""
        competitors = [c.strip() for c in competitors_part.replace("|", ",").split(",") if c.strip()]

        # Find comparison table
        for item in content:
            if isinstance(item, dict) and "table" in item:
                table = item["table"]
                if len(table) > 1:  # Has header + data rows
                    comparisons[our_product] = {
                        "competitors": competitors,
                        "table": table
                    }
                break

    return comparisons


def extract_selling_points(comparison_data: dict) -> dict[str, Any]:
    """Extract selling points from comparison table.

    Look for rows where our product has advantages.
    """
    if not comparison_data or "table" not in comparison_data:
        return {}

    table = comparison_data["table"]
    if len(table) < 2:
        return {}

    headers = table[0]
    our_col_idx = -1

    # Find our product column (usually last)
    for i, h in enumerate(headers):
        if any(brand in h for brand in ["Wonder", "FOTON", "Aumark", "Auman"]):
            our_col_idx = i
            break

    if our_col_idx == -1:
        our_col_idx = len(headers) - 1  # Assume last column

    advantages = []
    competitors_list = comparison_data.get("competitors", [])

    # Compare each row
    for row in table[1:]:
        if len(row) <= our_col_idx:
            continue

        feature = row[0] if row else ""
        our_value = row[our_col_idx] if our_col_idx < len(row) else ""

        # Check if we have advantage (higher number, "Có" vs "Không", larger size, etc.)
        has_advantage = False
        advantage_note = ""

        if our_value and our_value != "-":
            # Check against competitors
            competitor_values = [row[i] for i in range(1, len(row)) if i != our_col_idx]

            # Simple heuristics
            if our_value == "Có" and any(v == "Không" for v in competitor_values):
                has_advantage = True
                advantage_note = f"{feature}: có khi đối thủ không"
            elif our_value.replace(".", "").isdigit() and any(
                v.replace(".", "").isdigit() and float(our_value.replace(".", "")) > float(v.replace(".", ""))
                for v in competitor_values if v and v != "-"
            ):
                has_advantage = True
                advantage_note = f"{feature}: {our_value} (cao hơn đối thủ)"

        if has_advantage and advantage_note:
            advantages.append(advantage_note)

    return {
        "selling_points": advantages[:3],  # Top 3
        "competitors": competitors_list[:3]
    }


def match_product_to_comparison(product_name: str, comparisons: dict) -> dict:
    """Find matching comparison data for a product."""
    product_upper = product_name.upper()

    # Only process FOTON products
    if "FOTON" not in product_upper:
        return {}

    import re

    # Direct matching with comparison keys
    for key, data in comparisons.items():
        key_upper = key.upper()

        # Check for Wonder match
        if "WONDER" in product_upper and "WONDER" in key_upper:
            return extract_selling_points(data)

        # Check for Aumark models (X25, X25L, S35, S50, S70, S70L, S90)
        if "AUMARK" in product_upper:
            # Extract all alphanumeric model codes
            product_models = re.findall(r'[A-Z]\d+[A-Z]*', product_upper)
            key_models = re.findall(r'[A-Z]\d+[A-Z]*', key_upper)

            for pmodel in product_models:
                for kmodel in key_models:
                    # Match X25 with X25, X25L with X25L, etc.
                    if pmodel == kmodel:
                        return extract_selling_points(data)
                    # Match X25L with X25 if exact not found
                    if pmodel.startswith(kmodel) or kmodel.startswith(pmodel):
                        if len(pmodel) - len(kmodel) <= 1:  # Allow one extra letter
                            return extract_selling_points(data)

        # Check for Auman models (C160, C160M, C160L, C240, FV270, FV400, FV420, FV460)
        if "AUMAN" in product_upper:
            product_models = re.findall(r'[A-Z]+\d+[A-Z]*', product_upper)
            key_models = re.findall(r'[A-Z]+\d+[A-Z]*', key_upper)

            for pmodel in product_models:
                for kmodel in key_models:
                    if pmodel == kmodel:
                        return extract_selling_points(data)
                    # Match C160M/C160L with C160 base
                    if pmodel.startswith(kmodel) or kmodel.startswith(pmodel):
                        if len(pmodel) - len(kmodel) <= 1:
                            return extract_selling_points(data)

    return {}


def fill_sales_columns(input_csv: Path, output_csv: Path, extracted_data: dict) -> None:
    """Read CSV, fill sales columns, write to new CSV."""
    slides = extracted_data["powerpoint_competitor_comparison"]["slides"]
    comparisons = parse_competitor_tables(slides)

    print(f"Found {len(comparisons)} competitor comparisons in PowerPoint")
    for product, data in comparisons.items():
        print(f"  - {product}: vs {', '.join(data.get('competitors', []))}")

    with open(input_csv, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if len(rows) < 5:
        print("CSV too short, aborting")
        return

    # Find column indices for sales fields
    header_row = rows[1]  # Row 2 is English headers
    col_map = {name: i for i, name in enumerate(header_row)}

    brand_col = col_map.get("Brand", 6)
    model_col = col_map.get("Model", 8)

    # Sales columns to fill
    selling_point_cols = [
        col_map.get("Key_Selling_Point_1"),
        col_map.get("Key_Selling_Point_2"),
        col_map.get("Key_Selling_Point_3")
    ]
    competitor_cols = [
        col_map.get("Competitor_1"),
        col_map.get("Competitor_2"),
        col_map.get("Competitor_3")
    ]
    advantage_col = col_map.get("Advantage_Over_Competitor")
    benefit_col = col_map.get("Customer_Benefit")
    use_case_col = col_map.get("Primary_Use_Case")

    filled_count = 0
    foton_products = []

    # Process data rows (row 5 onwards, index 4+)
    for i in range(4, len(rows)):
        row = rows[i]
        if len(row) <= max(brand_col, model_col):
            continue

        brand = row[brand_col] if brand_col < len(row) else ""
        model = row[model_col] if model_col < len(row) else ""

        if not brand or brand == "-":
            continue

        product_name = f"{brand} {model}".strip()

        # Collect all FOTON products for mapping report
        if brand == "FOTON":
            foton_products.append(model)

        sales_data = match_product_to_comparison(product_name, comparisons)

        if sales_data:
            # Extend row if needed
            while len(row) < len(header_row):
                row.append("")

            # Fill selling points
            points = sales_data.get("selling_points", [])
            for j, col_idx in enumerate(selling_point_cols):
                if col_idx is not None and j < len(points):
                    row[col_idx] = points[j]

            # Fill competitors
            competitors = sales_data.get("competitors", [])
            for j, col_idx in enumerate(competitor_cols):
                if col_idx is not None and j < len(competitors):
                    row[col_idx] = competitors[j]

            # Generic advantage and benefit
            if advantage_col is not None and points:
                row[advantage_col] = f"Vượt trội về: {', '.join([p.split(':')[0] for p in points[:2]])}"

            if benefit_col is not None:
                row[benefit_col] = "Hiệu suất cao, độ bền tốt, chi phí vận hành hợp lý"

            if use_case_col is not None:
                row[use_case_col] = "Vận chuyển hàng hóa, logistics, kinh doanh vận tải"

            rows[i] = row
            filled_count += 1
            print(f"  Filled: {product_name}")

    # Write output
    with open(output_csv, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"\nFilled sales data for {filled_count} products")
    print(f"Output: {output_csv}")

    # Generate mapping report
    print(f"\n=== FOTON products in CSV that need manual mapping ===")
    print(f"CSV has {len(foton_products)} FOTON products:")
    for model in foton_products:
        print(f"  - {model}")
    print(f"\nPowerPoint has {len(comparisons)} comparison tables:")
    for key in comparisons.keys():
        print(f"  - {key}")
    print("\nThese need manual review to create proper mapping.")


def main():
    extracted = load_extracted_data()
    fill_sales_columns(INPUT_CSV, OUTPUT_CSV, extracted)


if __name__ == "__main__":
    main()
