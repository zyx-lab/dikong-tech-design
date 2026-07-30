COMPOSE ?= docker compose
ADMIN_USERNAME ?= admin

.PHONY: help env up ps first-admin sync-catalog urls verify logs stop

help:
	@printf '%s\n' \
		'make env                              # 从 .env.example 创建 .env' \
		'make up                               # 启动 db/redis/minio/web/worker' \
		'make ps                               # 查看容器状态' \
		'make first-admin ADMIN_USERNAME=admin # 全新库首次创建平台管理员；需要 ADMIN_PASSWORD' \
		'make sync-catalog                     # 已有库同步权限/角色/菜单，不重置数据' \
		'make urls                             # 打印本地访问地址' \
		'make verify                           # 检查 schema 和 worker 单次运行' \
		'make logs                             # 跟随 web 和 worker 日志' \
		'make stop                             # 停止容器，不删数据卷'

env:
	@test -f .env || cp .env.example .env
	@chmod 600 .env
	@echo ".env ready"

up:
	$(COMPOSE) up -d --build

ps:
	$(COMPOSE) ps

first-admin:
	@test -n "$$ADMIN_PASSWORD" || (echo "ADMIN_PASSWORD is required. Run: read -s ADMIN_PASSWORD; export ADMIN_PASSWORD; make first-admin"; exit 1)
	$(COMPOSE) exec -T web python manage.py bootstrap_v2_system --reset --username "$(ADMIN_USERNAME)" --password "$$ADMIN_PASSWORD" --noinput

sync-catalog:
	$(COMPOSE) exec -T web python manage.py bootstrap_v2_system

urls:
	@WEB_PORT=$$(awk -F= '/^WEB_PORT=/{print $$2}' .env 2>/dev/null | tail -n 1); \
	MINIO_API_PORT=$$(awk -F= '/^MINIO_API_PORT=/{print $$2}' .env 2>/dev/null | tail -n 1); \
	MINIO_CONSOLE_PORT=$$(awk -F= '/^MINIO_CONSOLE_PORT=/{print $$2}' .env 2>/dev/null | tail -n 1); \
	WEB_PORT=$${WEB_PORT:-8000}; \
	MINIO_API_PORT=$${MINIO_API_PORT:-9000}; \
	MINIO_CONSOLE_PORT=$${MINIO_CONSOLE_PORT:-9001}; \
	echo "API: http://127.0.0.1:$${WEB_PORT}"; \
	echo "Swagger: http://127.0.0.1:$${WEB_PORT}/api/v2/docs/"; \
	echo "OpenAPI: http://127.0.0.1:$${WEB_PORT}/api/v2/docs/schema/"; \
	echo "MinIO API: http://127.0.0.1:$${MINIO_API_PORT}"; \
	echo "MinIO Console: http://127.0.0.1:$${MINIO_CONSOLE_PORT}"

verify:
	@WEB_PORT=$$(awk -F= '/^WEB_PORT=/{print $$2}' .env 2>/dev/null | tail -n 1); \
	WEB_PORT=$${WEB_PORT:-8000}; \
	curl -fsS "http://127.0.0.1:$${WEB_PORT}/api/v2/docs/schema/" > /dev/null
	$(COMPOSE) exec -T web python manage.py run_v2_dji_worker --once

logs:
	$(COMPOSE) logs -f --tail=200 web v2-dji-worker

stop:
	$(COMPOSE) down
