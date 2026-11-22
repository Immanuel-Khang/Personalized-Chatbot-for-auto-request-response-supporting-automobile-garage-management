"""[TRACK A - RAG] BM25 thuần Python (không thêm thư viện). Corpus nhỏ-vừa; lớn thì đổi sang full-text của Postgres."""
import math
from collections import Counter

from app.rag.text import content_words, tokenize


class BM25Index:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [tokenize(t) for t in texts]
        self.doc_sets = [set(d) for d in self.docs]
        self.tf = [Counter(d) for d in self.docs]
        self.lens = [len(d) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for s in self.doc_sets:
            df.update(s)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, n: int) -> list[tuple[int, float, int]]:
        """Trả [(vị trí chunk, điểm BM25, số từ khóa của câu hỏi có trong chunk)], điểm giảm dần."""
        q_tokens = tokenize(query)
        q_words = content_words(query)
        out = []
        for i, tf in enumerate(self.tf):
            score = 0.0
            for t in q_tokens:
                f = tf.get(t)
                if not f:
                    continue
                norm = f + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1))
                score += self.idf.get(t, 0.0) * f * (self.k1 + 1) / norm
            if score > 0:
                out.append((i, score, len(q_words & self.doc_sets[i])))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:n]
