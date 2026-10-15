"""
[TRACK A - RAG] Chuẩn hóa tiếng Việt cho nhánh từ khóa (BM25).
- hạ chữ thường + bỏ dấu: khách gõ "phanh keu" vẫn khớp "phanh kêu"
- "40.000" -> "40000" để số km không bị tách đôi
- bỏ từ dừng (của, và, là...) — bản KeywordRetriever cũ tính chúng là từ trùng khớp
- thêm bigram (ghép 2 từ liền nhau) để bắt từ ghép tiếng Việt ("thay_dau", "ap_suat")
  mà không cần bộ tách từ. Nếu sau này dùng underthesea/pyvi thì chỉ sửa tokenize() ở đây.
"""
import re
import unicodedata

STOPWORDS = frozenset("""
cua va la thi ma co cho de nay do nhung cac mot nhu nao gi khong duoc bi o tai voi trong khi se da dang
ra vao len xuong hay hoac neu thoi roi cung nua van con rat qua lam
toi minh ban em anh chi xin vui long hoi biet bao nhieu
""".split())

_THOUSANDS = re.compile(r"(?<=\d)[.,](?=\d{3}(?!\d))")


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    return strip_accents(_THOUSANDS.sub("", text.lower()))


def tokenize(text: str, bigrams: bool = True) -> list[str]:
    """Trả list token đã bỏ từ dừng; bigram nối bằng '_'."""
    words = re.findall(r"\w+", normalize(text))
    out = [w for w in words if w not in STOPWORDS]
    if bigrams:
        out += [f"{a}_{b}" for a, b in zip(words, words[1:])
                if a not in STOPWORDS and b not in STOPWORDS]
    return out


def content_words(text: str) -> set[str]:
    """Chỉ unigram (không bigram) — dùng để đếm số từ khóa khớp khi lọc ngưỡng."""
    return set(tokenize(text, bigrams=False))
