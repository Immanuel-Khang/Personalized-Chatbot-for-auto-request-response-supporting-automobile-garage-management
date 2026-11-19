"""Đợt 3: đo token/tiền khi gọi LLM thật và dừng khi chạm trần ngân sách.

Chỉ hoạt động khi RUN_LLM_TESTS=1 (xem conftest.py). Mặc định gpt-6-luna: $0.10 input, $0.01 input cache, $0.50 output
mỗi 1M token (đổi bằng L5_PRICE_IN / L5_PRICE_CACHED / L5_PRICE_OUT nếu bảng giá đổi). Token reasoning đã nằm trong
output_tokens theo cách OpenAI tính tiền, nên cost ở đây là ước lượng sát; vẫn đối chiếu với dashboard OpenAI.

  L5_BUDGET_USD=1.50        trần chi cho MỘT lần chạy pytest (mặc định 1.50)
  L5_REASONING_EFFORT=low   none|low|medium|high (mặc định low)
"""
import os
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler

PRICE_IN = float(os.getenv("L5_PRICE_IN", "0.10"))
PRICE_CACHED = float(os.getenv("L5_PRICE_CACHED", "0.01"))
PRICE_OUT = float(os.getenv("L5_PRICE_OUT", "0.50"))
BUDGET_USD = float(os.getenv("L5_BUDGET_USD", "1.50"))


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0       # đã gồm cached
    cached_tokens: int = 0
    output_tokens: int = 0      # đã gồm reasoning

    @property
    def cost(self) -> float:
        fresh = self.input_tokens - self.cached_tokens
        return (fresh * PRICE_IN + self.cached_tokens * PRICE_CACHED + self.output_tokens * PRICE_OUT) / 1_000_000

    def minus(self, other: "Usage") -> "Usage":
        return Usage(self.calls - other.calls, self.input_tokens - other.input_tokens,
                     self.cached_tokens - other.cached_tokens, self.output_tokens - other.output_tokens)


class Meter(BaseCallbackHandler):
    """Cộng dồn usage_metadata của mọi lần gọi model (kể cả structured output và tool calling)."""

    def __init__(self) -> None:
        self.total = Usage()

    def on_llm_end(self, response, **kwargs) -> None:
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if not usage:
                    continue
                details = usage.get("input_token_details") or {}
                self.total.calls += 1
                self.total.input_tokens += int(usage.get("input_tokens", 0))
                self.total.cached_tokens += int(details.get("cache_read", 0))
                self.total.output_tokens += int(usage.get("output_tokens", 0))

    def snapshot(self) -> Usage:
        return Usage(**vars(self.total))


METER = Meter()


def exceeded() -> bool:
    return METER.total.cost >= BUDGET_USD


def summary() -> str:
    u = METER.total
    return (f"LLM: {u.calls} lần gọi, {u.input_tokens:,} token vào ({u.cached_tokens:,} cache), "
            f"{u.output_tokens:,} token ra, ≈ ${u.cost:.4f} / trần ${BUDGET_USD:.2f}")


def install() -> None:
    """Thay LLM của app bằng ChatOpenAI có đếm token và reasoning_effort thấp. Gọi trước khi node nào gọi _get_llm()."""
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    from app.agent import llm
    from app.config import settings

    assert settings.openai_api_key, "RUN_LLM_TESTS=1 nhưng thiếu OPENAI_API_KEY"
    effort = os.getenv("L5_REASONING_EFFORT", "low")
    kwargs = {"reasoning_effort": effort} if effort and effort != "default" else {}
    llm._llm = ChatOpenAI(model=settings.openai_model, temperature=settings.openai_temperature,
                          api_key=SecretStr(settings.openai_api_key), callbacks=[METER], **kwargs)
    llm._llm_init_attempted = True
