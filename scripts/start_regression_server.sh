#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export SQLITE_DB_NAME="${SQLITE_DB_NAME:-db.regression.sqlite3}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-127.0.0.1,localhost}"
export REGRESSION_ROOT_USERNAME="${REGRESSION_ROOT_USERNAME:-root}"
export REGRESSION_ROOT_PASSWORD="${REGRESSION_ROOT_PASSWORD:-admin123}"

HOST="${DJANGO_RUNSERVER_HOST:-0.0.0.0}"
PORT="${DJANGO_RUNSERVER_PORT:-8011}"

python manage.py migrate
python manage.py seed_role_permissions --mode replace
python manage.py bootstrap_frontend_test_tenant
python manage.py shell -c '
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ["REGRESSION_ROOT_USERNAME"]
password = os.environ["REGRESSION_ROOT_PASSWORD"]
user = User.objects.filter(username=username).first()
if user is None:
    User.objects.create_superuser(username=username, password=password)
else:
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.status = 1
    user.set_password(password)
    user.save(update_fields=["is_staff", "is_superuser", "is_active", "status", "password", "updated_at"])
'

exec python manage.py runserver "${HOST}:${PORT}"
