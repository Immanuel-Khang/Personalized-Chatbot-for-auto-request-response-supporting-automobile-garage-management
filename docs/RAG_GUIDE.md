# Hướng dẫn sử dụng & nâng cấp Hybrid RAG

Áp dụng cho bản Hybrid RAG: BM25 (từ khóa) \+ Vector (nghĩa) → RRF → trả về `KnowledgeChunk`.

---

## 1\. Tổng quan nhanh

| Mục | Giá trị hiện tại |
| :---- | :---- |
| Kiểu retrieval | Hybrid  (BM25 \+ embedding \+ RRF) |
| Fallback | Thiếu API key / lỗi embed → tự chạy BM25-only |
| Contract | `KnowledgeService.retrieve(query, k) → list[KnowledgeChunk]` |
| Dữ liệu | `data/knowledge/*.md` (và `.json` FAQ) |
| Chọn mode | Biến môi trường `RETRIEVER` |
| Debug | `python -m app.rag.cli "..."` |
| Đánh giá | `python -m eval.run_rag_eval` (Recall@k, MRR, chuyển oan, trả bừa) |

&nbsp;

**Luồng 1 câu hỏi kiến thức:**

```
Câu hỏi khách
  → Knowledge Agent gọi retrieve(query, k=3)
  → HybridRetriever:
        BM25 top-N (lọc min_keyword_hits)
        Vector top-N (lọc min_vector_sim)
        RRF gộp 2 danh sách
  → list[KnowledgeChunk]  (rỗng → agent chuyển người)
  → LLM trả lời dựa trên chunk (prompt knowledge)
```

&nbsp;

> **Về `score`:** `KnowledgeChunk.score` là điểm RRF đã chuẩn hóa về 0..1, chỉ dùng để xếp hạng. Nó **không** phải độ tin cậy, đừng dùng nó để quyết định chuyển người. Việc "không tìm thấy → chuyển người" do ngưỡng bên trong retriever quyết định (mục 3 và 6.1).

&nbsp;

---

## 2\. Cấu trúc thư mục RAG

```
app/rag/
├── text.py           # Chuẩn hóa tiếng Việt (bỏ dấu, stopwords, bigram)
├── chunking.py       # Đọc .md / FAQ → Chunk (+ metadata section, kind)
├── bm25.py           # Chỉ mục BM25 thuần Python
├── embeddings.py     # OpenAI / Hash + cache file
├── vectorstore.py    # LocalVectorStore (numpy, RAM) — dùng khi RAG_VECTOR_STORE=pgvector  # pgvector (mặc định) | local
RAG_LEXICAL=python         # python (BM25 RAM) | pg (full-text Postgres) / DB không sẵn sàng
├── pgstore.py        # PgVectorStore (pgvector + HNSW, đồng bộ tăng dần) + PgFtsIndex (full-text Postgres)
├── fusion.py         # Reciprocal Rank Fusion (RRF)
├── hybrid.py         # HybridRetriever (retrieve, debug, reload)
├── factory.py        # build_retriever() theo .env
├── retriever.py      # KeywordRetriever cũ (baseline so sánh)
└── cli.py            # Công cụ dòng lệnh: reindex + debug điểm

data/knowledge/
├── maintenance.md    # Kiến thức bảo dưỡng
└── faq.md            # Câu hỏi–đáp (tiêu đề = câu hỏi). Hiện là FAQ MẪU, lặp lại maintenance.md; thay bằng FAQ thật

data/.rag_cache/      # Cache embedding (runtime, không commit)

eval/
├── rag_cases.json    # Bộ câu hỏi đánh giá retrieval
└── run_rag_eval.py   # Đo Recall@k / MRR / chuyển oan / trả bừa
```

&nbsp;

Service bọc:

```
app/services/knowledge.py  →  RagKnowledgeService
  - retrieve(query, k)
  - reload()   # đọc lại knowledge không cần restart server
```

&nbsp;

---

## 3\. Cấu hình (.env)

Copy từ `.env.example` rồi chỉnh:

```sh
# Chọn retriever
RETRIEVER=hybrid          # hybrid | bm25 | vector | keyword

# Embedding
EMBEDDING_PROVIDER=openai # openai | hash | none
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...

# Vector store
RAG_VECTOR_STORE=local
RAG_CACHE_DIR=./data/.rag_cache

# Tham số hybrid
RAG_CANDIDATES=10         # top-N mỗi nhánh trước khi RRF
RAG_RRF_K=60              # hằng số RRF
RAG_WEIGHT_BM25=1.0
RAG_WEIGHT_VECTOR=1.0

# Ngưỡng “không tìm thấy → chuyển người”
RAG_MIN_KEYWORD_HITS=2    # số từ khóa khớp tối thiểu (sau bỏ stopwords)
RAG_MIN_VECTOR_SIM=0.35   # cosine tối thiểu
```

