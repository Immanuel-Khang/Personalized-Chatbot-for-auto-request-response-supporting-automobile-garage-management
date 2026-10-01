# === FILE: rag_pipeline.py ===
"""
RAG Pipeline: Ingestion and Retrieval for Excel FAQ sheets.
Reads all sheets (Phụ tùng, Bảo hành, Kỹ thuật...), embeds them,
and persists them into ChromaDB vector store.
"""
import os
from pathlib import Path
from typing import List, Optional
from pydantic import SecretStr
from dotenv import load_dotenv
import pandas as pd
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
CHROMA_PERSIST_DIR = os.getenv("CHROMA_DIR", "./chroma_faq_store")
EMBEDDING_MODEL_NAME = "text-embedding-3-small"
_vector_store: Optional[Chroma] = None

load_dotenv()

def get_embedding_model() -> OpenAIEmbeddings:
    api_key = os.getenv("OPEN_AI_KEY")
    
    if api_key is None:
        raise ValueError("OPENAI_API_KEY is not found!")

    return OpenAIEmbeddings(
        model=EMBEDDING_MODEL_NAME,
        api_key=SecretStr(api_key),
    )
    
def get_vector_store() -> Chroma:
    """
    Lazy-loaded singleton. Initializes or connects to the existing Chroma collection.
    """
    global _vector_store
    if _vector_store is None:
        _vector_store = Chroma(
            collection_name="foton_faq",
            embedding_function=get_embedding_model(),
            persist_directory=CHROMA_PERSIST_DIR
        )
    return _vector_store

# ─────────────────────────────────────────
# DETECT ACTION & SLOTS FROM ANSWER TEXT
# ─────────────────────────────────────────
def detect_action_type(answer_text: str) -> str:
    """
    Classifies whether the FAQ item requires data collection:
    - TEST_DRIVE_BOOKING: e.g. FAQ #7 (lái thử xe)
    - COLLECT_LEAD: e.g. FAQ #8 (nhận báo giá chi tiết)
    - NONE: normal static Q&A
    """
    text = answer_text.lower()
    if any(k in text for k in ["lái thử", "đăng ký lái thử", "test drive"]):
        return "TEST_DRIVE_BOOKING"
    if any(k in text for k in ["nhận báo giá", "báo giá chi tiết", "cung cấp một số thông tin như"]):
        return "COLLECT_LEAD"
    return "NONE"

def detect_required_slots(action_type: str) -> List[str]:
    if action_type == "COLLECT_LEAD":
        return ["customer_name", "customer_phone", "car_model", "customer_address"]
    if action_type == "TEST_DRIVE_BOOKING":
        return ["customer_name", "customer_phone", "car_model"]
    return []


# ─────────────────────────────────────────
# CORE INGESTION FUNCTION
# ─────────────────────────────────────────
def load_faq_from_excel(file_path: str) -> int:
    """
    Input: Path to your Excel file (.xlsx / .xls) containing one or multiple sheets.
    Output: Total number of FAQ items embedded into the Vector Store.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file Excel tại: {file_path}")
    print(f"\n📂 Bắt đầu nạp file FAQ: {path.name}")
    excel = pd.ExcelFile(path)
    all_docs: List[Document] = []
    for sheet_name in excel.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet_name)
        df.columns = [str(c).strip() for c in df.columns]
        # Tìm các cột câu hỏi và câu trả lời (linh hoạt tên cột: 'Câu hỏi', 'Question', 'Cau hoi', ...)
        q_col = next((c for c in df.columns if any(k in c.lower() for k in ["câu hỏi", "question", "hỏi"])), None)
        a_col = next((c for c in df.columns if any(k in c.lower() for k in ["câu trả lời", "answer", "trả lời"])), None)
        if not q_col or not a_col:
            print(f"⚠️ Bỏ qua sheet '{sheet_name}' (không tìm thấy cột 'Câu hỏi' hoặc 'Câu trả lời').")
            continue
        sheet_count = 0
        for idx, row in df.iterrows():
            question = str(row[q_col]).strip()
            answer = str(row[a_col]).strip()
            if not question or question.lower() == "nan":
                continue
            action_type = detect_action_type(answer)
            required_slots = detect_required_slots(action_type)
            doc = Document(
                page_content=f"Câu hỏi: {question}\nCâu trả lời: {answer}",
                metadata={
                    "question": question,
                    "answer": answer,
                    "category": sheet_name,
                    "action_type": action_type,
                    "required_slots": ",".join(required_slots),
                    "source_file": path.name,
                    "row_index": int(idx)
                }
            )
            all_docs.append(doc)
            sheet_count += 1
        print(f"  ✓ Sheet '{sheet_name}': Đọc được {sheet_count} câu hỏi.")
    if all_docs:
        print(f"\n⏳ Đang gọi OpenAI Embeddings để vector hoá {len(all_docs)} câu hỏi...")
        store = get_vector_store()
        store.add_documents(all_docs)
        print(f"🎉 Hoàn thành! Đã lưu thành công vào ChromaDB ({CHROMA_PERSIST_DIR}).")
    else:
        print("⚠️ Không có dữ liệu câu hỏi nào được nạp.")
    return len(all_docs)

# ─────────────────────────────────────────
# CLI EXECUTION (Nếu chạy trực tiếp file này)
# ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    # CÁCH 1: Nhận đường dẫn file từ dòng lệnh CLI: python rag_pipeline.py "path/to/file.xlsx"
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
    else:
        # CÁCH 2: Điền đường dẫn file Excel trực tiếp vào biến này:
        target_file = "D:/Personalized-Chatbot-for-auto-request-response-supporting-automobile-garage-management/QA.xlsx"
    print(f"Đường dẫn file: {target_file}")
    if os.path.exists(target_file):
        total = load_faq_from_excel(target_file)
        print(f"Tổng số FAQ đã nhúng: {total}")
    else:
        print(f"Vui lòng chỉnh sửa đường dẫn 'target_file' hoặc truyền qua CLI:")
        print(f"  python rag_pipeline.py \"duong/dan/toi/file_excel.xlsx\"")
