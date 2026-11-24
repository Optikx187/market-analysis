import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main, organizations
from app.auth import reset_current_user_key, set_current_user_key
from app.config import settings
from app.database import Base, get_db
from app.models import OrganizationAccessAudit


@pytest.fixture
def organization_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    database_path = tmp_path / "organizations.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    asyncio.run(create_tables())
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "JWT_SECRET",
        "organization-authorization-test-secret",
    )
    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _register(client: TestClient, username: str) -> tuple[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": f"{username}-password",
            "display_name": username.title(),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return str(payload["user_id"]), str(payload["token"])


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _organizations(
    client: TestClient,
    token: str,
) -> list[dict[str, object]]:
    response = client.get("/api/organizations", headers=_headers(token))
    assert response.status_code == 200, response.text
    return response.json()["organizations"]


def test_registration_creates_owned_default_organization(
    organization_client: TestClient,
) -> None:
    user_id, token = _register(organization_client, "alice")

    rows = _organizations(organization_client, token)

    assert len(rows) == 1
    assert rows[0]["name"] == "Alice workspace"
    assert rows[0]["role"] == "owner"
    assert "members.update" in rows[0]["permissions"]
    members = organization_client.get(
        f"/api/organizations/{rows[0]['id']}/members",
        headers=_headers(token),
    )
    assert members.status_code == 200
    assert members.json()["members"] == [
        {
            "id": members.json()["members"][0]["id"],
            "organization_id": rows[0]["id"],
            "user_key": user_id,
            "username": "alice",
            "role": "owner",
            "is_active": True,
            "joined_at": members.json()["members"][0]["joined_at"],
        }
    ]


def test_registration_truncates_default_organization_name(
    organization_client: TestClient,
) -> None:
    response = organization_client.post(
        "/api/auth/register",
        json={
            "username": "long-name",
            "password": "password123",
            "display_name": "x" * 200,
        },
    )
    assert response.status_code == 200
    organization = _organizations(
        organization_client,
        response.json()["token"],
    )[0]
    assert organization["name"].endswith(" workspace")
    assert len(organization["name"]) == 200


def test_cross_organization_access_is_rejected_and_audited(
    organization_client: TestClient,
) -> None:
    _, alice_token = _register(organization_client, "alice")
    bob_id, bob_token = _register(organization_client, "bob")
    alice_organization = _organizations(organization_client, alice_token)[0]

    denied = organization_client.get(
        f"/api/organizations/{alice_organization['id']}/members",
        headers=_headers(bob_token),
    )

    assert denied.status_code == 403
    audit = organization_client.get(
        f"/api/organizations/{alice_organization['id']}/access-audit",
        headers=_headers(alice_token),
    )
    assert audit.status_code == 200
    assert audit.json()["events"][0] == {
        "id": audit.json()["events"][0]["id"],
        "actor_user_key": bob_id,
        "action": "members.read",
        "outcome": "denied",
        "required_roles": ["admin", "analyst", "operator", "owner", "viewer"],
        "detail": "No active membership with the required role",
        "created_at": audit.json()["events"][0]["created_at"],
    }


def test_invitation_acceptance_and_role_matrix(
    organization_client: TestClient,
) -> None:
    _, alice_token = _register(organization_client, "alice")
    _, bob_token = _register(organization_client, "bob")
    _register(organization_client, "carol")
    organization_id = _organizations(organization_client, alice_token)[0]["id"]

    invitation = organization_client.post(
        f"/api/organizations/{organization_id}/invitations",
        headers=_headers(alice_token),
        json={"username": "bob", "role": "analyst"},
    )
    assert invitation.status_code == 201, invitation.text
    token = invitation.json()["token"]

    accepted = organization_client.post(
        "/api/organizations/invitations/accept",
        headers=_headers(bob_token),
        json={"token": token},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "analyst"
    assert len(_organizations(organization_client, bob_token)) == 2

    replay = organization_client.post(
        "/api/organizations/invitations/accept",
        headers=_headers(bob_token),
        json={"token": token},
    )
    assert replay.status_code == 404
    denied_invite = organization_client.post(
        f"/api/organizations/{organization_id}/invitations",
        headers=_headers(bob_token),
        json={"username": "carol", "role": "viewer"},
    )
    assert denied_invite.status_code == 403

    members = organization_client.get(
        f"/api/organizations/{organization_id}/members",
        headers=_headers(alice_token),
    ).json()["members"]
    bob_membership = next(row for row in members if row["username"] == "bob")
    promoted = organization_client.patch(
        f"/api/organizations/{organization_id}/members/{bob_membership['id']}",
        headers=_headers(alice_token),
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    admin_invite = organization_client.post(
        f"/api/organizations/{organization_id}/invitations",
        headers=_headers(bob_token),
        json={"username": "carol", "role": "admin"},
    )
    assert admin_invite.status_code == 403


def test_new_and_removed_members_cannot_use_older_invitations(
    organization_client: TestClient,
) -> None:
    _, owner_token = _register(organization_client, "owner")
    _, bob_token = _register(organization_client, "bob")
    organization_id = _organizations(organization_client, owner_token)[0]["id"]

    first = organization_client.post(
        f"/api/organizations/{organization_id}/invitations",
        headers=_headers(owner_token),
        json={"username": "bob", "role": "viewer"},
    ).json()
    second = organization_client.post(
        f"/api/organizations/{organization_id}/invitations",
        headers=_headers(owner_token),
        json={"username": "bob", "role": "analyst"},
    ).json()

    assert organization_client.post(
        "/api/organizations/invitations/accept",
        headers=_headers(bob_token),
        json={"token": first["token"]},
    ).status_code == 404
    accepted = organization_client.post(
        "/api/organizations/invitations/accept",
        headers=_headers(bob_token),
        json={"token": second["token"]},
    )
    assert accepted.status_code == 200

    membership_id = accepted.json()["id"]
    assert organization_client.delete(
        f"/api/organizations/{organization_id}/members/{membership_id}",
        headers=_headers(owner_token),
    ).status_code == 204
    assert organization_client.post(
        "/api/organizations/invitations/accept",
        headers=_headers(bob_token),
        json={"token": first["token"]},
    ).status_code == 404


def test_last_owner_cannot_be_removed_or_downgraded(
    organization_client: TestClient,
) -> None:
    _, owner_token = _register(organization_client, "owner")
    organization_id = _organizations(organization_client, owner_token)[0]["id"]
    owner_membership = organization_client.get(
        f"/api/organizations/{organization_id}/members",
        headers=_headers(owner_token),
    ).json()["members"][0]

    unchanged = organization_client.patch(
        f"/api/organizations/{organization_id}/members/{owner_membership['id']}",
        headers=_headers(owner_token),
        json={"role": "owner"},
    )
    downgrade = organization_client.patch(
        f"/api/organizations/{organization_id}/members/{owner_membership['id']}",
        headers=_headers(owner_token),
        json={"role": "admin"},
    )
    remove = organization_client.delete(
        f"/api/organizations/{organization_id}/members/{owner_membership['id']}",
        headers=_headers(owner_token),
    )

    assert unchanged.status_code == 200
    assert downgrade.status_code == 409
    assert remove.status_code == 409


def test_unknown_permission_is_denied_by_default_and_audited(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "deny-default.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def exercise() -> tuple[str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        token = set_current_user_key("default")
        try:
            async with session_factory() as db:
                organization = await organizations.create_owned_organization(
                    db,
                    user_key="default",
                    username="local-operator",
                    display_name="Local operator",
                )
                await db.commit()
                organization_id = organization.id
            async with session_factory() as db:
                with pytest.raises(HTTPException) as denied:
                    await organizations.require_permission(
                        db,
                        organization_id=organization_id,
                        permission="billing.manage",
                    )
                assert denied.value.status_code == 403
            async with session_factory() as db:
                audit = (
                    await db.execute(select(OrganizationAccessAudit))
                ).scalars().one()
                return audit.action, audit.outcome
        finally:
            reset_current_user_key(token)
            await engine.dispose()

    assert asyncio.run(exercise()) == ("billing.manage", "denied")
