import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.access.models import AuthSession, UserStatus


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class BearerAuthSessionAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth:
            return None

        if auth[0].decode("utf-8", errors="ignore") != self.keyword:
            return None

        if len(auth) != 2:
            raise AuthenticationFailed("Invalid token")

        try:
            token = auth[1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AuthenticationFailed("Invalid token") from exc

        token_hash = sha256_text(token)
        now = timezone.now()
        session = (
            AuthSession.objects.select_related("user")
            .filter(
                access_token_hash=token_hash,
                revoked_at__isnull=True,
                access_token_expires_at__gt=now,
            )
            .first()
        )
        if session is None:
            raise AuthenticationFailed("Token invalid or expired")

        user = session.user
        if not user.is_active or user.status != UserStatus.ACTIVE:
            session.revoked_at = now
            session.save(update_fields=["revoked_at", "updated_at"])
            raise AuthenticationFailed("Token invalid or expired")

        session.last_used_at = now
        session.last_used_ip = _resolve_client_ip(request)
        session.save(update_fields=["last_used_at", "last_used_ip", "updated_at"])
        return user, session

    def authenticate_header(self, request):
        del request
        return self.keyword


def _resolve_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
