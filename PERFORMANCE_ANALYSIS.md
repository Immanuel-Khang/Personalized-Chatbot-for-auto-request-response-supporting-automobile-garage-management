# Performance Analysis - Response Time 8-9s

## Tổng quan kiến trúc

Request flow qua các node theo thứ tự:
```
input → preprocess → policy_guard → router → specialist(s) → validation → compose → output_guard → respond
```

## Các nguyên nhân tiềm năng gây chậm

### 1. **LLM Calls - NGUYÊN NHÂN CHÍNH**

#### 1.1 Preprocess Node
- **File**: `app/agent/nodes/preprocess.py`
- **LLM call**: `generate_structured()` với schema `ConversationInterpretation`
- **Ước tính**: 2-3s
- **Mô tả**: Phân tích intent, task, slot extraction, reference resolution
- **Prompt rất dài**: Bao gồm CANONICAL_SLOTS, INTERPRETATION_PROMPT, conversation history, shown_products, pending_interactions
- **Structured output**: Pydantic model phức tạp với nhiều nested fields

#### 1.2 Specialist Nodes (Sales, Conversation, Knowledge...)
- **Sales** (`app/agent/nodes/sales.py`):
  - `generate_with_tools()` với tool calling
  - LLM có thể gọi tools nhiều lần (max_turns=5)
  - Mỗi tool call = 1 round-trip LLM
  - **Ước tính**: 2-4s (tùy số lượng tool calls)

- **Conversation** (`app/agent/nodes/conversation.py`):
  - `generate()` cho greeting, clarify, out-of-scope
  - **Ước tính**: 1-2s

- **Knowledge** (`app/agent/nodes/knowledge.py`):
  - RAG retrieval + `generate()`
  - **Ước tính**: 1.5-2.5s

#### 1.3 Compose Node
- **File**: `app/agent/nodes/compose.py`
- **LLM call**: `generate_with_tools()` với submit_reply tool
- **Ước tính**: 1-2s
- **Note**: Có optimization để skip LLM khi single clean answer (line 101-103)
  ```python
  if not _needs_composition(texts):
      return sources  # Skip LLM
  ```

### 2. **Database Operations**

#### 2.1 Checkpoint Save/Load (LangGraph)
- **File**: `app/agent/graph.py:80-89`
- **Backend**: PostgreSQL với `PostgresSaver`
- **Operations**:
  - Load checkpoint at start của turn
  - Save checkpoint sau mỗi node update
- **Ước tính**: 50-200ms mỗi checkpoint operation
- **Số lượng**: ~5-10 checkpoint operations per turn

#### 2.2 Database Queries trong các node
- **input.py**: Load customer profile, chat history
- **conversation.py**: `_get_customer_profile()` query DB
- **respond.py**: Save message to DB
- **Ước tính tổng**: 100-300ms

### 3. **RAG/Vector Search**

#### 3.1 Knowledge Retrieval
- **File**: `app/rag/` và `app/services/knowledge.py`
- **Operations**: 
  - Embedding generation cho query
  - Vector similarity search
  - Retrieval từ knowledge base
- **Ước tính**: 200-500ms (tùy embedding model và vector DB)

### 4. **Sequential Processing**

- **Vấn đề**: Các node chạy **tuần tự** theo graph
- **Không có parallel**: Chỉ coordinator có thể spawn parallel specialists
- **Impact**: Latency cộng dồn
  - preprocess (2-3s) → router (0s) → specialist (2-4s) → validation (0s) → compose (1-2s)
  - **Tổng ước tính**: 5-9s

### 5. **Network Latency đến OpenAI API**

- **File**: `app/agent/llm.py:34-38`
- **Model**: `settings.openai_model` (có thể là gpt-4, gpt-3.5-turbo...)
- **Latency factors**:
  - RTT đến OpenAI servers
  - Model inference time
  - Streaming vs non-streaming
- **Ước tính**: Đã bao gồm trong LLM call time ở trên

## Breakdown Timeline Ước Tính

Cho một request thông thường (sales + conversation):

| Node | Operation | Time (s) |
|------|-----------|----------|
| input | Load context, DB queries | 0.1-0.2 |
| preprocess | LLM structured output | 2-3 |
| policy_guard | Rule-based check | 0.01 |
| router | Dispatch logic | 0.01 |
| specialist (sales) | LLM with tools (2-3 calls) | 3-4 |
| validation | Rule validation | 0.05 |
| compose | LLM composition | 1-2 |
| output_guard | Safety check | 0.05 |
| respond | Save to DB | 0.1 |
| **Checkpoint ops** | 5-10 save/load | 0.3-0.5 |
| **TOTAL** | | **6.6-10.0s** |

## Ranking Bottlenecks (Theo Impact)

1. 🔴 **CRITICAL: Preprocess LLM** (2-3s, 30-35% total time)
2. 🔴 **CRITICAL: Specialist LLM with tools** (2-4s, 30-40% total time)
3. 🟡 **HIGH: Compose LLM** (1-2s, 12-20% total time)
4. 🟡 **MEDIUM: Checkpoint operations** (0.3-0.5s, 5% total time)
5. 🟢 **LOW: RAG retrieval** (0.2-0.5s, 3-5% total time)
6. 🟢 **LOW: DB queries** (0.1-0.3s, 2-3% total time)

## Các Điểm Cần Kiểm Tra Sâu

### A. Preprocess Prompt Size
- Check length của prompt trong preprocess
- History có thể rất dài nếu conversation kéo dài
- shown_products list có thể lớn
- **Action**: Log prompt token count

### B. Sales Tool Calls
- Số lượng tool calls trung bình
- Có bị loop không cần thiết không
- **Action**: Log tool call history

### C. Compose LLM Usage
- Kiểm tra bao nhiêu % requests skip LLM (single clean answer)
- **Action**: Check metrics từ `test_timing.py`

### D. Checkpoint Frequency
- LangGraph checkpoint sau mỗi node
- Có thể giảm frequency không?
- **Action**: Profile checkpoint operations

### E. Model Speed
- OpenAI model nào đang dùng? (gpt-4 chậm hơn gpt-3.5-turbo)
- Temperature setting
- **Action**: Check `settings.openai_model`

## Gợi Ý Optimization (Không implement, chỉ note)

1. **Prompt caching**: OpenAI prompt caching cho system prompts
2. **Streaming**: Stream response thay vì chờ full completion
3. **Parallel specialists**: Nếu có nhiều specialist, chạy song song
4. **Faster model cho preprocess**: Dùng gpt-3.5-turbo cho preprocess, gpt-4 cho specialist
5. **Skip compose khi có thể**: Expand logic `_needs_composition()`
6. **Checkpoint optimization**: Giảm frequency hoặc async checkpoint
7. **RAG caching**: Cache frequent queries
8. **Reduce history**: Limit conversation history length trong prompts

## Kết Luận

**Nguyên nhân chính gây response time 8-9s:**

1. **3 LLM calls tuần tự** chiếm 80-85% total time:
   - Preprocess: 2-3s
   - Specialist: 2-4s
   - Compose: 1-2s

2. **Sequential processing** không có optimization parallel

3. **Model latency** từ OpenAI API

Đây là **bình thường** cho một multi-agent architecture với structured output và tool calling. Để giảm xuống 3-5s cần architectural changes lớn.
