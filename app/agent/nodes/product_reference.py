"""Canonical product-reference resolution for conversation interpretation."""
import re

from app.contracts.schemas import Product


ORDINALS = {
    "đầu tiên": 1, "đầu": 1, "nhất": 1, "hai": 2, "ba": 3,
    "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7,
    "tám": 8, "chín": 9, "mười": 10, "cuối cùng": -1, "cuối": -1,
}
_ORDINAL_WORDS = "|".join(sorted(ORDINALS, key=len, reverse=True))
POSITION_RE = re.compile(
    rf"\b(?:xe|mẫu|chiếc|dòng)\s+(?:thứ\s+|số\s+)?"
    rf"(\d{{1,2}}|{_ORDINAL_WORDS})\b"
    r"(?!\s*(?:tấn|triệu|tỷ|chỗ|giờ|km|nay|ngoái|\d))"
)
PRONOUN_RE = re.compile(r"\bnó\b|\b(?:mẫu|xe|chiếc|cái|dòng)\s+(?:này|đó|ấy|kia)\b")


def products_in(text: str, vehicles: list[Product]) -> list[Product]:
    """Return catalog products named in text, in their order of appearance."""
    lowered = text.lower()
    spans = [(index, index + len(vehicle.name), vehicle)
             for vehicle in vehicles
             if (index := lowered.find(vehicle.name.lower())) >= 0]
    kept = [span for span in spans if not any(
        other is not span and other[0] <= span[0] and span[1] <= other[1]
        and other[1] - other[0] > span[1] - span[0]
        for other in spans
    )]
    return [vehicle for _, _, vehicle in sorted(kept, key=lambda span: span[0])]


def resolve_product(text: str, ai_messages: list[str], vehicles: list[Product],
                    llm_name: str | None) -> tuple[Product | None, bool]:
    """Resolve a product only from an explicit name or a product list shown by the bot.

    Returns ``(product, needs_clarification)``. Ordinals refer to the most recent
    displayed list; an ambiguous or unavailable reference is never guessed.
    """
    named_in_message = products_in(text, vehicles)
    if named_in_message:
        return named_in_message[0], len(named_in_message) > 1

    shown = [products_in(message, vehicles) for message in ai_messages]
    by_position = None
    match = POSITION_RE.search(text.lower())
    if match:
        lists = [shown_list for shown_list in shown if len(shown_list) >= 2]
        if shown and len(shown[-1]) >= 2:
            listing = shown[-1]
        elif lists and len({tuple(product.id for product in shown_list) for shown_list in lists}) == 1:
            listing = lists[-1]
        else:
            return None, True
        word = match.group(1)
        position = int(word) if word.isdigit() else ORDINALS[word]
        if position != -1 and not 1 <= position <= len(listing):
            return None, True
        by_position = listing[position - 1] if position > 0 else listing[-1]

    named = None
    if llm_name and len(llm_name.strip()) >= 4:
        wanted = llm_name.strip().lower()
        seen = {product.id: product for shown_list in shown for product in shown_list}
        exact = [product for product in seen.values() if product.name.lower() == wanted]
        partial = [product for product in seen.values() if wanted in product.name.lower()]
        named = exact[0] if exact else (partial[0] if len(partial) == 1 else None)

    if by_position and named and named.id != by_position.id:
        return None, True
    if by_position or named:
        return by_position or named, False
    if PRONOUN_RE.search(text.lower()):
        recent = next((shown_list for shown_list in reversed(shown) if shown_list), [])
        return (recent[0], False) if len(recent) == 1 else (None, bool(recent))
    return None, False
