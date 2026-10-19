"""[TRACK B] Nhận diện loại dịch vụ đặt lịch (service_type) theo luật, có thể kiểm soát được.
- Luật regex theo từng loại; khớp đúng một loại → chắc chắn.
- Khớp nhiều loại → chỉ giải quyết bằng PRECEDENCE khai báo sẵn, còn lại là mơ hồ → hỏi lại khách.
- Không khớp → để LLM / slot cũ quyết định, không có thì hỏi lại.
Muốn chỉnh hành vi: sửa SERVICE_PATTERNS / PRECEDENCE, không cần sửa prompt."""
import re
from dataclasses import dataclass

from app.contracts.schemas import ServiceType

_PLACE = r"(?:xưởng|showroom|đại lý|cửa hàng|garage|gara)"
_GO = r"(?:qua|đến|tới|ghé|lên|ra|vào)"

SERVICE_PATTERNS: dict[ServiceType, list[str]] = {
    ServiceType.TEST_DRIVE: [r"lái thử", r"chạy thử", r"thử xe", r"test drive"],
    ServiceType.SHOWROOM_VISIT: [
        r"showroom",
        r"xem (?:mẫu |các mẫu |những mẫu )?xe(?: mới)?\b(?! (?:bị|có|hỏng|hư|kêu|giúp|hộ|lỗi))",
        r"xem (?:xe )?(?:trực tiếp|tận mắt|thực tế)",
        rf"{_GO} {_PLACE} (?:để |đặng )?(?:xem|ngắm|tham quan)",
        r"tham quan",
    ],
    ServiceType.REPAIR: [r"sửa", r"hỏng", r"\bhư\b", r"\bkêu\b", r"\blỗi\b", r"thay phanh", r"\bbị (?:rò|chảy|nóng|rung)",
                         r"repair"],
    ServiceType.MAINTENANCE: [r"bảo dưỡng", r"bảo trì", r"thay dầu", r"thay nhớt", r"định kỳ", r"maintenance"],
}

# Khi khớp nhiều loại: (cặp loại) → loại thắng. Cặp không có ở đây = mơ hồ → hỏi lại.
PRECEDENCE: dict[frozenset[ServiceType], ServiceType] = {
    # "đến showroom lái thử" → lái thử là yêu cầu cụ thể hơn
    frozenset({ServiceType.TEST_DRIVE, ServiceType.SHOWROOM_VISIT}): ServiceType.TEST_DRIVE,
    # "qua xưởng xem xe bị kêu" / "qua xưởng để bảo dưỡng" → mang xe đến làm dịch vụ, không phải xem xe mới
    frozenset({ServiceType.REPAIR, ServiceType.SHOWROOM_VISIT}): ServiceType.REPAIR,
    frozenset({ServiceType.MAINTENANCE, ServiceType.SHOWROOM_VISIT}): ServiceType.MAINTENANCE,
    # REPAIR + MAINTENANCE, TEST_DRIVE + REPAIR... không khai báo → hỏi lại khách
}

LABELS: dict[ServiceType, str] = {
    ServiceType.MAINTENANCE: "bảo dưỡng",
    ServiceType.REPAIR: "sửa chữa",
    ServiceType.TEST_DRIVE: "lái thử",
    ServiceType.SHOWROOM_VISIT: "xem xe trực tiếp tại showroom",
}

_COMPILED = {s: [re.compile(p) for p in ps] for s, ps in SERVICE_PATTERNS.items()}


@dataclass(frozen=True)
class ServiceDetection:
    service: ServiceType | None  # kết luận chắc chắn, None nếu không rõ
    candidates: tuple[ServiceType, ...] = ()  # các loại cùng khớp khi mơ hồ (để hỏi lại đúng trọng tâm)

    @property
    def ambiguous(self) -> bool:
        return self.service is None and len(self.candidates) > 1


def parse_service_type(value: str | None) -> ServiceType | None:
    """Giá trị enum hợp lệ (VD từ LLM: 'TEST_DRIVE'); giá trị lạ → None."""
    if not value:
        return None
    try:
        return ServiceType(str(value).strip().upper())
    except ValueError:
        return None


def detect_service_type(text: str | None) -> ServiceDetection:
    """Nhận diện loại dịch vụ từ câu khách bằng luật."""
    if not text:
        return ServiceDetection(None)
    if service := parse_service_type(text):
        return ServiceDetection(service, (service,))
    lowered = " ".join(text.lower().split())
    found = tuple(s for s, patterns in _COMPILED.items() if any(p.search(lowered) for p in patterns))
    if len(found) == 1:
        return ServiceDetection(found[0], found)
    if len(found) == 2 and (winner := PRECEDENCE.get(frozenset(found))):
        return ServiceDetection(winner, found)
    return ServiceDetection(None, found)


def resolve_service_type(text: str | None, current: str | None) -> ServiceDetection:
    """Kết hợp luật (ưu tiên, kiểm soát được) với giá trị đã có (LLM trích xuất hoặc lượt trước).
    - Luật chắc chắn → dùng luật (kể cả khi khách đổi ý giữa chừng).
    - Luật mơ hồ → dùng giá trị đã có nếu nó nằm trong các ứng viên, nếu không thì hỏi lại.
    - Luật không khớp → giữ giá trị đã có nếu hợp lệ."""
    rule = detect_service_type(text)
    existing = parse_service_type(current) or detect_service_type(current).service
    if rule.service:
        return rule
    if rule.ambiguous:
        return ServiceDetection(existing, rule.candidates) if existing in rule.candidates else rule
    return ServiceDetection(existing, (existing,) if existing else ())


def clarify_question(candidates: tuple[ServiceType, ...]) -> str:
    """Câu hỏi lại cố định, chỉ nêu các lựa chọn đang mơ hồ."""
    options = [LABELS[c] for c in candidates] or [LABELS[s] for s in ServiceType]
    return f"Bạn muốn đặt lịch {', '.join(options[:-1])} hay {options[-1]} ạ?"
