import secrets
from datetime import timedelta

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from apps.access.authentication import sha256_text
from apps.access.exceptions import StandardUnauthorized
from apps.access.models import (
    AuthSession,
    AuthSessionType,
    DirectoryStatus,
    User,
    UserStatus,
)
from apps.common.request import resolve_client_ip

ACCESS_TOKEN_TTL_SECONDS = 2 * 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def _token_payload(session: AuthSession, access_token: str, refresh_token: str) -> dict:
    del session
    return {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "tokenType": "Bearer",
        "expiresIn": ACCESS_TOKEN_TTL_SECONDS,
        "refreshExpiresIn": REFRESH_TOKEN_TTL_SECONDS,
    }


def _session_type_for_user(user: User) -> str:
    return AuthSessionType.PLATFORM if user.is_platform_admin else AuthSessionType.BUSINESS


def _has_active_v2_profile(user: User) -> bool:
    try:
        account_profile_model = apps.get_model("iam_v2", "V2AccountProfile")
    except LookupError:
        return False
    return account_profile_model.objects.filter(user=user, status=DirectoryStatus.ACTIVE).exists()


def is_formal_login_account(user: User | None) -> bool:
    if not user:
        return False
    if user.is_superuser:
        return False
    if not user.is_active or user.status != UserStatus.ACTIVE:
        return False
    return bool(user.is_platform_admin or _has_active_v2_profile(user))


def create_auth_session(*, user: User, request) -> dict:
    now = timezone.now()
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)
    session = AuthSession.objects.create(
        user=user,
        session_type=_session_type_for_user(user),
        access_token_hash=sha256_text(access_token),
        refresh_token_hash=sha256_text(refresh_token),
        access_token_expires_at=now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
        refresh_token_expires_at=now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS),
        created_ip=resolve_client_ip(request),
        last_used_ip=resolve_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:255],
        last_used_at=now,
    )
    user.last_login = now
    user.save(update_fields=["last_login", "updated_at"])
    payload = _token_payload(session, access_token, refresh_token)
    payload["user"] = {
        "id": user.id,
        "username": user.username,
        "status": user.status,
        "hasPlatformAccess": bool(user.is_platform_admin),
    }
    return payload


@transaction.atomic
def refresh_auth_session(*, refresh_token: str, request) -> dict:
    now = timezone.now()
    session = (
        AuthSession.objects.select_related("user")
        .filter(
            refresh_token_hash=sha256_text(refresh_token),
            revoked_at__isnull=True,
            refresh_token_expires_at__gt=now,
        )
        .first()
    )
    if session is None:
        raise StandardUnauthorized()

    user = session.user
    if not is_formal_login_account(user):
        session.revoked_at = now
        session.save(update_fields=["revoked_at", "updated_at"])
        raise StandardUnauthorized()

    access_token = secrets.token_urlsafe(32)
    new_refresh_token = secrets.token_urlsafe(48)
    session.access_token_hash = sha256_text(access_token)
    session.refresh_token_hash = sha256_text(new_refresh_token)
    session.access_token_expires_at = now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    session.refresh_token_expires_at = now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)
    session.last_refreshed_at = now
    session.last_used_at = now
    session.last_used_ip = resolve_client_ip(request)
    session.save(
        update_fields=[
            "access_token_hash",
            "refresh_token_hash",
            "access_token_expires_at",
            "refresh_token_expires_at",
            "last_refreshed_at",
            "last_used_at",
            "last_used_ip",
            "updated_at",
        ]
    )
    return _token_payload(session, access_token, new_refresh_token)


def revoke_current_session(session: AuthSession) -> None:
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at", "updated_at"])
