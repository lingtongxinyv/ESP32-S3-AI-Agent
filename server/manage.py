from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal, configure_database, create_schema
from app.models import User
from app.security import hash_password


def create_user(username: str, admin: bool, password_stdin: bool) -> int:
    password = sys.stdin.readline().rstrip("\n") if password_stdin else getpass.getpass("密码: ")
    if not password:
        print("密码不能为空", file=sys.stderr)
        return 2
    if not password_stdin and password != getpass.getpass("再次输入密码: "):
        print("两次密码不一致", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    configure_database(settings.database_url)
    create_schema()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)) is not None:
            print("用户已存在", file=sys.stderr)
            return 1
        db.add(User(username=username, password_hash=hash_password(password), is_admin=admin))
        db.commit()
    print(f"已创建用户 {username}（admin={admin}）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ESP32-S3 AI Agent 管理命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user", help="创建用户（服务不开放注册）")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    create.add_argument("--password-stdin", action="store_true", help="从标准输入读取一行密码")
    args = parser.parse_args()
    if args.command == "create-user":
        return create_user(args.username, args.admin, args.password_stdin)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
