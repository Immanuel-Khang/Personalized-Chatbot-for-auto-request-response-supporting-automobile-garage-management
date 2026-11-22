"""
[TRACK A - RAG] Nạp tài liệu trong data/knowledge/ và cắt thành chunk (kèm metadata).

Định dạng hỗ trợ:
- *.md  : cắt theo tiêu đề (#...), trong mỗi mục cắt theo đoạn (dòng trống).
          Đoạn quá ngắn được gộp với đoạn kế, đoạn quá dài được cắt theo câu.
- faq*.md: mỗi tiêu đề là MỘT CÂU HỎI, nội dung bên dưới là câu trả lời (1 chunk / câu hỏi).
- *.json: danh sách [{"question": ..., "answer": ..., "tags": [...]}] (FAQ xuất từ nơi khác).

Thêm loại file mới (pdf, docx...): viết thêm 1 hàm trả list[Chunk] rồi đăng ký vào _LOADERS.
Chunk.id = hash nội dung -> biết đoạn nào đổi để chỉ embed lại đoạn đó.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MIN_CHARS = 60      # đoạn ngắn hơn thì gộp với đoạn sau
MAX_CHARS = 1200    # đoạn dài hơn thì cắt theo câu
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Chunk:
    text: str                      # nội dung trả cho agent
    source: str                    # tên file
    section: str = ""              # tiêu đề mục / câu hỏi FAQ
    kind: str = "doc"              # doc | faq
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def index_text(self) -> str:
        """Văn bản dùng để lập chỉ mục (BM25 + embedding): kèm tiêu đề mục cho đủ ngữ cảnh."""
        if self.kind == "faq":
            return self.text
        return f"{self.section}\n{self.text}" if self.section else self.text

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.source}|{self.index_text}".encode("utf-8")).hexdigest()[:16]

    @property
    def label(self) -> str:
        return f"{self.source}#{self.section}" if self.section else self.source


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + len(sent) > MAX_CHARS:
            parts.append(cur.strip())
            cur = ""
        cur += sent + " "
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _paragraphs(body: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    merged: list[str] = []
    carry = ""
    for p in paras:
        p = f"{carry}\n{p}".strip() if carry else p
        carry = ""
        if len(p) < MIN_CHARS:
            carry = p
        else:
            merged.extend(_split_long(p))
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]}\n{carry}"
        else:
            merged.append(carry)
    return merged


def _sections(md: str) -> list[tuple[str, str]]:
    """[(tiêu đề, nội dung)] — nội dung trước tiêu đề đầu tiên có tiêu đề rỗng."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in md.splitlines():
        m = _HEADING.match(line)
        if m:
            out.append((m.group(2), []))
        else:
            out[-1][1].append(line)
    return [(h, "\n".join(lines).strip()) for h, lines in out if "\n".join(lines).strip()]


def load_markdown(path: Path) -> list[Chunk]:
    md = path.read_text(encoding="utf-8")
    is_faq = path.name.lower().startswith("faq")
    chunks: list[Chunk] = []
    for heading, body in _sections(md):
        if is_faq and heading:
            chunks.append(Chunk(text=f"Hỏi: {heading}\nĐáp: {body}", source=path.name,
                                section=heading, kind="faq"))
        else:
            chunks.extend(Chunk(text=p, source=path.name, section=heading) for p in _paragraphs(body))
    return chunks


def load_faq_json(path: Path) -> list[Chunk]:
    chunks = []
    for row in json.loads(path.read_text(encoding="utf-8")):
        q, a = str(row["question"]).strip(), str(row["answer"]).strip()
        if q and a:
            chunks.append(Chunk(text=f"Hỏi: {q}\nĐáp: {a}", source=path.name, section=q, kind="faq",
                                tags=tuple(row.get("tags", []))))
    return chunks


_LOADERS = {".md": load_markdown, ".json": load_faq_json}


def load_chunks(directory: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for f in sorted(directory.iterdir()) if directory.exists() else []:
        loader = _LOADERS.get(f.suffix.lower())
        if loader and f.is_file():
            chunks.extend(loader(f))
    # bỏ trùng nội dung (cùng id) nhưng giữ thứ tự
    seen, unique = set(), []
    for c in chunks:
        if c.id not in seen:
            seen.add(c.id)
            unique.append(c)
    return unique
