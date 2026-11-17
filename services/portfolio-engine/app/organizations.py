"""Organization membership and deny-by-default role authorization."""

import datetime
import hashlib
import re
import secrets
import uuid
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import DEFAULT_USER_KEY, current_user_key
from app.financial import POSTGRESQL_STORAGE
from app.models import (
    Organization,
    OrganizationAccessAudit,
    OrganizationInvitation,
    OrganizationMembership,
    User,
    _utc_now,
)

OWNER = "owner"
ADMIN = "admin"
OPERATOR = "operator"
ANALYST = "analyst"
VIEWER = "viewer"
ROLES = (OWNER, ADMIN, OPERATOR, ANALYST, VIEWER)
INVITABLE_ROLES = (ADMIN, OPERATOR, ANALYST, VIEWER)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    OWNER: frozenset({
        "organization.read",
        "organization.update",
        "members.read",
        "members.invite",
        "members.update",
        "members.remove",
        "audit.read",
        "operations.execute",
        "analysis.run",
    }),
    ADMIN: frozenset({
        "organization.read",
        "organization.update",
        "members.read",
        "members.invite",
        "members.update",
        "members.remove",
        "audit.read",
        "operations.execute",
        "analysis.run",
    }),
    OPERATOR: frozenset({
        "organization.read",
        "members.read",
        "operations.execute",
        "analysis.run",
    }),
    ANALYST: frozenset({
        "organization.read",
        "members.read",
        "analysis.run",
    }),
    VIEWER: frozenset({
        "organization.read",
        "members.read",
    }),
}


def parse_organization_id(value: object):
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(404, "Organization not found") from exc
    return parsed if POSTGRESQL_STORAGE else str(parsed)


def _user_id(user_key: str):
    try:
        return uuid.UUID(user_key) if POSTGRESQL_STORAGE else int(user_key)
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "A registered account is required") from exc


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        normalized = "organization"
    return f"{normalized}-{secrets.token_hex(4)}"[:100]


async def create_owned_organization(
    db: AsyncSession,
    *,
    user_key: str,
    username: str,
    display_name: str,
    organization_name: str | None = None,
) -> Organization:
    organization = Organization(
        slug=_slug(organization_name or username),
        name=organization_name or f"{display_name} workspace",
        created_by_user_key=user_key,
    )
    db.add(organization)
    await db.flush()
    db.add(OrganizationMembership(
        organization_id=organization.id,
        user_key=user_key,
        username=username,
        role=OWNER,
        is_active=True,
    ))
    return organization


async def record_access(
    db: AsyncSession,
    *,
    organization_id,
    action: str,
    outcome: str,
    required_roles: Iterable[str] = (),
    detail: str = "",
) -> None:
    db.add(OrganizationAccessAudit(
        organization_id=organization_id,
        actor_user_key=current_user_key() or DEFAULT_USER_KEY,
        action=action,
        outcome=outcome,
        required_roles=",".join(sorted(required_roles)),
        detail=detail[:1000],
    ))
    await db.flush()


async def require_permission(
    db: AsyncSession,
    *,
    organization_id,
    permission: str,
) -> OrganizationMembership:
    user_key = current_user_key() or DEFAULT_USER_KEY
    result = await db.execute(
        select(OrganizationMembership)
        .join(
            Organization,
            Organization.id == OrganizationMembership.organization_id,
        )
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_key == user_key,
            OrganizationMembership.is_active.is_(True),
            Organization.is_active.is_(True),
        )
    )
    membership = result.scalars().first()
    role = membership.role if membership else None
    if membership is None or permission not in ROLE_PERMISSIONS.get(role, frozenset()):
        required_roles = [
            candidate
            for candidate, permissions in ROLE_PERMISSIONS.items()
            if permission in permissions
        ]
        await record_access(
            db,
            organization_id=organization_id,
            action=permission,
            outcome="denied",
            required_roles=required_roles,
            detail="No active membership with the required role",
        )
        await db.commit()
        raise HTTPException(403, "Organization access denied")
    return membership


