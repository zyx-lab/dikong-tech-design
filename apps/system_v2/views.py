from django.db import IntegrityError, transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.access.authentication import BearerAuthSessionAuthentication
from apps.access.models import DirectoryStatus
from apps.common.api_response import BusinessApiResponseMixin, StandardCode, standard_error_payload
from apps.iam_v2.models import V2Menu, V2MenuPermissionBinding, V2Permission, V2Role, V2RoleMenuGrant
from apps.iam_v2.serializers import V2MenuReadSerializer, V2MenuWriteSerializer
from apps.iam_v2.services import apply_data_scope, is_platform_super_admin, require_v2_permission, resolve_v2_context
from apps.resource_v2.models import V2AuditLog
from apps.resource_v2.serializers import AuditLogReadSerializer
from apps.resource_v2.audit import log_v2_action
from apps.api_v2.openapi import list_data_serializer


class EmptySchemaSerializer(serializers.Serializer):
    pass


class SystemV2APIView(BusinessApiResponseMixin, GenericAPIView):
    authentication_classes = [BearerAuthSessionAuthentication]
    serializer_class = EmptySchemaSerializer


MENU_LIST_RESPONSE = list_data_serializer("V2SystemMenuListData", V2MenuReadSerializer)
OPERATION_LOG_LIST_RESPONSE = list_data_serializer("V2SystemOperationLogListData", AuditLogReadSerializer)


def _not_found_response():
    return Response(standard_error_payload(StandardCode.NOT_FOUND, "资源不存在", None), status=status.HTTP_404_NOT_FOUND)


def _duplicate_response(errors=None):
    return Response(standard_error_payload(StandardCode.DUPLICATE, "资源已存在", errors), status=status.HTTP_409_CONFLICT)


def _active_menus():
    return list(V2Menu.objects.prefetch_related("permission_bindings__permission").filter(status=DirectoryStatus.ACTIVE).order_by("sort", "id"))


def _menu_permission_codes(menu: V2Menu) -> set[str]:
    bindings = list(menu.permission_bindings.all())
    return {binding.permission.code for binding in bindings if binding.permission.status == DirectoryStatus.ACTIVE}


def _granted_menu_ids(context) -> set[int]:
    if is_platform_super_admin(context):
        return set(V2Menu.objects.filter(status=DirectoryStatus.ACTIVE).values_list("id", flat=True))
    role_ids = V2Role.objects.filter(code__in=context.role_codes, status=DirectoryStatus.ACTIVE).values_list("id", flat=True)
    return set(V2RoleMenuGrant.objects.filter(role_id__in=role_ids).values_list("menu_id", flat=True))


def _tree_from_menus(menus: list[V2Menu]) -> list[V2Menu]:
    children_by_parent: dict[int | None, list[V2Menu]] = {}
    for menu in menus:
        children_by_parent.setdefault(menu.parent_id, []).append(menu)
    for menu in menus:
        menu._visible_children = children_by_parent.get(menu.id, [])
    return children_by_parent.get(None, [])


def _visible_menu_tree(context) -> list[V2Menu]:
    menus = _active_menus()
    children_by_parent: dict[int | None, list[V2Menu]] = {}
    for menu in menus:
        children_by_parent.setdefault(menu.parent_id, []).append(menu)
    granted_ids = _granted_menu_ids(context)
    permission_codes = set(context.permissions)

    def visible_node(menu: V2Menu):
        children = []
        for child in children_by_parent.get(menu.id, []):
            visible_child = visible_node(child)
            if visible_child is not None:
                children.append(visible_child)

        if menu.menu_type == V2Menu.MenuType.DIRECTORY:
            if not children:
                return None
        else:
            bound_permissions = _menu_permission_codes(menu)
            has_bound_permission = not bound_permissions or bool(permission_codes.intersection(bound_permissions))
            if menu.id not in granted_ids or not has_bound_permission:
                return None

        menu._visible_children = children
        return menu

    visible_roots = []
    for menu in children_by_parent.get(None, []):
        visible = visible_node(menu)
        if visible is not None:
            visible_roots.append(visible)
    return visible_roots


def _sync_menu_permissions(*, menu: V2Menu, permission_ids: list[int]) -> None:
    permissions = list(V2Permission.objects.filter(id__in=permission_ids, status=DirectoryStatus.ACTIVE))
    found_ids = {permission.id for permission in permissions}
    missing = set(permission_ids) - found_ids
    if missing:
        raise serializers.ValidationError({"permissionIds": [f"权限不存在或已停用: {', '.join(str(item) for item in sorted(missing))}"]})
    V2MenuPermissionBinding.objects.filter(menu=menu).exclude(permission_id__in=found_ids).delete()
    for permission in permissions:
        V2MenuPermissionBinding.objects.get_or_create(menu=menu, permission=permission)


