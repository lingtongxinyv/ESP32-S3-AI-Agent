from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Device, Pairing, User, UserToken, utcnow
from app.security import hash_token
from .helpers import PASSWORD, bearer, claim_device, login


def test_login_refresh_rotation_and_hash_storage(client, tokens):
    bad = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "wrong"}
    )
    assert bad.status_code == 401

    alice = tokens["alice"]
    assert alice["user"] == {"id": 2, "username": "alice", "is_admin": False}
    response = client.get("/api/v1/devices", headers=bearer(alice["access_token"]))
    assert response.status_code == 200
    assert client.get("/api/v1/devices", headers=bearer(alice["refresh_token"])).status_code == 401

    rotated = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]}
    )
    assert rotated.status_code == 200
    assert rotated.json()["access_token"] != alice["access_token"]
    reused = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]}
    )
    assert reused.status_code == 401

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "alice"))
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("scrypt$")
        stored_tokens = list(db.scalars(select(UserToken)))
        assert stored_tokens
        hashes = {item.token_hash for item in stored_tokens}
        for raw in (alice["access_token"], alice["refresh_token"]):
            assert raw not in hashes
            assert len(raw) > 20


def test_pairing_claim_one_time_and_device_ownership(client, tokens):
    access = tokens["alice"]["access_token"]
    response = client.post(
        "/api/v1/pairings", json={"device_id": "dev-alice"}, headers=bearer(access)
    )
    assert response.status_code == 200
    pairing_token = response.json()["pairing_token"]
    assert response.json()["expires_at"]

    wrong = client.post(
        "/api/v1/devices/claim",
        json={"device_id": "dev-alice", "pairing_token": "not-the-pairing-token"},
    )
    assert wrong.status_code == 400
    claimed = client.post(
        "/api/v1/devices/claim",
        json={"device_id": "dev-alice", "pairing_token": pairing_token},
    )
    assert claimed.status_code == 200
    device_token = claimed.json()["device_token"]
    assert client.post(
        "/api/v1/devices/claim",
        json={"device_id": "dev-alice", "pairing_token": pairing_token},
    ).status_code == 409
    assert client.post(
        "/api/v1/pairings", json={"device_id": "dev-alice"}, headers=bearer(access)
    ).status_code == 409

    alice_devices = client.get("/api/v1/devices", headers=bearer(access)).json()["items"]
    assert [item["id"] for item in alice_devices] == ["dev-alice"]
    assert alice_devices[0]["device_id"] == "dev-alice"
    assert "device_token" not in alice_devices[0]
    assert "device_token_hash" not in alice_devices[0]
    assert client.get(
        "/api/v1/devices", headers=bearer(tokens["bob"]["access_token"])
    ).json() == {"items": []}
    assert client.delete(
        "/api/v1/devices/dev-alice", headers=bearer(tokens["bob"]["access_token"])
    ).status_code == 404

    with SessionLocal() as db:
        pairing = db.scalar(select(Pairing).where(Pairing.device_id == "dev-alice"))
        device = db.get(Device, "dev-alice")
        assert pairing.token_hash != pairing_token
        assert pairing.used_at is not None
        assert device.device_token_hash != device_token

    assert client.delete("/api/v1/devices/dev-alice", headers=bearer(access)).status_code == 200
    assert client.get("/api/v1/devices", headers=bearer(access)).json() == {"items": []}


def test_network_authorization_preserves_identity_and_cannot_be_replayed(client, tokens):
    device_token = claim_device(client, tokens)
    alice_auth = bearer(tokens["alice"]["access_token"])
    with SessionLocal() as db:
        original = db.get(Device, "dev-alice")
        identity = (original.user_id, original.device_token_hash, original.claimed_at)

    response = client.post(
        "/api/v1/devices/dev-alice/network_authorization", headers=alice_auth
    )
    assert response.status_code == 200
    authorization = response.json()["pairing_token"]
    assert response.json()["expires_at"]
    assert client.post(
        "/api/v1/devices/dev-alice/network_authorization",
        headers=bearer(tokens["bob"]["access_token"]),
    ).status_code == 404

    wrong_token = client.post(
        "/api/v1/devices/network_authorize",
        json={
            "device_id": "dev-alice",
            "pairing_token": authorization,
            "device_token": "wrong-device-token",
        },
    )
    assert wrong_token.status_code == 401

    confirmed = client.post(
        "/api/v1/devices/network_authorize",
        json={
            "device_id": "dev-alice",
            "pairing_token": authorization,
            "device_token": device_token,
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {"device_id": "dev-alice", "ok": True}
    replay = client.post(
        "/api/v1/devices/network_authorize",
        json={
            "device_id": "dev-alice",
            "pairing_token": authorization,
            "device_token": device_token,
        },
    )
    assert replay.status_code == 409

    with SessionLocal() as db:
        device = db.get(Device, "dev-alice")
        assert (device.user_id, device.device_token_hash, device.claimed_at) == identity
        pairing = db.scalar(select(Pairing).where(Pairing.token_hash == hash_token(authorization)))
        assert pairing.used_at is not None
        assert pairing.token_hash != authorization


def test_network_authorization_expires(client, tokens):
    device_token = claim_device(client, tokens)
    response = client.post(
        "/api/v1/devices/dev-alice/network_authorization",
        headers=bearer(tokens["alice"]["access_token"]),
    )
    authorization = response.json()["pairing_token"]
    with SessionLocal() as db:
        pairing = db.scalar(select(Pairing).where(Pairing.token_hash == hash_token(authorization)))
        pairing.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    expired = client.post(
        "/api/v1/devices/network_authorize",
        json={
            "device_id": "dev-alice",
            "pairing_token": authorization,
            "device_token": device_token,
        },
    )
    assert expired.status_code == 400
    with SessionLocal() as db:
        pairing = db.scalar(select(Pairing).where(Pairing.token_hash == hash_token(authorization)))
        assert pairing.used_at is None


def test_device_claim_helper_and_unauthenticated_pairing(client, tokens):
    assert client.post("/api/v1/pairings", json={"device_id": "x"}).status_code == 401
    device_token = claim_device(client, tokens, "dev-alice")
    assert device_token
    assert login(client, "admin")["user"]["is_admin"] is True
