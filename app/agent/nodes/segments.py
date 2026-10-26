"""[TRACK B] Tách tin nhắn nhiều ý thành các vế, để mỗi agent chỉ đọc phần câu của mình.
Chỉ đọc, không side effect. Vế không có intent nào gộp vào vế bên cạnh, giữ nguyên văn bản gốc."""
import re
from typing import Callable

# Không tách dấu chấm giữa hai chữ số (1.5 tỷ, 850.000.000 đồng)
SPLIT = re.compile(r"[,;?!\n]+|\.(?!\d)|\s+(?:và|còn|rồi|với lại|thêm nữa|ngoài ra)\s+", re.IGNORECASE)


def split_clauses(text: str, classify: Callable[[str], set[str]]) -> list[tuple[str, set[str]]]:
    """Trả [(vế, intents)]. 'So sánh Vios và Camry' giữ nguyên vì vế 'Camry' không có intent."""
    spans, pos = [], 0
    for m in SPLIT.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))

    clauses: list[list] = []  # [start, end, intents]
    for start, end in spans:
        part = text[start:end].strip()
        if not part:
            continue
        found = set(classify(part))
        if clauses and (not found or not clauses[-1][2]):
            clauses[-1][1] = end
            clauses[-1][2] |= found
        else:
            clauses.append([start, end, found])
    return [(text[s:e].strip(), intents) for s, e, intents in clauses]
