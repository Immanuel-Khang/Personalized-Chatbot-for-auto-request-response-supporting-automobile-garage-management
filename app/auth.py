"""Đăng nhập admin: tài khoản trong bảng managers, mật khẩu PBKDF2, token ký HMAC (không cần thư viện ngoài).
Vai trò: manager (xem mọi cuộc chat kể cả bot tự xử lý), sales_admin / technician (chỉ hàng đợi chuyển người và lịch hẹn).
ADMIN_AUTH_REQUIRED=false tắt xác thực (dev/test): mọi request coi như manager."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.config import settings

log = logging.getLogger(__name__)
ITERATIONS = 200_000


@dataclass(frozen=True)
class AdminUser:
    id: int | None
    username: str | None
    role: str

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(candidate, digest)


def _key() -> bytes:
    if settings.admin_secret:
        return settings.admin_secret.encode()
    log.warning("ADMIN_SECRET chưa đặt: dùng khóa suy ra từ DATABASE_URL. Hãy đặt ADMIN_SECRET khi triển khai.")
    return hashlib.sha256(("garage-admin|" + settings.database_url).encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_token(user: AdminUser) -> str:
    body = _b64(json.dumps({"i": user.id, "u": user.username, "r": user.role,
                            "e": int(time.time() + settings.admin_token_hours * 3600)}).encode())
    return f"{body}.{_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())}"


def parse_token(token: str) -> AdminUser | None:
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if data["e"] < time.time():
            return None
        return AdminUser(id=data["i"], username=data["u"], role=data["r"])
    except (ValueError, KeyError, TypeError):
        return None


def admin_user(authorization: str | None = Header(default=None)) -> AdminUser:
    """FastAPI dependency: người đang gọi admin API."""
    if not settings.admin_auth_required:
        return AdminUser(id=None, username=None, role="manager")
    user = parse_token(authorization[7:]) if authorization and authorization.startswith("Bearer ") else None
    if user is None:
        raise HTTPException(401, "Cần đăng nhập")
    return user