&nbsp;

> **Hai ngưỡng này là giá trị khởi đầu, chưa được kiểm chứng.** Một đoạn lọt qua nếu khớp đủ từ khóa **hoặc** đạt cosine tối thiểu. Phải chỉnh bằng bộ eval (mục 10\) khi có dữ liệu thật. Câu hỏi chỉ có 1 từ khóa (vd "lốp") được tự hạ ngưỡng từ khóa xuống 1\.

&nbsp;

### Khi nào dùng mode nào?

| `RETRIEVER` | Khi nào dùng |
| :---- | :---- |
| `hybrid` | Mặc định – cân bằng từ khóa \+ nghĩa |
| `bm25` | Offline / không muốn gọi embedding |
| `vector` | Thử nghiệm chỉ semantic |
| `keyword` | Baseline cũ để so sánh eval |

&nbsp;

`EMBEDDING_PROVIDER=hash`: embedding giả (không hiểu nghĩa), chỉ để test pipeline khi không có API key.

---

## 4\. Chuẩn bị & cập nhật tài liệu knowledge

### 4.1 Định dạng file

**Markdown thường (`*.md`):**

```markdown
# Tiêu đề mục (giữ lại làm section)

Đoạn văn bản trọn một ý.

Đoạn ý tiếp theo (cách bằng dòng trống).
```

&nbsp;

- Cắt theo `#` heading → trong mỗi mục cắt theo đoạn (`\n\n`).  
- Đoạn quá ngắn được gộp; đoạn quá dài cắt theo câu.  
- `index_text` \= `section + text` → BM25/embedding hiểu ngữ cảnh tiêu đề.

**FAQ (`faq*.md`):**

```markdown
# FAQ khách hàng

## Bao lâu thì nên thay dầu động cơ?
Khoảng mỗi 5.000 km hoặc 6 tháng...

## Đèn báo áp suất dầu sáng thì phải làm sao?
Dừng xe ở nơi an toàn...
```

&nbsp;

- Mỗi `## câu hỏi` \= **1 chunk** dạng `Hỏi: ...\nĐáp: ...`.

**JSON FAQ (tùy chọn):**

```json
[
  {"question": "...", "answer": "...", "tags": ["maintenance"]}
]
```

&nbsp;

### 4.2 Quy tắc viết knowledge

1. **Một đoạn \= một ý hoàn chỉnh** (ví dụ: “khi nào thay bugi”, không trộn với ắc quy).  
2. **Tránh trùng** giữa `maintenance.md` và `faq.md` (FAQ ngắn, chi tiết để một nơi). Hiện `faq.md` mẫu đang lặp lại `maintenance.md` nên top-3 có thể chứa 2 đoạn cùng ý; khi có FAQ thật thì hết.  
3. **Thông số / giá xe** lấy từ SQL (`catalog`), không nhét số liệu cứng vào knowledge nếu có thể lệch.  
4. Ưu tiên tiếng Việt tự nhiên; hệ thống đã xử lý không dấu phía retrieval.  
5. Câu an toàn (đèn dầu, phanh…) nên rõ ràng, có hướng dẫn hành động.

### 4.3 Nạp lại sau khi sửa file

Có hai trường hợp khác nhau:

| Cách | Tác dụng | Server đang chạy có thấy tài liệu mới? |
| :---- | :---- | :---- |
| `python -m app.rag.cli reindex` | Chạy ở **tiến trình riêng**: đọc lại file, embed đoạn mới/đổi và lưu vào cache, in số chunk | **Không.** Cần restart server (lần khởi động sau dùng cache nên rất nhanh) |
| `RagKnowledgeService.reload()` | Dựng lại chỉ mục **trong tiến trình đang chạy** | **Có**, không cần restart |

&nbsp;

Hiện **chưa có endpoint nào gọi `reload()`** (vd `POST /admin/knowledge/reload`). Muốn cập nhật không restart thì cần thêm endpoint này (`app/api/admin.py`, thuộc track C); nếu chưa thì restart server sau khi sửa tài liệu.

Đoạn không đổi dùng lại vector trong cache, chỉ đoạn mới/đổi tốn tiền embed.

