from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.access.models import (
    DirectoryStatus,
    EmploymentStatus,
    Role,
    StaffProfile,
    Tenant,
    TenantMember,
    TenantMemberRole,
    TenantMemberRoleStatus,
    TenantMemberStatus,
    TenantStatus,
    User,
    UserStatus,
)


@dataclass(frozen=True)
class TestAccountSpec:
    suffix: str
    role_code: str
    display_name: str


TEST_ACCOUNT_SPECS = [
    TestAccountSpec("tenant_admin", "tenant_admin", "前端测试-租户管理员"),
    TestAccountSpec("biz_admin", "business_admin", "前端测试-业务管理员"),
    TestAccountSpec("dispatcher", "dispatcher", "前端测试-调度员"),
    TestAccountSpec("pilot", "pilot_operator", "前端测试-飞手"),
    TestAccountSpec("route", "route_planner", "前端测试-航线规划员"),
    TestAccountSpec("auditor", "auditor", "前端测试-审计员"),
]


class Command(BaseCommand):
    help = "创建前端联调用测试租户及多角色账号（可重复执行）"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", default="frontend_lab", help="租户编码")
        parser.add_argument("--tenant-name", default="前端联调租户", help="租户名称")
        parser.add_argument("--password", default="FrontTest@123", help="测试账号统一密码")
        parser.add_argument(
            "--username-prefix",
            default="fe",
            help="测试账号名前缀，最终用户名格式为 <prefix>_<tenant_code>_<suffix>",
        )

    def _ensure_roles(self) -> dict[str, Role]:
        role_codes = sorted({spec.role_code for spec in TEST_ACCOUNT_SPECS})
        roles = {role.code: role for role in Role.objects.filter(code__in=role_codes, status=DirectoryStatus.ACTIVE)}

        missing_codes = [code for code in role_codes if code not in roles]
        if missing_codes:
            raise CommandError(
                "missing active roles: {}. Please run: python manage.py seed_role_permissions --mode replace".format(
                    ", ".join(missing_codes)
                )
            )

        return roles

    @transaction.atomic
    def handle(self, *args, **options):
        tenant_code = options["tenant_code"].strip()
        tenant_name = options["tenant_name"].strip()
        password = options["password"]
        username_prefix = options["username_prefix"].strip()

        if not tenant_code:
            raise CommandError("tenant_code cannot be empty")
        if not tenant_name:
            raise CommandError("tenant_name cannot be empty")
        if not username_prefix:
            raise CommandError("username_prefix cannot be empty")

        roles = self._ensure_roles()

        tenant, tenant_created = Tenant.objects.update_or_create(
            code=tenant_code,
            defaults={
                "name": tenant_name,
                "status": TenantStatus.ACTIVE,
            },
        )
        if tenant_created:
            self.stdout.write(self.style.SUCCESS(f"created tenant: {tenant.code} (id={tenant.id})"))
        else:
            self.stdout.write(self.style.SUCCESS(f"updated tenant: {tenant.code} (id={tenant.id})"))

        now = timezone.now()
        created_users = 0
        updated_users = 0

        for spec in TEST_ACCOUNT_SPECS:
            username = f"{username_prefix}_{tenant_code}_{spec.suffix}"

            user = User.objects.filter(username=username).first()
            user_created = user is None
            if user_created:
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                    is_platform_admin=False,
                )
                created_users += 1
            else:
                updated_users += 1
                if user.is_superuser or user.is_platform_admin:
                    raise CommandError(
                        f"user {username} is superuser/platform_admin and cannot join tenant members"
                    )
                user.status = UserStatus.ACTIVE
                user.is_active = True
                user.save(update_fields=["status", "is_active", "updated_at"])

            user.set_password(password)
            user.save(update_fields=["password", "updated_at"])

            StaffProfile.objects.update_or_create(
                user=user,
                defaults={
                    "name": spec.display_name,
                    "employment_status": EmploymentStatus.ACTIVE,
                },
            )

            member, _ = TenantMember.objects.get_or_create(
                tenant=tenant,
                user=user,
                defaults={
                    "display_name": spec.display_name,
                    "member_no": f"{tenant_code}-{spec.suffix}"[:64],
                    "status": TenantMemberStatus.ACTIVE,
                    "joined_at": now,
                    "responded_at": now,
                },
            )

            member.display_name = spec.display_name
            member.status = TenantMemberStatus.ACTIVE
            member.invitation_token = None
            member.invited_by_user = None
            member.invited_at = None
            member.expires_at = None
            member.joined_at = member.joined_at or now
            member.responded_at = member.responded_at or now
            if not member.member_no:
                member.member_no = f"{tenant_code}-{spec.suffix}"[:64]
            member.save()

            role = roles[spec.role_code]
            TenantMemberRole.objects.update_or_create(
                tenant_member=member,
                system_role=role,
                defaults={
                    "status": TenantMemberRoleStatus.GRANTED,
                    "assigned_by_user": None,
                    "assigned_at": now,
                },
            )

            # Revoke any other roles for deterministic frontend testing behavior.
            TenantMemberRole.objects.filter(tenant_member=member).exclude(system_role=role).update(
                status=TenantMemberRoleStatus.REVOKED,
                updated_at=now,
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"prepared account username={username} role={spec.role_code} tenant={tenant.code}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                "bootstrap done: tenant={} created_users={} updated_users={} password={}"
                .format(tenant.code, created_users, updated_users, password)
            )
        )