class CurrentMenuTreeView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_system_menus_current",
        summary="获取当前用户可见菜单树",
        responses={200: OpenApiResponse(response=MENU_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        menus = _visible_menu_tree(context)
        serializer = V2MenuReadSerializer(menus, many=True)
        return Response({"list": serializer.data, "total": len(menus)}, status=status.HTTP_200_OK)


class MenuTreeView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_system_menus_tree",
        summary="查询全部菜单树",
        responses={200: OpenApiResponse(response=MENU_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:menu:read")
        menus = _tree_from_menus(_active_menus())
        return Response({"list": V2MenuReadSerializer(menus, many=True).data, "total": len(menus)}, status=status.HTTP_200_OK)


class MenuListCreateView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_system_menus_list",
        summary="查询菜单列表",
        responses={200: OpenApiResponse(response=MENU_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:menu:read")
        queryset = V2Menu.objects.prefetch_related("permission_bindings__permission").order_by("sort", "id")
        serializer = V2MenuReadSerializer(queryset, many=True)
        return Response({"list": serializer.data, "total": queryset.count()}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_system_menus_create",
        summary="创建菜单",
        request=V2MenuWriteSerializer,
        responses={201: OpenApiResponse(response=V2MenuReadSerializer, description="创建成功。")},
    )
    @transaction.atomic
    def post(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:menu:create")
        serializer = V2MenuWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent = None
        parent_id = serializer.validated_data.get("parentId")
        if parent_id:
            parent = V2Menu.objects.filter(pk=parent_id).first()
            if parent is None:
                return _not_found_response()
        try:
            menu = V2Menu.objects.create(
                parent=parent,
                name=serializer.validated_data["name"],
                code=serializer.validated_data["code"],
                menu_type=serializer.validated_data["type"],
                path=serializer.validated_data.get("path", ""),
                component=serializer.validated_data.get("component", ""),
                icon=serializer.validated_data.get("icon", ""),
                sort=serializer.validated_data.get("sort", 100),
                status=serializer.validated_data.get("status", DirectoryStatus.ACTIVE),
            )
            _sync_menu_permissions(menu=menu, permission_ids=serializer.validated_data.get("permissionIds", []))
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = V2MenuReadSerializer(menu).data
        log_v2_action(
            request=request,
            context=context,
            action="create_menu",
            target_type="v2_menu",
            target_id=menu.id,
            after_data=data,
        )
        return Response(data, status=status.HTTP_201_CREATED)


class MenuDetailView(SystemV2APIView):
    def _menu(self, id: int):
        return V2Menu.objects.filter(pk=id).first()

    @extend_schema(
        operation_id="v2_system_menus_update",
        summary="更新菜单",
        request=V2MenuWriteSerializer,
        responses={200: OpenApiResponse(response=V2MenuReadSerializer, description="更新成功。")},
    )
    @transaction.atomic
    def put(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:menu:update")
        menu = self._menu(id)
        if menu is None:
            return _not_found_response()
        serializer = V2MenuWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent = None
        parent_id = serializer.validated_data.get("parentId")
        if parent_id:
            parent = V2Menu.objects.filter(pk=parent_id).first()
            if parent is None:
                return _not_found_response()
        before_data = V2MenuReadSerializer(menu).data
        menu.parent = parent
        menu.name = serializer.validated_data["name"]
        menu.code = serializer.validated_data["code"]
        menu.menu_type = serializer.validated_data["type"]
        menu.path = serializer.validated_data.get("path", "")
        menu.component = serializer.validated_data.get("component", "")
        menu.icon = serializer.validated_data.get("icon", "")
        menu.sort = serializer.validated_data.get("sort", 100)
        menu.status = serializer.validated_data.get("status", DirectoryStatus.ACTIVE)
        try:
            menu.save()
            _sync_menu_permissions(menu=menu, permission_ids=serializer.validated_data.get("permissionIds", []))
        except IntegrityError as exc:
            return _duplicate_response({"detail": str(exc)})
        data = V2MenuReadSerializer(menu).data
        log_v2_action(
            request=request,
            context=context,
            action="update_menu",
            target_type="v2_menu",
            target_id=menu.id,
            before_data=before_data,
            after_data=data,
        )
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="v2_system_menus_delete",
        summary="删除菜单",
        responses={200: OpenApiResponse(response=V2MenuReadSerializer, description="删除成功。")},
    )
    @transaction.atomic
    def delete(self, request, id: int):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:menu:delete")
        menu = self._menu(id)
        if menu is None:
            return _not_found_response()
        if menu.is_system:
            raise serializers.ValidationError({"id": ["内置菜单不可删除"]})
        before_data = V2MenuReadSerializer(menu).data
        menu.delete()
        log_v2_action(
            request=request,
            context=context,
            action="delete_menu",
            target_type="v2_menu",
            target_id=id,
            before_data=before_data,
        )
        return Response(before_data, status=status.HTTP_200_OK)


class OperationLogListView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_system_operation_logs_list",
        summary="查询操作日志",
        parameters=[
            OpenApiParameter("action", str, OpenApiParameter.QUERY, required=False, description="按动作过滤。"),
            OpenApiParameter("targetType", str, OpenApiParameter.QUERY, required=False, description="按目标类型过滤。"),
        ],
        responses={200: OpenApiResponse(response=OPERATION_LOG_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:operation_log:read")
        queryset = V2AuditLog.objects.select_related("actor_department", "resource_owner_department")
        queryset = apply_data_scope(context, queryset, department_field="actor_department")
        action = str(request.query_params.get("action") or "").strip()
        target_type = str(request.query_params.get("targetType") or "").strip()
        if action:
            queryset = queryset.filter(action=action)
        if target_type:
            queryset = queryset.filter(target_type=target_type)
        queryset = queryset.order_by("-created_at", "-id")
        return Response(
            {"list": AuditLogReadSerializer(queryset, many=True).data, "total": queryset.count()},
            status=status.HTTP_200_OK,
        )


class LoginLogListView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_system_login_logs_list",
        summary="查询登录日志",
        responses={200: OpenApiResponse(response=OPERATION_LOG_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:login_log:read")
        return Response({"list": [], "total": 0}, status=status.HTTP_200_OK)


class FileLogListView(SystemV2APIView):
    @extend_schema(
        operation_id="v2_system_file_logs_list",
        summary="查询文件日志",
        responses={200: OpenApiResponse(response=OPERATION_LOG_LIST_RESPONSE, description="查询成功。")},
    )
    def get(self, request):
        context = resolve_v2_context(request)
        require_v2_permission(context, "system:file_log:read")
        return Response({"list": [], "total": 0}, status=status.HTTP_200_OK)
