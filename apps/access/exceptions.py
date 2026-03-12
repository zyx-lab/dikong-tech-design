"""Custom exception handlers for DRF."""
from rest_framework.exceptions import APIException, AuthenticationFailed, NotAuthenticated
from rest_framework.views import exception_handler


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
    business_detail_code = "RESOURCE_NOT_FOUND"


class BusinessStateConflict(BusinessAPIException):
    status_code = 409
    default_detail = "state conflict"
    default_code = "state_conflict"
    business_code = "STATE_CONFLICT"
    business_detail_code = "STATE_CONFLICT"


def custom_exception_handler(exc, context):
    """Custom exception handler that formats permission denied with business_code."""
    response = exception_handler(exc, context)

    if response is not None:
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
