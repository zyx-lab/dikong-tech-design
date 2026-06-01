from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPageNumberPagination(PageNumberPagination):
    """业务 API 统一分页协议。"""

    page_query_param = "pageNum"
    page_size_query_param = "pageSize"
    max_page_size = 100
    page_size = 20

    def get_page_size(self, request):
        page_size = request.query_params.get(self.page_size_query_param)
        if page_size is None:
            return self.page_size

        try:
            page_size = int(page_size)
        except (TypeError, ValueError):
            return self.page_size

        if page_size <= 0:
            return self.page_size

        if self.max_page_size is not None:
            return min(page_size, self.max_page_size)
        return page_size

    def get_paginated_response(self, data):
        return Response(
            {
                "list": data,
                "total": self.page.paginator.count,
            }
        )
