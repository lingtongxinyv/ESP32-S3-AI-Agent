from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    Conversation,
    Device,
    FirmwareRelease,
    Message,
    OTAEvent,
    Pairing,
    User,
    UserToken,
    utcnow,
)
from .schemas import (
    ClaimRequest,
    ClaimResponse,
    ConversationResponse,
    DeviceResponse,
    FirmwareResponse,
    HistoryPullResponse,
    ItemsResponse,
    LoginRequest,
    MessageResponse,
    NetworkAuthorizationRequest,
    NetworkAuthorizationResponse,
    OTAEventResponse,
    OTAManifest,
    OTARequest,
    OTAResultRequest,
    PairingRequest,
    PairingResponse,
    PanelCommand,
    PanelStatusResponse,
    ProfilePayload,
    ProfileStatusResponse,
    RefreshRequest,
    TokenResponse,
    wake_word_to_command,
)
from .security import (
    authenticate_user,
    get_admin_user,
    get_current_device,
    get_current_user,
    hash_token,
    issue_token_pair,
    owned_device,
)


router = APIRouter(prefix="/api/v1")


def _panel_verify(settings, username: str, password: str) -> bool | None:
    """用面板账号校验。True 通过 / False 拒绝 / None 面板不可用（回退本地密码）。"""
    if not settings.panel_base_url:
        return None
    try:
        response = httpx.post(
            f"{settings.panel_base_url}/api/auth/login",
            json={"username": username, "password": password},
            timeout=settings.panel_timeout,
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 200:
        return True
    if response.status_code in (400, 401, 403):
        return False
    return None


def _upsert_panel_user(db: Session, username: str) -> User:
    """面板账号首次登录时建立本地映射行。

    这里**不保存任何可用密码**：口令只由管理员在面板创建和下发，
    客户端登录必须先通过面板校验，本地这行只用于设备归属和权限。
    """
    user = db.scalar(select(User).where(User.username == username))
    if user is not None:
        return user
    user = User(username=username, password_hash="!panel-only")
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "账号映射创建失败")
        return user
    db.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    username = payload.username.strip()
    panel = _panel_verify(request.app.state.settings, username, payload.password)
    if panel is True:
        user = _upsert_panel_user(db, username)
    elif panel is False:
        user = None
    else:
        # 面板暂不可用时回退本地密码，避免面板维护导致全部客户端无法登录。
        user = authenticate_user(db, username, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    return issue_token_pair(db, user, request.app.state.settings)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    token = db.scalar(
        select(UserToken).where(UserToken.token_hash == hash_token(payload.refresh_token))
    )
    if (
        token is None
        or token.kind != "refresh"
        or token.revoked_at is not None
        or token.expires_at <= utcnow()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "刷新令牌无效或已过期")
    user = db.get(User, token.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    token.revoked_at = utcnow()
    return issue_token_pair(db, user, request.app.state.settings)


@router.post("/pairings", response_model=PairingResponse)
def create_pairing(
    payload: PairingRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(Device, payload.device_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "设备已被认领")
    raw = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(
        minutes=request.app.state.settings.pairing_token_minutes
    )
    db.add(
        Pairing(
            user_id=user.id,
            device_id=payload.device_id,
            token_hash=hash_token(raw),
            expires_at=expires_at,
        )
    )
    db.commit()
    return {"pairing_token": raw, "expires_at": expires_at}


@router.post("/devices/claim", response_model=ClaimResponse)
def claim_device(payload: ClaimRequest, db: Session = Depends(get_db)):
    pairing = db.scalar(
        select(Pairing).where(
            Pairing.device_id == payload.device_id,
            Pairing.token_hash == hash_token(payload.pairing_token),
        )
    )
    if pairing is None or pairing.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "配对令牌无效或已过期")
    if pairing.used_at is not None or db.get(Device, payload.device_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "配对已完成，设备只能认领一次")

    claimed_at = utcnow()
    result = db.execute(
        update(Pairing)
        .where(Pairing.id == pairing.id, Pairing.used_at.is_(None))
        .values(used_at=claimed_at)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "配对令牌已使用")
    raw_device_token = secrets.token_urlsafe(32)
    db.add(
        Device(
            id=payload.device_id,
            user_id=pairing.user_id,
            device_token_hash=hash_token(raw_device_token),
            claimed_at=claimed_at,
            last_seen_at=claimed_at,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "设备已被认领") from None
    return {"device_id": payload.device_id, "device_token": raw_device_token}


@router.post("/devices/{device_id}/network_authorization", response_model=PairingResponse)
def create_network_authorization(
    device_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """设备所属账号签发一次性改网授权，复用配对令牌的哈希与过期机制。"""
    device = owned_device(device_id, user, db)
    raw = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(
        minutes=request.app.state.settings.pairing_token_minutes
    )
    db.add(
        Pairing(
            user_id=user.id,
            device_id=device.id,
            token_hash=hash_token(raw),
            expires_at=expires_at,
        )
    )
    db.commit()
    return {"pairing_token": raw, "expires_at": expires_at}


@router.post("/devices/network_authorize", response_model=NetworkAuthorizationResponse)
def authorize_network(payload: NetworkAuthorizationRequest, db: Session = Depends(get_db)):
    """由机器人确认改网；保留设备归属和原令牌，只消费一次性授权。"""
    pairing = db.scalar(
        select(Pairing).where(
            Pairing.device_id == payload.device_id,
            Pairing.token_hash == hash_token(payload.pairing_token),
        )
    )
    if pairing is None or pairing.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "改网授权无效或已过期")
    if pairing.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "改网授权已使用")
    device = db.get(Device, payload.device_id)
    if device is None or device.device_token_hash != hash_token(payload.device_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "设备令牌无效")
    if pairing.user_id != device.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "改网授权与设备归属不一致")

    result = db.execute(
        update(Pairing)
        .where(Pairing.id == pairing.id, Pairing.used_at.is_(None))
        .values(used_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "改网授权已使用")
    db.commit()
    return {"device_id": device.id, "ok": True}


@router.get("/devices", response_model=ItemsResponse[DeviceResponse])
def list_devices(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    devices = db.scalars(
        select(Device).where(Device.user_id == user.id).order_by(Device.id)
    )
    return {
        "items": [
            DeviceResponse(
                id=device.id,
                device_id=device.id,
                name=device.id,
                hardware=device.hardware,
                fw_version=device.fw_version,
                protocol=device.protocol,
                online=device.online,
                claimed_at=device.claimed_at,
                last_seen_at=device.last_seen_at,
            )
            for device in devices
        ]
    }


@router.delete("/devices/{device_id}")
def delete_device(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = owned_device(device_id, user, db)
    db.execute(delete(Pairing).where(Pairing.device_id == device_id))
    db.delete(device)
    db.commit()
    return {"ok": True}


@router.post("/devices/{device_id}/config", response_model=ProfileStatusResponse)
async def push_device_config(
    device_id: str,
    payload: ProfilePayload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把客户端生成的配置包下发到机器人。服务器只在内存保留，不落盘。"""
    device = owned_device(device_id, user, db)
    store = request.app.state.profiles
    bundle = payload.model_dump()
    bundle["wake_command"] = wake_word_to_command(payload.wake_word)
    profile = store.put(device.id, user.id, bundle)
    connection = request.app.state.online_devices.sessions.get(device.id)
    if connection is None:
        return ProfileStatusResponse(
            device_id=device.id,
            online=False,
            delivered=False,
            version=profile.version,
            error="设备离线，配置已暂存，上线后自动下发",
        )
    waiter = store.begin_config_wait(device.id)
    try:
        await connection.send_json(0, "config", version=profile.version, bundle=bundle)
        result = await asyncio.wait_for(waiter, timeout=10)
    except asyncio.TimeoutError:
        store.cancel_config_wait(device.id)
        return ProfileStatusResponse(
            device_id=device.id,
            online=True,
            delivered=False,
            version=profile.version,
            error="设备未在 10 秒内确认",
        )
    except Exception as exc:
        store.cancel_config_wait(device.id)
        return ProfileStatusResponse(
            device_id=device.id,
            online=True,
            delivered=False,
            version=profile.version,
            error=f"下发失败：{type(exc).__name__}",
        )
    return ProfileStatusResponse(
        device_id=device.id,
        online=True,
        delivered=bool(result.get("ok")),
        version=result.get("version", profile.version),
        error=result.get("error", ""),
    )


@router.get("/devices/{device_id}/config/status", response_model=ProfileStatusResponse)
def device_config_status(
    device_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = owned_device(device_id, user, db)
    profile = request.app.state.profiles.get(device.id)
    online = device.id in request.app.state.online_devices.connections
    return ProfileStatusResponse(
        device_id=device.id,
        online=online,
        delivered=profile is not None and online,
        version=profile.version if profile is not None else None,
        error="" if profile is not None else "服务器内存中没有该设备的配置",
    )


@router.post("/devices/{device_id}/panel", response_model=PanelStatusResponse)
async def push_device_panel(
    device_id: str,
    payload: PanelCommand,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手机端改 OLED 画面 / 喇叭音量：服务器转发给设备并等待确认。"""
    device = owned_device(device_id, user, db)
    store = request.app.state.panels
    state = store.get(device.id)
    if payload.volume is None and payload.emotion is None and not payload.clear:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "至少给出音量或表情")
    connection = request.app.state.online_devices.sessions.get(device.id)
    if connection is None:
        return PanelStatusResponse(
            device_id=device.id,
            online=False,
            delivered=False,
            volume=state.volume,
            emotion=state.emotion,
            text=state.text,
            active=state.active,
            error="设备离线，指令未下发",
        )
    waiter = store.begin_wait(device.id)
    try:
        await connection.send_json(
            0,
            "panel",
            volume=payload.volume,
            emotion=None if payload.clear else payload.emotion,
            text=payload.text or "",
            clear=payload.clear,
        )
        result = await asyncio.wait_for(waiter, timeout=10)
    except asyncio.TimeoutError:
        store.cancel_wait(device.id)
        return PanelStatusResponse(
            device_id=device.id,
            online=True,
            delivered=False,
            volume=state.volume,
            emotion=state.emotion,
            text=state.text,
            active=state.active,
            error="设备未在 10 秒内确认",
        )
    except Exception as exc:
        store.cancel_wait(device.id)
        return PanelStatusResponse(
            device_id=device.id,
            online=True,
            delivered=False,
            volume=state.volume,
            emotion=state.emotion,
            text=state.text,
            active=state.active,
            error=f"下发失败：{type(exc).__name__}",
        )
    if not result.get("ok"):
        return PanelStatusResponse(
            device_id=device.id,
            online=True,
            delivered=False,
            volume=state.volume,
            emotion=state.emotion,
            text=state.text,
            active=state.active,
            error=result.get("error") or "设备拒绝了指令",
        )
    state = store.update(
        device.id,
        volume=payload.volume if payload.volume is not None else result.get("volume"),
        emotion=None if payload.clear else payload.emotion,
        text=None if payload.clear else (payload.text or ""),
        active=False if payload.clear else (True if payload.emotion else state.active),
    )
    return PanelStatusResponse(
        device_id=device.id,
        online=True,
        delivered=True,
        volume=state.volume,
        emotion=state.emotion,
        text=state.text,
        active=state.active,
    )


@router.get("/devices/{device_id}/panel", response_model=PanelStatusResponse)
def device_panel_status(
    device_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手机端打开控制面板时读取上次下发的音量/表情。"""
    device = owned_device(device_id, user, db)
    state = request.app.state.panels.get(device.id)
    online = device.id in request.app.state.online_devices.connections
    return PanelStatusResponse(
        device_id=device.id,
        online=online,
        delivered=online,
        volume=state.volume,
        emotion=state.emotion,
        text=state.text,
        active=state.active,
        error="" if online else "设备离线",
    )


@router.post("/devices/{device_id}/history/pull", response_model=HistoryPullResponse)
async def pull_device_history(
    device_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """让机器人上报本地历史，服务器只做转发，不保存。"""
    device = owned_device(device_id, user, db)
    connection = request.app.state.online_devices.sessions.get(device.id)
    if connection is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "设备离线，无法拉取历史")
    store = request.app.state.profiles
    waiter = store.begin_history_wait(device.id)
    try:
        await connection.send_json(0, "history_request")
        items = await asyncio.wait_for(waiter, timeout=10)
    except asyncio.TimeoutError:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "设备未在 10 秒内返回历史")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"拉取失败：{type(exc).__name__}")
    finally:
        store.cancel_history_wait(device.id)
    return HistoryPullResponse(device_id=device.id, items=items)


@router.post("/devices/{device_id}/history/ack")
async def ack_device_history(
    device_id: str,
    request: Request,
    count: int = Query(default=0, ge=0, le=40),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """客户端归档完成后通知机器人清掉已同步的部分。"""
    device = owned_device(device_id, user, db)
    connection = request.app.state.online_devices.sessions.get(device.id)
    if connection is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "设备离线，无法清理历史")
    await connection.send_json(0, "history_ack", count=count)
    return {"ok": True, "count": count}


@router.get("/conversations", response_model=ItemsResponse[ConversationResponse])
def list_conversations(
    device_id: str | None = None,
    date: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conditions = [Conversation.user_id == user.id]
    if device_id is not None:
        owned_device(device_id, user, db)
        conditions.append(Conversation.device_id == device_id)
    if date is not None:
        try:
            start = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "date 必须为 YYYY-MM-DD") from None
        conditions.extend(
            [Conversation.started_at >= start, Conversation.started_at < start + timedelta(days=1)]
        )
    return {
        "items": list(
            db.scalars(
                select(Conversation).where(*conditions).order_by(Conversation.started_at.desc())
            )
        )
    }


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ItemsResponse[MessageResponse],
)
def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return {
        "items": list(
            db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        )
    }


@router.get("/firmware", response_model=ItemsResponse[FirmwareResponse])
def list_firmware(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return {
        "items": list(
            db.scalars(select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc()))
        )
    }


@router.post("/firmware", response_model=FirmwareResponse)
async def upload_firmware(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(..., min_length=1, max_length=64),
    hardware: str = Form(..., min_length=1, max_length=64),
    protocol: str = Form(..., min_length=1, max_length=16),
    notes: str = Form(default="", max_length=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    firmware_dir: Path = request.app.state.settings.firmware_dir
    firmware_dir.mkdir(parents=True, exist_ok=True)
    destination = (firmware_dir / f"{secrets.token_hex(16)}.bin").resolve()
    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                output.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "固件不能为空")
        release = FirmwareRelease(
            version=version.strip(),
            hardware=hardware.strip(),
            protocol=protocol.strip(),
            sha256=digest.hexdigest(),
            size=size,
            notes=notes.strip(),
            file_path=str(destination),
            uploaded_by=user.id,
        )
        db.add(release)
        db.commit()
        db.refresh(release)
        return release
    except IntegrityError:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_409_CONFLICT, "相同版本、硬件和协议的固件已存在") from None
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/firmware/{firmware_id}/publish", response_model=FirmwareResponse)
def publish_firmware(
    firmware_id: int,
    _admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    release = db.get(FirmwareRelease, firmware_id)
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "固件不存在")
    db.execute(
        update(FirmwareRelease)
        .where(
            FirmwareRelease.hardware == release.hardware,
            FirmwareRelease.protocol == release.protocol,
            FirmwareRelease.id != release.id,
        )
        .values(published_at=None)
    )
    release.published_at = utcnow()
    db.commit()
    db.refresh(release)
    return release


@router.post("/devices/{device_id}/ota", response_model=OTAEventResponse)
async def request_ota(
    device_id: str,
    payload: OTARequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = owned_device(device_id, user, db)
    release = db.get(FirmwareRelease, payload.firmware_id)
    if release is None or release.published_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目标固件尚未发布")
    if release.hardware != device.hardware or release.protocol != device.protocol:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "固件硬件或协议与设备不匹配")
    event = OTAEvent(
        device_id=device.id, firmware_id=release.id, event="requested", status="pending"
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    connection = request.app.state.online_devices.sessions.get(device.id)
    if connection is not None:
        try:
            await connection.send_json(0, "ota_check", firmware_id=release.id)
        except Exception:
            # 通知失败不影响 pending 请求；设备上线或六小时周期检查仍会拾取。
            pass
    return event


@router.get("/devices/{device_id}/ota", response_model=ItemsResponse[OTAEventResponse])
def list_ota_events(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = owned_device(device_id, user, db)
    events = db.scalars(
        select(OTAEvent)
        .where(OTAEvent.device_id == device.id)
        .order_by(OTAEvent.id.desc())
    )
    items = []
    for event in events:
        release = db.get(FirmwareRelease, event.firmware_id)
        items.append(
            OTAEventResponse(
                id=event.id,
                device_id=event.device_id,
                firmware_id=event.firmware_id,
                fw_version=release.version if release is not None else "",
                event=event.event,
                status=event.status,
                detail=event.detail,
                created_at=event.created_at,
            )
        )
    return {"items": items}


def _published_release(db: Session, hardware: str, protocol: str) -> FirmwareRelease | None:
    return db.scalar(
        select(FirmwareRelease)
        .where(
            FirmwareRelease.hardware == hardware,
            FirmwareRelease.protocol == protocol,
            FirmwareRelease.published_at.is_not(None),
        )
        .order_by(FirmwareRelease.published_at.desc(), FirmwareRelease.id.desc())
        .limit(1)
    )


def _requested_release(db: Session, device: Device) -> FirmwareRelease | None:
    """桌面端“推送指定版本”产生的 pending 请求优先于最新发布版本。"""
    event = db.scalar(
        select(OTAEvent)
        .where(
            OTAEvent.device_id == device.id,
            OTAEvent.event == "requested",
            OTAEvent.status == "pending",
        )
        .order_by(OTAEvent.id.desc())
        .limit(1)
    )
    if event is None:
        return None
    release = db.get(FirmwareRelease, event.firmware_id)
    if (
        release is None
        or release.published_at is None
        or release.hardware != device.hardware
        or release.protocol != device.protocol
    ):
        return None
    return release


@router.get("/device/ota/current", response_model=OTAManifest)
def ota_current(
    request: Request,
    current_version: str | None = Query(default=None, max_length=64),
    hardware: str | None = Query(default=None, max_length=64),
    protocol: str | None = Query(default=None, max_length=16),
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    if hardware is not None:
        device.hardware = hardware
    if protocol is not None:
        device.protocol = protocol
    db.commit()
    release = _requested_release(db, device) or _published_release(
        db, device.hardware, device.protocol
    )
    reported_version = current_version if current_version is not None else device.fw_version
    manifest = OTAManifest(
        device_id=device.id,
        current_version=reported_version,
        update_available=release is not None and release.version != reported_version,
    )
    if release is None:
        return manifest
    token = request.query_params.get("token", "")
    if not token:
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            token = credential.strip()
    public_base = request.app.state.settings.public_base_url
    prefix = "" if public_base else request.headers.get("x-forwarded-prefix", "").rstrip("/")
    base = public_base or str(request.base_url).rstrip("/")
    download_url = f"{base}{prefix}/api/v1/device/ota/download/{release.id}"
    if token:
        download_url += f"?token={quote(token, safe='')}"
    manifest.firmware_id = release.id
    manifest.version = release.version
    manifest.hardware = release.hardware
    manifest.protocol = release.protocol
    manifest.notes = release.notes
    manifest.size = release.size
    manifest.sha256 = release.sha256
    manifest.download_url = download_url
    return manifest


@router.get("/device/ota/download/{firmware_id}")
def ota_download(
    firmware_id: int,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    release = db.get(FirmwareRelease, firmware_id)
    if (
        release is None
        or release.published_at is None
        or release.hardware != device.hardware
        or release.protocol != device.protocol
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "已发布固件不存在")
    path = Path(release.file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "固件文件不存在")
    db.execute(
        update(OTAEvent)
        .where(
            OTAEvent.device_id == device.id,
            OTAEvent.firmware_id == release.id,
            OTAEvent.event == "requested",
            OTAEvent.status == "pending",
        )
        .values(status="downloading")
    )
    db.add(
        OTAEvent(
            device_id=device.id,
            firmware_id=release.id,
            event="download",
            status="started",
        )
    )
    db.commit()
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"firmware-{release.version}.bin",
        headers={"X-SHA256": release.sha256},
    )


@router.post("/device/ota/result", response_model=OTAEventResponse)
def ota_result(
    payload: OTAResultRequest,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    if db.get(FirmwareRelease, payload.firmware_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "固件不存在")
    db.execute(
        update(OTAEvent)
        .where(
            OTAEvent.device_id == device.id,
            OTAEvent.firmware_id == payload.firmware_id,
            OTAEvent.event == "requested",
        )
        .values(status=payload.status)
    )
    event = OTAEvent(
        device_id=device.id,
        firmware_id=payload.firmware_id,
        event="result",
        status=payload.status,
        detail=payload.detail,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