---

## 5\. Sử dụng hàng ngày

### 5.1 Debug một câu hỏi

```sh
python -m app.rag.cli "đèn áp suất dầu"
python -m app.rag.cli "phanh keu rit"
python -m app.rag.cli "bao lau thay dau Vios"
```

&nbsp;

Output mẫu:

```
mode=hybrid
-- BM25 (nhãn, điểm, số từ khóa khớp)
   ('faq.md#Đèn báo...', 4.21, 3)
-- Vector (nhãn, cosine)
   ('faq.md#Đèn báo...', 0.72)
-- Kết quả cuối (sau ngưỡng + RRF)
  [0.91] faq.md#Đèn báo...: 'Hỏi: Đèn báo áp suất dầu...'
```

&nbsp;

- Không có dòng “Kết quả cuối” / `(rỗng)` → bot sẽ **chuyển người** (`COMPLEXITY_HIGH: no RAG match`).  
- Dùng output này để chỉnh `RAG_MIN_KEYWORD_HITS` và `RAG_MIN_VECTOR_SIM`.

### 5.2 So sánh nhanh keyword vs hybrid

```sh
# Terminal 1
RETRIEVER=keyword python -m app.rag.cli "xe bi i khi tang toc"

# Terminal 2
RETRIEVER=hybrid python -m app.rag.cli "xe bi i khi tang toc"
```

&nbsp;

### 5.3 Chạy app

```sh
pip install -r requirements.txt   # đã gồm numpy
cp .env.example .env   # điền OPENAI_API_KEY nếu dùng embedding openai
uvicorn app.main:app --reload
# Mở http://localhost:8000
```

&nbsp;

---

## 6\. Cách tune

### 6.1 Ngưỡng chuyển người

| Hiện tượng | Hướng chỉnh |
| :---- | :---- |
| Chuyển người quá nhiều (miss) | Giảm `RAG_MIN_KEYWORD_HITS` (2 → 1\) hoặc giảm `RAG_MIN_VECTOR_SIM` (ví dụ 0.35 → 0.28; **con số chỉ là ví dụ**, hãy đo bằng eval) |
| Trả lời bừa / chunk kém liên quan | Tăng `RAG_MIN_VECTOR_SIM` hoặc `RAG_MIN_KEYWORD_HITS` |
| Câu ngoài phạm vi vẫn được trả lời (**trả bừa**) | Tăng `RAG_MIN_KEYWORD_HITS` lên 3; thêm từ chung ("thế", "hôm"...) vào `STOPWORDS` trong `text.py`; thêm câu ngoài phạm vi vào bộ eval để kiểm lại. Lưu ý "xe" và "suất" (trong "lãi suất") khớp tình cờ với "áp suất", "xe" trong tài liệu |
| Câu không dấu vẫn miss | Kiểm tra `text.py` (normalize); thử CLI với câu không dấu |

&nbsp;

### 6.2 Trọng số RRF

```sh
RAG_WEIGHT_BM25=1.2
RAG_WEIGHT_VECTOR=1.0   # ưu tiên từ khóa hơn một chút (tên xe, số km)
```

&nbsp;

hoặc ngược lại nếu câu diễn đạt tự do nhiều hơn.

### 6.3 Quy trình tune khuyến nghị

1. Mở rộng `eval/rag_cases.json` với **30–50 câu hỏi thật của khách** (hoặc do người ngoài nhóm viết). Câu tự viết từ chính tài liệu cho kết quả quá đẹp.  
2. Chia nhóm: có dấu / không dấu, diễn đạt lại, tên xe, mốc km, an toàn, ngoài phạm vi.  
3. Chạy `python -m eval.run_rag_eval -v` đo Recall@3, MRR, chuyển oan, trả bừa.  
4. Chỉ đổi **một** tham số mỗi lần, ghi lại kết quả.  
5. Chốt bộ tham số vào `.env` / README.

---

## 7\. Nâng cấp theo giai đoạn

### P0 – Đang có (giữ ổn định)

- [x] Hybrid BM25 \+ vector \+ RRF  
- [x] Fallback BM25-only  
- [x] Chuẩn hóa tiếng Việt (không dấu, stopwords, bigram)  
- [x] Chunk theo heading \+ FAQ  
- [x] Cache embedding  
- [x] pgvector (HNSW) + đồng bộ tăng dần + full-text Postgres tùy chọn (mục 9)  
- [x] CLI debug \+ reload  
- [x] Config qua `.env`

