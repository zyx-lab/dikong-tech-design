# 现网原地迁移：手动 `python manage.py runserver` 切换到 Docker PostgreSQL

适用场景：Django 现在是你在服务器上手动执行 `python manage.py runserver ...` 启动；当前数据库是 SQLite；准备把数据库切到 Docker 里的 PostgreSQL。目标是保留现有账号和业务数据，只做数据库底层切换。

这份文档不再展开所有底层命令，默认使用仓库里的 4 个 Python 脚本自动化迁移步骤：

- [scripts/precheck_backup_export.py](/home/charles/dikong-tech-design/scripts/precheck_backup_export.py)
- [scripts/prepare_postgres_import.py](/home/charles/dikong-tech-design/scripts/prepare_postgres_import.py)
- [scripts/verify_and_restore.py](/home/charles/dikong-tech-design/scripts/verify_and_restore.py)
- [scripts/show_migration_state.py](/home/charles/dikong-tech-design/scripts/show_migration_state.py)

## 0. 先看清楚这几条

1. 只迁数据库里的业务数据和账号数据，不迁本地文件。
2. 不执行 `createsuperuser`、`create_business_admin_account`、`seed_role_permissions` 这类初始化命令。
3. 不改现有业务账号、管理员账号和密码。
4. `POSTGRES_PASSWORD` 是 PostgreSQL 数据库用户密码，不是应用登录密码。
5. 迁移脚本按“分段执行”设计，不是一键到底。每段成功后再执行下一段。

## 1. 进入项目目录

```bash
cd /path/to/dikong-tech-design
source .venv/bin/activate
```

## 2. 第一段：停写入、备份 SQLite、导出数据

这一步会做下面几件事：

- 停掉当前 `runserver` 进程
- 停掉 `run_dji_sync_scheduler` 进程
- 备份 `db.sqlite3` 及其 WAL / journal 文件
- 导出当前业务数据到 JSON
- 在备份目录里生成 `migration_state.json`

先看一下将要执行什么：

```bash
python scripts/precheck_backup_export.py --dry-run
```

确认没问题后正式执行：

```bash
python scripts/precheck_backup_export.py
```

默认备份目录会落在 `/tmp/dikong/<timestamp>`。

执行成功后，脚本会打印一个 `backup_dir=...`。后面两段都要用这个目录。

如果你想自己指定备份目录或 PostgreSQL 端口，也可以：

```bash
python scripts/precheck_backup_export.py \
  --backup-dir "/tmp/dikong/migrate-001" \
  --postgres-port 15432
```

如果想查看这次迁移保存的状态文件：

```bash
python scripts/show_migration_state.py \
  --backup-dir "/tmp/dikong/migrate-001" \
  --summary
```

## 3. 第二段：确保 PostgreSQL 容器存在并导入数据

这一步会做下面几件事：

- 检查 `docker` 是否可用
- 确保 PostgreSQL 容器存在并可启动
- 若容器不存在，则自动创建
- 执行 Django `migrate`
- 执行 `loaddata`
- 执行 `sqlsequencereset`

先看一下将要执行什么：

```bash
python scripts/prepare_postgres_import.py \
  --backup-dir "/tmp/dikong/你的备份目录" \
  --dry-run
```

正式执行：

```bash
python scripts/prepare_postgres_import.py \
  --backup-dir "/tmp/dikong/你的备份目录"
```

脚本会提示输入 PostgreSQL 密码。
如果你明确要重建 PostgreSQL 容器，可以加 `--recreate-container`，但这只适合你确认容器里没有要保留的数据时使用。

## 4. 第三段：校验数据并恢复服务

这一步分成两个模式。

### 4.1 只校验，不启动 Django

这会：

- 从 PostgreSQL 再导出一份数据
- 与第一段导出的 SQLite JSON 做比对

```bash
python scripts/verify_and_restore.py \
  --backup-dir "/tmp/dikong/你的备份目录" \
  --mode verify
```

### 4.2 校验通过后，按 PostgreSQL 启动 Django

这会：

- 先执行上面的数据比对
- 比对通过后，用 PostgreSQL 环境变量后台启动 `runserver`
- 把输出写到备份目录里的 `runserver.log`

```bash
python scripts/verify_and_restore.py \
  --backup-dir "/tmp/dikong/你的备份目录" \
  --mode start-postgres \
  --host 0.0.0.0 \
  --port 8000
```

成功后，脚本会打印新的 `runserver pid` 和日志文件路径。

## 5. 回滚

如果你已经完成了前两段，但还没接受 PostgreSQL 上的新写入，可以恢复回 SQLite：

```bash
python scripts/verify_and_restore.py \
  --backup-dir "/tmp/dikong/你的备份目录" \
  --mode rollback \
  --host 0.0.0.0 \
  --port 8000
```

这会：

- 把备份目录里的 SQLite 文件恢复回项目目录
- 用 SQLite 环境重新后台启动 `runserver`

重要提醒：如果 PostgreSQL 已经开始接收新的业务写入，回滚回 SQLite 就不再是零损失了。这个时候不要直接回滚，先确认是否接受丢掉切换后的新增数据。

## 6. 脚本边界

这 3 个脚本只覆盖你当前这种场景：

- 宿主机直接跑 `python manage.py runserver`
- SQLite -> Docker PostgreSQL
- 同一个仓库内原地迁移

它们不会替你处理这些事情：

- `systemd` / `gunicorn` / `supervisor` 服务管理
- Nginx / 反向代理配置
- 外部 PostgreSQL 实例
- 双写、零停机切换
- 迁移本地文件

如果后面你的服务启动方式变了，这套脚本也要跟着调整。当前阶段它们就是为“现网手动 runserver 切库”准备的最小闭环。
