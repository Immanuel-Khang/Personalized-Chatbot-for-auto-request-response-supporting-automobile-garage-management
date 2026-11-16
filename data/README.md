# data/

- `catalog_master_refined.csv` — **CSV duy nhất** được seed (`app/db/seed.py`). Muốn đổi dữ liệu xe thì sửa file này.
- `knowledge/` — tài liệu cho Knowledge RAG.
- `sales_data_extracted.json` — dữ liệu trung gian của `map_sales_to_csv.py`.

Không thêm bản CSV khác vào repo; các bản cũ (`catalog_master.csv`, `catalog_master_with_sales*.csv`, `*_mota (3).csv`) đã bị xóa để mọi người dùng chung một dữ liệu.
