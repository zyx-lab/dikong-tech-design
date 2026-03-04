from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.crypto import get_random_string

from apps.access.models import EmploymentStatus, StaffProfile, StaffType, UserStatus

User = get_user_model()


class Command(BaseCommand):
    help = "创建或更新业务管理员角色账号（普通业务账号，非 Django superuser）"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="登录账号")
        parser.add_argument("--password", help="登录密码；不传则自动生成")
        parser.add_argument("--staff-no", help="人员编号；默认 BS-<USERNAME>")
        parser.add_argument("--name", default="业务管理员", help="人员姓名")
        parser.add_argument("--phone", default="", help="手机号")
        parser.add_argument("--email", default="", help="邮箱")
        parser.add_argument("--org-id", type=int, default=None, help="组织 ID（可选）")
        parser.add_argument(
            "--upsert",
            action="store_true",
            help="若账号已存在则更新账号与 staff 绑定；默认存在即报错",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("username cannot be empty")

        staff_type = StaffType.objects.filter(code="business_admin").first()
        if not staff_type:
            raise CommandError("staff_type 'business_admin' not found, run seed_role_permissions first")

        password = options.get("password") or get_random_string(16)
        password_auto_generated = not bool(options.get("password"))

        staff_no = options.get("staff_no") or f"BS-{username.upper()}"
        name = options.get("name") or "业务管理员"
        phone = options.get("phone") or ""
        email = options.get("email") or ""
        org_id = options.get("org_id")

        user = User.objects.filter(username=username).first()
        if user and not options.get("upsert"):
            raise CommandError("user already exists, use --upsert to update")

        if not user:
            user = User.objects.create_user(
                username=username,
                password=password,
                is_staff=False,
                is_active=True,
                status=UserStatus.ACTIVE,
            )
            self.stdout.write(self.style.SUCCESS(f"created user: {user.username} (id={user.id})"))
        else:
            if user.is_superuser:
                raise CommandError("existing user is superuser; superuser cannot be business account")
            user.is_active = True
            user.status = UserStatus.ACTIVE
            user.set_password(password)
            user.save(update_fields=["is_active", "status", "password", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"updated user: {user.username} (id={user.id})"))

        staff = StaffProfile.objects.filter(user=user).first()
        if not staff:
            # 一账号一 staff：首次创建时直接绑定业务管理员角色 staff_type。
            staff = StaffProfile.objects.create(
                user=user,
                staff_no=staff_no,
                name=name,
                phone=phone,
                email=email,
                employment_status=EmploymentStatus.ACTIVE,
                staff_type=staff_type,
                org_id=org_id,
            )
            self.stdout.write(self.style.SUCCESS(f"created staff profile: {staff.staff_no} (id={staff.id})"))
        else:
            staff.staff_no = staff_no
            staff.name = name
            staff.phone = phone
            staff.email = email
            staff.employment_status = EmploymentStatus.ACTIVE
            staff.staff_type = staff_type
            staff.org_id = org_id
            staff.save(
                update_fields=[
                    "staff_no",
                    "name",
                    "phone",
                    "email",
                    "employment_status",
                    "staff_type",
                    "org_id",
                    "updated_at",
                ]
            )
            self.stdout.write(self.style.SUCCESS(f"updated staff profile: {staff.staff_no} (id={staff.id})"))

        self.stdout.write(self.style.SUCCESS("business admin account is ready"))
        self.stdout.write(f"username={user.username}")
        if password_auto_generated:
            self.stdout.write(self.style.WARNING(f"generated_password={password}"))
