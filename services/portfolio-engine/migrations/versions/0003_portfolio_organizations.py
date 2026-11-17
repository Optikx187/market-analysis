"""Organization, membership, invitation, and authorization audit primitives.

Revision ID: 0003_portfolio
Revises: 0002_portfolio
Create Date: 2026-11-17
"""

import re
import uuid
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, select

from app import models
from app.auth import DEFAULT_USER_KEY
from app.config import settings

revision: str = "0003_portfolio"
down_revision: Union[str, Sequence[str], None] = "0002_portfolio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _organization_id(user_key: str):
    if user_key == DEFAULT_USER_KEY:
        value = uuid.UUID(settings.PORTFOLIO_TENANT_ID)
    else:
        value = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"market-analysis:organization:{user_key}",
        )
    return value if op.get_bind().dialect.name == "postgresql" else str(value)


def _slug(value: str, user_key: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    suffix = re.sub(r"[^a-z0-9]+", "", user_key.lower())[-12:] or "operator"
    return f"{normalized or 'organization'}-{suffix}"[:100]


def _create_tables() -> None:
    connection = op.get_bind()
    for table in (
        models.Organization.__table__,
        models.OrganizationMembership.__table__,
        models.OrganizationInvitation.__table__,
        models.OrganizationAccessAudit.__table__,
    ):
        table.create(bind=connection, checkfirst=True)


def _ensure_owned_organization(
    *,
    user_key: str,
    username: str,
    display_name: str,
) -> None:
    connection = op.get_bind()
    organization_id = _organization_id(user_key)
    organization = models.Organization.__table__
    membership = models.OrganizationMembership.__table__

    existing_organization = connection.execute(
        select(organization.c.id).where(organization.c.id == organization_id)
    ).first()
    if existing_organization is None:
        connection.execute(
            organization.insert().values(
                id=organization_id,
                slug=_slug(username, user_key),
                name=f"{display_name} workspace",
                is_active=True,
                created_by_user_key=user_key,
            )
        )

    existing_membership = connection.execute(
        select(membership.c.id).where(
            membership.c.organization_id == organization_id,
            membership.c.user_key == user_key,
        )
    ).first()
    if existing_membership is None:
        connection.execute(
            membership.insert().values(
                organization_id=organization_id,
                user_key=user_key,
                username=username,
                role="owner",
                is_active=True,
            )
        )


def _backfill_users() -> None:
    connection = op.get_bind()
    _ensure_owned_organization(
        user_key=DEFAULT_USER_KEY,
        username="local-operator",
        display_name="Local operator",
    )

    if "users" not in inspect(connection).get_table_names(
        schema="portfolio" if connection.dialect.name == "postgresql" else None
    ):
        return
    users = models.User.__table__
    for row in connection.execute(
        select(users.c.id, users.c.username, users.c.display_name)
    ).mappings():
        user_key = str(row["id"])
        _ensure_owned_organization(
            user_key=user_key,
            username=row["username"],
            display_name=row["display_name"] or row["username"],
        )


def upgrade() -> None:
    _create_tables()
    _backfill_users()


def downgrade() -> None:
    raise RuntimeError(
        "Organization ownership and authorization audit data are not downgraded "
        "in place. Restore the verified pre-migration backup instead."
    )