async def list_organizations(db: AsyncSession) -> list[dict[str, object]]:
    user_key = current_user_key() or DEFAULT_USER_KEY
    result = await db.execute(
        select(Organization, OrganizationMembership)
        .join(
            OrganizationMembership,
            OrganizationMembership.organization_id == Organization.id,
        )
        .where(
            OrganizationMembership.user_key == user_key,
            OrganizationMembership.is_active.is_(True),
            Organization.is_active.is_(True),
        )
        .order_by(Organization.name, Organization.id)
    )
    return [
        organization_dict(organization, membership.role)
        for organization, membership in result.all()
    ]


def organization_dict(
    organization: Organization,
    role: str,
) -> dict[str, object]:
    return {
        "id": str(organization.id),
        "slug": organization.slug,
        "name": organization.name,
        "role": role,
        "permissions": sorted(ROLE_PERMISSIONS.get(role, frozenset())),
        "created_at": organization.created_at,
    }


def membership_dict(
    membership: OrganizationMembership,
) -> dict[str, object]:
    return {
        "id": str(membership.id),
        "organization_id": str(membership.organization_id),
        "user_key": membership.user_key,
        "username": membership.username,
        "role": membership.role,
        "is_active": membership.is_active,
        "joined_at": membership.joined_at,
    }


async def create_invitation(
    db: AsyncSession,
    *,
    organization_id,
    invitee_username: str,
    role: str,
) -> tuple[OrganizationInvitation, str]:
    actor = await require_permission(
        db,
        organization_id=organization_id,
        permission="members.invite",
    )
    if role not in INVITABLE_ROLES:
        raise HTTPException(422, "Invitation role is not permitted")
    if actor.role == ADMIN and role == ADMIN:
        raise HTTPException(403, "Only an owner can invite an administrator")

    user = (
        await db.execute(
            select(User).where(
                User.username == invitee_username,
                User.is_active.is_(True),
            )
        )
    ).scalars().first()
    if user is None:
        raise HTTPException(404, "Active registered user not found")
    user_key = str(user.id)
    existing = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_key == user_key,
                OrganizationMembership.is_active.is_(True),
            )
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(409, "User is already an active member")

    token = secrets.token_urlsafe(32)
    invitation = OrganizationInvitation(
        organization_id=organization_id,
        invitee_username=invitee_username,
        role=role,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        invited_by_user_key=current_user_key() or DEFAULT_USER_KEY,
        expires_at=_utc_now() + datetime.timedelta(days=7),
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    return invitation, token


async def accept_invitation(
    db: AsyncSession,
    *,
    token: str,
) -> OrganizationMembership:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    invitation = (
        await db.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.token_hash == token_hash,
            ).with_for_update()
        )
    ).scalars().first()
    now = _utc_now()
    if (
        invitation is None
        or invitation.revoked_at is not None
        or invitation.accepted_at is not None
        or invitation.expires_at <= now
    ):
        raise HTTPException(404, "Invitation is invalid or expired")

    user_key = current_user_key() or DEFAULT_USER_KEY
    user = await db.get(User, _user_id(user_key))
    if user is None or user.username != invitation.invitee_username:
        raise HTTPException(403, "Invitation belongs to another account")

    membership = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == invitation.organization_id,
                OrganizationMembership.user_key == user_key,
            )
        )
    ).scalars().first()
    if membership is None:
        membership = OrganizationMembership(
            organization_id=invitation.organization_id,
            user_key=user_key,
            username=user.username,
            role=invitation.role,
            is_active=True,
            invited_by_user_key=invitation.invited_by_user_key,
        )
        db.add(membership)
    else:
        membership.role = invitation.role
        membership.is_active = True
        membership.invited_by_user_key = invitation.invited_by_user_key
        membership.joined_at = now
    invitation.accepted_at = now
    await db.commit()
    await db.refresh(membership)
    return membership


async def active_owner_count(
    db: AsyncSession,
    *,
    organization_id,
) -> int:
    value = await db.scalar(
        select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == OWNER,
            OrganizationMembership.is_active.is_(True),
        )
    )
    return int(value or 0)