### P1 – Nên làm tiếp (ít đụng contract)

| Việc | Gợi ý kỹ thuật | Lợi ích |
| :---- | :---- | :---- |
| Mở rộng bộ câu hỏi thật | Đã có khung `eval/rag_cases.json` (26 câu mẫu) \+ `run_rag_eval.py`; cần thay/bổ sung câu thật | Biết hybrid thật sự hơn keyword bao nhiêu |
| Metadata filter (model xe, topic) | Mở rộng `Chunk` \+ `KnowledgeChunk` optional; filter trước/sau RRF | Tránh trả số liệu xe khác |
| Query rewrite có ngữ cảnh | Dùng `slots.car_model`, `current_mileage_km` trước khi retrieve | Câu “còn xe kia thì sao” rõ hơn |
| Gỡ trùng knowledge | FAQ ngắn, chi tiết một nguồn; bỏ thông số xe khỏi md nếu đã có SQL | Điểm RRF sạch hơn |
| Embedding local (tùy chọn) | Thêm class `LocalEmbedder` (BGE-M3 / multilingual-e5) trong `embeddings.py` | Không phụ thuộc API, dữ liệu không ra ngoài |

&nbsp;

**Lưu ý contract:**  
Thêm field optional vào `KnowledgeChunk` (ví dụ `section`, `topic`) thường cần cả nhóm duyệt `app/contracts/schemas.py`. Có thể trả metadata trong `source` (đã là `file#section`) trước khi mở schema.

### P2 – Khi corpus lớn / nhu cầu rõ

| Việc | Khi nào cần |
| :---- | :---- |
| Cross-encoder rerank | Đo hybrid vẫn precision thấp trên top-k |
| GraphRAG | Nhiều câu hỏi quan hệ hệ thống / multi-hop thật sự |
| Agentic retrieval (nhiều vòng) | Câu hỏi phức tạp, cần tự quyết định retrieve thêm |
| Đa phương thức (hình / sơ đồ) | Khách hỏi “cầu chì nào”, “sơ đồ hộp cầu chì” |

&nbsp;

Mọi nâng cấp **giữ nguyên** chữ ký:

```py
def retrieve(self, query: str, k: int = 3) -> list[KnowledgeChunk]:
```

&nbsp;

Chỉ thay bên trong `HybridRetriever` / `factory` / thêm backend mới đăng ký ở `factory.py`.

---

## 8\. Hướng dẫn nâng cấp cụ thể (checklist)

### 8.1 Thêm file knowledge mới

1. Thêm `data/knowledge/ten_chu_de.md` (đúng format mục 4).  
2. `python -m app.rag.cli reindex`  
3. `python -m app.rag.cli "câu hỏi liên quan"` kiểm tra chunk có lên không.

### 8.2 Đổi embedding model

1. Đổi `EMBEDDING_MODEL` trong `.env`.  
2. Restart server hoặc gọi `reload()`. Không cần xóa cache: cache khóa theo tên model nên vector cũ không bao giờ bị dùng lẫn; file cache cũ chỉ nằm lại, xóa tay trong `data/.rag_cache/` nếu muốn dọn.  
3. Chạy lại `python -m eval.run_rag_eval`: **ngưỡng cosine phụ thuộc model**, đổi model là phải chỉnh lại `RAG_MIN_VECTOR_SIM`.

### 8.3 Thêm Embedder local

1. Viết class trong `embeddings.py` (đủ `name`, `embed_documents`, `embed_query`).  
2. Đăng ký trong `factory.build_embedder()` theo `EMBEDDING_PROVIDER=local` (hoặc tên bạn chọn).  
3. Cài dependency (ví dụ `sentence-transformers`).  
4. Reindex.

### 8.4 Chuyển vector store sang pgvector — ĐÃ LÀM, xem mục 9

1. Viết `PgVectorStore` cùng interface: `replace_all(ids, vectors)`, `search(vector, n)`.  
2. Đăng ký khi `RAG_VECTOR_STORE=pgvector`.  
3. (Tùy chọn) Chuyển BM25 sang Postgres full-text → hybrid thuần SQL.

### 8.5 Thêm reranker (P1/P2)

1. Sau RRF, lấy top-M (ví dụ 10\) → cross-encoder chấm lại → lấy top-k.  
2. Giữ ngưỡng / fallback như hiện tại.  
3. Đo latency \+ Recall trước khi bật mặc định.

---

