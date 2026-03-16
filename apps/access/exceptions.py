"""Custom exception handlers for DRF."""
import logging

from rest_framework.exceptions import APIException, AuthenticationFailed, NotAuthenticated
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.api_v1.business_response import build_standard_response

logger = logging.getLogger(__name__)


class BusinessAPIException(APIException):
    """Base API exception with stable business error payload."""

    business_code = "INVALID_PARAMS"
    business_detail_code = "ERROR"

    def __init__(self, detail=None, *, business_detail_code=None):
        super().__init__(detail=detail)
        if business_detail_code:
            self.business_detail_code = business_detail_code


class BusinessResourceNotFound(BusinessAPIException):
    status_code = 404
    default_detail = "resource not found"
    default_code = "resource_not_found"
    business_code = "RESOURCE_NOT_FOUND"
    business_detail_code = "NOT_FOUND"


class BusinessStateConflict(BusinessAPIException):
    status_code = 409
    default_detail = "state conflict"
    default_code = "state_conflict"
    business_code = "STATE_CONFLICT"
    business_detail_code = "STATE_CONFLICT"


class BusinessIdempotentDuplicate(BusinessAPIException):
    status_code = 409
    default_detail = "duplicate request"
    default_code = "duplicate_request"
    business_code = "IDEMPOTENT_DUPLICATE"
    business_detail_code = "DUPLICATE_REQUEST"


class BusinessPermissionDenied(BusinessAPIException):
    status_code = 403
    default_detail = "permission denied"
    default_code = "permission_denied"
    business_code = "PERMISSION_DENIED"
    business_detail_code = "FORBIDDEN"


def custom_exception_handler(exc, context):
    """DRF exception handler with a dedicated /api/v1 standard envelope."""
    response = exception_handler(exc, context)
    request = context.get("request")
    is_business_api = request is not None and request.path.startswith("/api/v1/")

    if response is not None:
        if is_business_api:
            if isinstance(exc, BusinessAPIException):
                payload = {"detail": str(exc.detail)}
            elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)) or response.status_code == 401:
                payload = {"detail": str(response.data.get("detail", "Authentication required"))}
            elif response.status_code == 403:
                if hasattr(exc, "message") and exc.message:
                    detail = exc.message
                elif isinstance(response.data, dict) and "detail" in response.data:
                    detail = str(response.data["detail"])
                else:
                    detail = "Permission denied"
                payload = {"detail": detail}
            else:
                payload = response.data
            response.data = build_standard_response(payload, response.status_code, trace_id=getattr(request, "trace_id", None))
        else:
            if isinstance(exc, BusinessAPIException):
                response.data = {
                    "business_code": exc.business_code,
                    "business_detail_code": exc.business_detail_code,
                    "detail": str(exc.detail),
                }

            # Some authentication failures are rendered as 403 by DRF,
            # so check the exception type before falling back to generic forbidden.
            elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)) or response.status_code == 401:
                response.data = {
                    "business_code": "PERMISSION_DENIED",
                    "business_detail_code": "NOT_AUTHENTICATED",
                    "detail": str(response.data.get('detail', 'Authentication required')),
                }

            # Check if it's a permission denied error (403)
            elif response.status_code == 403:
                # Extract the permission message if available
                if hasattr(exc, 'message') and exc.message:
                    detail = exc.message
                elif isinstance(response.data, dict) and 'detail' in response.data:
                    detail = str(response.data['detail'])
                else:
                    detail = "Permission denied"

                response.data = {
                    "business_code": "PERMISSION_DENIED",
                    "business_detail_code": "FORBIDDEN",
                    "detail": detail,
                }

        return response

    if request is not None:
        logger.exception(
            "Unhandled API exception on %s %s",
            request.method,
            request.get_full_path(),
            exc_info=exc,
        )
    else:
        logger.exception("Unhandled API exception", exc_info=exc)

    if is_business_api:
        return Response(
            build_standard_response({"detail": "internal server error"}, 500, trace_id=getattr(request, "trace_id", None)),
            status=500,
        )

    return Response(
        {
            "business_code": "INTERNAL_ERROR",
            "business_detail_code": "INTERNAL_ERROR",
            "detail": "internal server error",
        },
        status=500,
    )
