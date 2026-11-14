from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .database import get_db
from .models import Device, User, UserToken, utcnow


_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    n, r, p = 1 << 14, 8, 1
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return "$".join(
        (
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(expected)),
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User, kind: str, settings: Settings) -> str:
    raw = secrets.token_urlsafe(32)
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if kind == "access"
        else timedelta(days=settings.refresh_token_days)
    )
    db.add(
        UserToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            kind=kind,
            expires_at=utcnow() + lifetime,
        )
    )
    return raw


def issue_token_pair(db: Session, user: User, settings: Settings) -> dict:
    access = issue_token(db, user, "access", settings)
    refresh = issue_token(db, user, "refresh", settings)
    db.commit()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "user": user,
    }


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问令牌")
    token = db.scalar(
        select(UserToken).where(UserToken.token_hash == hash_token(credentials.credentials))
    )
    if (
        token is None
        or token.kind != "access"
        or token.revoked_at is not None
        or token.expires_at <= utcnow()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "访问令牌无效或已过期")
    user = db.get(User, token.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可执行此操作")
    return user


def get_current_device(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Device:
    # 设备令牌优先走 Authorization: Bearer（fw ≥1.0.1），query token 仅为旧固件兼容保留。
    if credentials is not None and credentials.scheme.lower() == "bearer":
        raw_token = credentials.credentials
    else:
        raw_token = request.query_params.get("token", "")
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少设备令牌")
    device = db.scalar(
        select(Device).where(Device.device_token_hash == hash_token(raw_token))
    )
    if device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "设备令牌无效")
    device.last_seen_at = utcnow()
    db.commit()
    return device


def owned_device(device_id: str, user: User, db: Session) -> Device:
    device = db.scalar(
        select(Device).where(Device.id == device_id, Device.user_id == user.id)
    )
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "设备不存在")
    return device