## 9\. Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
| :---- | :---- | :---- |
| Luôn BM25-only | Thiếu `OPENAI_API_KEY` hoặc `EMBEDDING_PROVIDER=none` | Kiểm tra `.env`, log khi start |
| Reindex chậm | Gọi OpenAI mỗi lần, cache miss | Giữ `data/.rag_cache`; chỉ sửa đoạn đổi |
| Câu không dấu không ra | Normalize chưa chạy / stopwords cắt hết | CLI debug; xem `content_words(query)` |
| Chuyển người oan | Ngưỡng cao / knowledge thiếu | Hạ ngưỡng; bổ sung đoạn |
| Trả chunk trùng | Cùng nội dung ở md \+ faq | Gỡ trùng file knowledge |
| Điểm vector thấp bất thường | Dùng `EMBEDDING_PROVIDER=hash` (không hiểu nghĩa) hoặc ngưỡng chưa hợp model | Kiểm tra `.env`; chạy CLI debug xem cosine thực tế |
| Sửa file xong server vẫn trả nội dung cũ | `cli reindex` không tác động tiến trình server đang chạy | Restart server hoặc gọi `reload()` (mục 4.3) |
| Trả lời câu ngoài phạm vi (vd lãi suất vay) | Ngưỡng từ khóa yếu, từ chung khớp tình cờ | Xem mục 6.1, dòng "trả bừa" |

&nbsp;

---

## 10\. Eval retrieval

`eval/run_eval.py` chấm **đường đi của agent** (node đi qua). `eval/run_rag_eval.py` chấm **chất lượng tìm đoạn tài liệu**; hai cái độc lập nhau.

```sh
python -m eval.run_rag_eval                          # so keyword / bm25 / hybrid
python -m eval.run_rag_eval --configs keyword,bm25   # không cần API key
python -m eval.run_rag_eval -v                       # in từng câu trượt / trả bừa
python -m eval.run_rag_eval --min-recall 0.8         # thoát mã 1 nếu Recall@3 thấp hơn (dùng cho CI)
```

&nbsp;

`hybrid` và `vector` cần `OPENAI_API_KEY`; thiếu key thì bị bỏ qua.

**Định dạng `eval/rag_cases.json`:**

```json
[
  {"id": "oil_interval", "cat": "co_dau",
   "query": "Bao lâu thì cần thay dầu động cơ",
   "gold_contains": ["5.000 km"]},
  {"id": "oos_bread", "cat": "ngoai_pham_vi",
   "query": "cách làm bánh mì", "expect_empty": true}
]
```

&nbsp;

- `gold_contains`: chunk được coi là **đúng** nếu chứa **tất cả** các chuỗi này (không phân biệt hoa thường và dấu). Dùng chuỗi con thay vì tên file/chunk nên bộ câu không vỡ khi cắt lại chunk hoặc sửa file.  
- `expect_empty: true`: câu **ngoài phạm vi**, `retrieve` phải trả rỗng để bot chuyển người.  
- `cat`: nhóm câu hỏi, để xem điểm theo nhóm.

**Các chỉ số:**

| Chỉ số | Ý nghĩa |
| :---- | :---- |
| Recall@k | Tỷ lệ câu có chunk đúng nằm trong top-k |
| MRR@5 | Trung bình 1/hạng của chunk đúng đầu tiên (hạng 1 \= 1, hạng 2 \= 0.5, không có \= 0\) |
| Chuyển oan | Câu trong phạm vi nhưng retrieve rỗng: bot chuyển người dù có đáp án (thấp là tốt) |
| Trả bừa | Câu ngoài phạm vi nhưng retrieve vẫn trả chunk: bot trả lời lẽ ra phải chuyển người (thấp là tốt) |

&nbsp;

**Kết quả mẫu (keyword vs bm25; 22 câu trong phạm vi \+ 4 ngoài phạm vi):** Recall@3 0.82 → 1.00, MRR 0.81 → 0.98, nhóm câu không dấu 0.00 → 1.00, nhưng **trả bừa tăng 0.25 → 0.50**. Đây là số trên bộ câu nhỏ tự viết, chỉ để minh họa, chưa phải kết luận về chất lượng thật. Chưa có số cho `hybrid`/`vector` (cần API key).

---

## 11\. Những gì nên bổ sung thêm (đề xuất ưu tiên)

Ngoài phần đã có trong code, nên có thêm:

