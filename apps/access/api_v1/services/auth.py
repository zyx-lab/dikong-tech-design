import secrets
from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.access.api_v1.authentication import sha256_text
from apps.access.api_v1.context import is_formal_business_account
from apps.access.exceptions import StandardConstraintConflict, StandardPhoneDuplicate, StandardUnauthorized
from apps.access.models import AuthSession, AuthSessionType, StaffProfile, User, UserStatus

ACCESS_TOKEN_TTL_SECONDS = 2 * 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
MOCK_SMS_CODE = "123456"


def _token_payload(session: AuthSession, access_token: str, refresh_token: str) -> dict:
    return {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "tokenType": "Bearer",
        "expiresIn": ACCESS_TOKEN_TTL_SECONDS,
        "refreshExpiresIn": REFRESH_TOKEN_TTL_SECONDS,
    }


def _session_type_for_user(user: User) -> str:
    return AuthSessionType.PLATFORM if user.is_platform_admin else AuthSessionType.BUSINESS


def _is_formal_login_account(user: User) -> bool:
    return bool(
        user
        and not user.is_superuser
        and user.is_active
        and user.status == UserStatus.ACTIVE
        and (user.is_platform_admin or is_formal_business_account(user))
    )


def authenticate_formal_user(*, username: str, password: str) -> User:
    user = authenticate(username=username, password=password)
    if not _is_formal_login_account(user):
        raise StandardUnauthorized()
    return user


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
        created_ip=_resolve_client_ip(request),
        last_used_ip=_resolve_client_ip(request),
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
    if not _is_formal_login_account(user):
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
    session.last_used_ip = _resolve_client_ip(request)
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


def revoke_all_user_sessions(user: User) -> int:
    return AuthSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


@transaction.atomic
def register_by_username(*, username: str, password: str, name: str, phone: str) -> User:
    if User.objects.filter(username=username).exists():
        from apps.access.exceptions import StandardDuplicate

        raise StandardDuplicate(msg="用户名已存在")
    if User.objects.filter(username=phone).exists() or StaffProfile.objects.filter(phone=phone).exists():
        raise StandardPhoneDuplicate()
    user = User.objects.create_user(username=username, password=password, is_staff=False)
    StaffProfile.objects.create(user=user, name=name, phone=phone)
    return user


@transaction.atomic
def register_by_phone(*, phone: str, sms_code: str, password: str) -> User:
    if sms_code != MOCK_SMS_CODE:
        raise serializers.ValidationError({"smsCode": ["短信验证码错误或失效"]})
    if User.objects.filter(username=phone).exists() or StaffProfile.objects.filter(phone=phone).exists():
        raise StandardPhoneDuplicate()
    user = User.objects.create_user(username=phone, password=password, is_staff=False)
    StaffProfile.objects.create(user=user, name=phone, phone=phone)
    return user


def _resolve_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
