# Product RAG: catalog search, latency, and next steps

This document describes the product search used when a customer describes a vehicle need in natural language, for example: “a durable truck for long trips, around five tons, mid-range price.” This is a separate path from knowledge RAG: it searches product catalog summaries and returns catalog `Product` records.

## Current path

```text
customer message
  -> preprocess identifies a SALES search task
  -> Sales LLM chooses search_by_description and writes a search query
  -> product service searches the product summaries
       BM25 keyword branch + embedding/vector branch
       -> Reciprocal Rank Fusion combines the ranked results
       -> catalog query loads current product/price/spec fields
  -> Sales LLM reads the tool result and writes the customer-facing answer
```

### 1. Product index creation

At startup, `seed_catalog()` calls `ensure_product_index()`. The indexer takes active products with `sales_info.summary`, removes the “Không hợp: …” sentence from the searchable text, and creates one RAG chunk per product. A chunk's stable source name uses the product SKU (`product_<sku>.catalog`). The full `sales_info` remains in the catalog; only the index text is cleaned.

The chunks and their embeddings are stored in PostgreSQL's `rag_chunks` table. The configured embedding dimension is 1536, which corresponds to `text-embedding-3-small` in the current configuration. Index sync is incremental: unchanged chunks do not need to be embedded again.

### 2. Startup warm-up and process cache

After the index sync, the FastAPI startup hook calls `warm_rag_search()`. It loads each product chunk's `source` and `index_text` once, builds the Python BM25 index and its common-word set, constructs the embedder, and sends one short warm-up embedding query. The service keeps those objects in process memory for later searches.

This avoids re-reading all product chunks and rebuilding the BM25 index for every customer query. The warm-up embedding also initializes the embedding client/connection before the first customer search. It does **not** precompute vectors for every possible customer query: an unseen query still needs its own embedding.

The query-vector cache is process-local and keyed by the exact query text (up to 256 recent queries). Repeating the same query in the same app process can reuse its vector; a differently worded query will normally call the embedding provider again.

### 3. Keyword branch (BM25)

For each query, the service normalizes/tokenizes the text using the shared Vietnamese text utilities. It removes words common to more than 60% of product chunks from the keyword-hit check, so generic words such as “xe” or “tải” do not count as distinguishing evidence.

BM25 ranks the in-memory product summaries. The service considers at most `RAG_CANDIDATES` candidates (default 10) and keeps chunks meeting `RAG_MIN_KEYWORD_HITS` (default 2, lowered when the query has fewer content words).

### 4. Vector branch (semantic similarity)

The embedder turns the query into a vector. PostgreSQL compares it with product vectors using cosine distance (`embedding <=> query_vector`), considers the nearest configured candidates, and keeps results whose similarity reaches `RAG_MIN_VECTOR_SIM` (default 0.35). It filters by embedding model so vectors from different models are never compared together.

If embedding or vector search fails, the service logs the failure and continues with the BM25 results alone. This preserves keyword search when the vector branch is unavailable.

### 5. Fusion and catalog hydration

The service combines the BM25 and vector rankings with Reciprocal Rank Fusion (RRF), using the configured branch weights and `RAG_RRF_K`. It converts chunk sources back to stable product SKUs, then queries the live catalog for the matching active vehicles and their current approved MSRP and specifications. The RAG text ranks candidates; the catalog remains the source for current prices and specs. The normal result limit is three products.

### 6. Where the LLM fits

RAG retrieval does not write the final answer by itself. `Sales` uses `generate_with_tools()`: the LLM first chooses a product tool and its arguments, the application executes that tool, and the LLM then reads the returned products and writes its answer. The Sales node allows up to three tool-call rounds; if it uses all three, the helper can make one final LLM call without tools. A typical search therefore has an LLM round trip before retrieval and another after retrieval. Preprocessing has already made a separate LLM call earlier in the chat request.

That distinction matters for latency: the time recorded for the whole Sales node includes LLM tool selection, product retrieval, and answer generation. It is not a measurement of RAG alone.

## Why it can feel slow

There are several costs, but they are not equally large:

1. **LLM round trips dominate the chat response.** Preprocessing calls the LLM; the Sales tool loop can call it more than once. Network time and model generation are included in those node times.
2. **A new semantic query needs an embedding API call.** The exact-query cache helps repeated queries, but cannot answer a new phrasing without embedding it.
3. **A cold app process must load product chunks and build BM25.** The startup warm-up now moves this initialization out of the first customer search, at the cost of doing it during app startup.
4. **There are still database operations per search.** The vector branch queries pgvector and the service hydrates ranked SKUs from the current catalog. With the current small product index, these were not the main measured cost.

### Measurements from the local development database

These are individual live timings, not a controlled benchmark:

| Operation | Observed time |
| :--- | ---: |
| Cold first product search in a fresh process | 772 ms |
| Startup-style load + embedding warm-up | 1,135 ms |
| First unseen query after warm-up | 246 ms |
| Repeated identical query in the warmed process | 15 ms |
| Full natural-language product chat | 16.0–16.7 s |
| Nodes in one measured product chat | preprocessing 3.9 s; Sales 11.8 s |

The first four rows show that warm-up helps product retrieval. The final two show why the full chat can still be slow: the measured Sales duration includes LLM calls, and preprocessing also calls the LLM. The evidence does not support attributing the full 16 seconds to the product retriever.

## Development suggestions

Recommended order for future performance work:

1. **Measure each span separately.** Record durations for embedding API, BM25 ranking, pgvector SQL, RRF, catalog hydration, and each Sales LLM invocation. Also record Sales tool-call count and model usage. Keep message text and personal data out of performance logs. This will show whether a future slowdown is retrieval, an LLM round trip, or database latency.
2. **Reduce unnecessary Sales LLM rounds.** For an unambiguous `search_catalog` task already identified by preprocessing, consider executing the product search directly and using one LLM call to explain the results. Keep the current tool-selection path for ambiguous or multi-action requests. Compare behavior and response quality on real natural-language search examples before making this the default.
3. **Keep the in-memory index fresh after reindexing.** A running process keeps its BM25 data from startup. Running the product reindex command in a separate process updates PostgreSQL but does not refresh the server's in-memory BM25 cache. Add a supported reload/invalidation operation after a product index sync, and make it work across app workers. `DbProductService.invalidate_rag_search()` exists as a local building block but is not currently wired into an admin or indexing workflow.
4. **Evaluate query-cache normalization.** The current vector cache uses exact query text. A carefully chosen canonical key (for example trimming and collapsing whitespace) might improve cache hits for semantically identical repeated queries. Validate that it does not merge meaningfully different queries; do not normalize away model names, numbers, or payload values.
5. **Benchmark before changing retrieval settings or adding a reranker.** Build product-specific examples with expected relevant SKUs, including Vietnamese paraphrases, budgets, payloads, and long-distance use. Track Recall@k, ranking quality, and latency. Only add a cross-encoder or change thresholds if these measurements show a retrieval-quality problem that justifies the extra cost.

The warm-up is a latency trade: it adds work to application startup and spends one embedding request, but avoids making the first customer search pay for process-local index construction and embedding-client initialization. It does not remove per-query embedding for unseen text.

## Relevant implementation

- Sales tool definitions and execution: `app/agent/nodes/sales.py`
- Product retrieval, cache, fusion, and catalog hydration: `app/services/product.py`
- Product chunk extraction and database index sync: `app/rag/product_indexer.py`
- Embedder construction and query cache: `app/rag/factory.py`, `app/rag/embeddings.py`
- Startup warm-up: `app/main.py`
- Defaults (environment variables may override): `app/config.py`