1. **`docs/RAG_GUIDE.md`** (file này) – để cả nhóm biết dùng / nâng cấp.  
2. **Bộ câu hỏi** (`eval/rag_cases.json`) \+ script đo Recall: **đã có khung**, còn thiếu câu hỏi thật.  
3. **Quy ước knowledge** ngắn trong README hoặc `data/knowledge/README.md`&nbsp;  
4. **Log retrieval** (query, mode, top chunk id/score) đưa vào `trace` hoặc Langfuse/LangSmith (P1).  
5. **Metadata tối thiểu** trên chunk (`section` đã có trong label; cân nhắc `topic`) khi sửa contracts.  
6. **Test unit**: đã có trong `tests/test_rag.py` (chuẩn hóa, chunking, BM25, RRF, hybrid, fallback, cache, reload).  
7. **Endpoint admin gọi `reload()`** để cập nhật tài liệu không cần restart (mục 4.3).

---

*Tài liệu tương ứng code Hybrid RAG (BM25 \+ OpenAI/hash embedding \+ RRF). Cập nhật khi đổi contract hoặc thêm backend mới.*

---

## 9\. Chạy trên Postgres (pgvector)

Mặc định `RAG_VECTOR_STORE=pgvector`: chunk, vector và tsvector nằm trong bảng `rag_chunks` (migration `a1c3e5f7b9d2`,
tự chạy khi app khởi động hoặc `alembic upgrade head`). Cần image có pgvector (`pgvector/pgvector:pg17`, đã dùng trong compose).

| | `local` (cũ) | `pgvector` (mới) |
| :---- | :---- | :---- |
| Nơi lưu vector | RAM + file `data/.rag_cache/*.json` | Cột `embedding vector(1536)` + chỉ mục HNSW |
| Khởi động / thêm worker | Nạp lại cache file, mỗi worker tự dựng | `sync()` chỉ so hash, **không embed lại** nếu không đổi |
| Tìm vector | numpy quét toàn bộ trong RAM mỗi worker | SQL `ORDER BY embedding <=> q` (HNSW) |
| Đổi model embedding | Cache khóa theo model | Cột `embedding_model`; đổi model → tự embed lại, search chỉ xét vector cùng model |
| DB lỗi / chưa migrate | — | Tự hạ về `local`, có cảnh báo trong log |

**Nguyên tắc:** `data/knowledge/` vẫn là nguồn sự thật; `rag_chunks` chỉ là chỉ mục, xóa đi dựng lại được.
`id` chunk = hash nội dung nên `sync()` upsert chunk mới/đổi, xóa chunk không còn trong file, chỉ gọi API embed cho chunk thiếu vector.
Ghi `sync()` có advisory lock nên nhiều worker khởi động cùng lúc không đạp nhau (có thể cùng embed một lần, chỉ tốn tiền, không sai dữ liệu).

**Nhánh từ khóa:** `RAG_LEXICAL=python` (mặc định) giữ BM25 trong RAM có bigram. `RAG_LEXICAL=pg` dùng full-text
Postgres (`tsvector` trên token đã bỏ dấu, xếp hạng `ts_rank_cd`, chưa có IDF/bigram); ngưỡng `RAG_MIN_KEYWORD_HITS` vẫn tính
bằng cùng hàm `content_words`. Trên bộ eval hiện tại `pg` thấp hơn một chút (Recall@1 0.91 so với 0.95) nên chưa là mặc định;
đổi sau khi đo trên câu hỏi thật.

**So sánh bằng eval:**

```sh
python -m eval.run_rag_eval --configs bm25,hybrid,hybrid_pg,hybrid_pgfts,bm25_pgfts
```

**Giới hạn cần biết**
- Cột vector cố định 1536 chiều (`text-embedding-3-small`). Model khác chiều (BGE-M3 1024...) cần migration đổi cột + chỉ mục; sync sẽ báo lỗi chiều và retriever tự chạy BM25-only.
- `python -m app.rag.cli reindex` cập nhật DB ngay, nhưng server đang chạy vẫn giữ danh sách chunk + BM25 trong RAM từ lúc khởi động: nhánh vector thấy dữ liệu mới, nhánh BM25 thì cần `reload()` hoặc restart. Endpoint `POST /admin/knowledge/reload` vẫn chưa có (track C).
- HNSW là tìm kiếm gần đúng; với vài trăm chunk không khác gì quét đủ, nhưng không nên coi là chính xác tuyệt đối khi corpus lớn.
- Test dùng DB `*_test` đã có sẵn extension; `CREATE EXTENSION vector` cần quyền superuser (hoặc extension đã được cài sẵn) trên môi trường thật.
