#!/usr/bin/env python3
"""Parse PowerPoint competitor comparison and Word Q&A to extract sales data."""
import json
from pathlib import Path
from pptx import Presentation
from docx import Document

DOCS_DIR = Path("GarageChatbotDocs")
PPTX_FILE = DOCS_DIR / "TSKT - So sanh doi thu 260505.pptx"
DOCX_FILE = DOCS_DIR / "20201228 Tổng hợp Q&A CSKH.docx"
OUTPUT_JSON = Path("data/sales_data_extracted.json")


def extract_text_from_pptx(pptx_path: Path) -> dict:
    """Extract all text from PowerPoint slides grouped by slide."""
    prs = Presentation(str(pptx_path))
    slides_data = []

    for i, slide in enumerate(prs.slides, 1):
        slide_text = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                slide_text.append(shape.text.strip())
            # Check tables
            if shape.has_table:
                table_data = []
                for row in shape.table.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_data.append(row_data)
                slide_text.append({"table": table_data})

        slides_data.append({
            "slide_number": i,
            "content": slide_text
        })

    return {"slides": slides_data, "total_slides": len(prs.slides)}


def extract_text_from_docx(docx_path: Path) -> dict:
    """Extract paragraphs and tables from Word document."""
    doc = Document(str(docx_path))
    content = []

    for para in doc.paragraphs:
        if para.text.strip():
            content.append({"type": "paragraph", "text": para.text.strip()})

    for table in doc.tables:
        table_data = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            table_data.append(row_data)
        content.append({"type": "table", "data": table_data})

    return {"content": content, "total_items": len(content)}


def main():
    print(f"Extracting PowerPoint: {PPTX_FILE}")
    pptx_data = extract_text_from_pptx(PPTX_FILE)
    print(f"  Found {pptx_data['total_slides']} slides")

    print(f"\nExtracting Word document: {DOCX_FILE}")
    docx_data = extract_text_from_docx(DOCX_FILE)
    print(f"  Found {docx_data['total_items']} content blocks")

    result = {
        "powerpoint_competitor_comparison": pptx_data,
        "word_qa_document": docx_data
    }

    OUTPUT_JSON.parent.mkdir(exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nExtracted data saved to: {OUTPUT_JSON}")
    print(f"File size: {OUTPUT_JSON.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
