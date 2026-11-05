"""Quản lý tài khoản admin.
  python -m app.manage create-manager <username> <password> [--role manager|sales_admin|technician]
  python -m app.manage unlock <username>      # mở khóa tài khoản bị khóa do nhập sai mật khẩu
  python -m app.manage list-managers
Docker: docker compose -f docker-compose.dev.yml exec web python -m app.manage create-manager an 'mat-khau' --role manager"""
import argparse
from datetime import datetime, timezone

from app.auth import hash_password
from app.db.models import Manager
from app.db.session import SessionLocal, init_db


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-manager")
    c.add_argument("username")
    c.add_argument("password")
    c.add_argument("--role", default="manager", choices=("manager", "sales_admin", "technician"))
    sub.add_parser("list-managers")
    sub.add_parser("unlock").add_argument("username")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        if args.cmd == "list-managers":
            for m in db.query(Manager).order_by(Manager.id):
                locked = " LOCKED until " + m.locked_until.isoformat() if m.locked_until and m.locked_until > datetime.now(timezone.utc) else ""
                print(m.id, m.username, m.role, ("active" if m.is_active else "disabled") + locked)
            return
        if args.cmd == "unlock":
            row = db.query(Manager).filter_by(username=args.username).first()
            if row is None:
                raise SystemExit(f"Không có tài khoản {args.username}")
            row.failed_logins, row.locked_until = 0, None
            db.commit()
            print(f"Đã mở khóa {args.username}")
            return
        row = db.query(Manager).filter_by(username=args.username).first()
        if row:  # tạo lại = đổi mật khẩu/vai trò
            row.password_hash, row.role, row.is_active = hash_password(args.password), args.role, True
            row.failed_logins, row.locked_until = 0, None
            print(f"Đã cập nhật {args.username} ({args.role})")
        else:
            db.add(Manager(username=args.username, password_hash=hash_password(args.password), role=args.role))
            print(f"Đã tạo {args.username} ({args.role})")
        db.commit()


if __name__ == "__main__":
    main()
