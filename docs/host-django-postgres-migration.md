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

如果你就是在现网服务器上手工执行迁移，建议直接固定一组值，避免每一步都临时判断：

- 备份目录固定用 `/tmp/dikong/migrate-now`
- PostgreSQL 宿主机端口优先用 `15432`，不要默认抢占 `5432`
- Django 继续使用你原来对外提供服务的端口，例如 `8010`

先看一下宿主机上 `5432` 和 `15432` 有没有被占用：

```bash
ss -ltnp | rg ':5432\b|:15432\b' || true
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | rg 'dikong-postgres|5432|15432' || true
```

如果 `5432` 已经被占用，最稳的处理方式不是去停别人的服务，而是后面的迁移步骤统一改用 `15432`。

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

### 2.1 现网推荐执行方式

如果你当前服务就是手工运行的 `python manage.py runserver`，而且之前已经遇到过 `5432` 端口冲突，建议直接执行下面这组命令：

```bash
docker rm -f dikong-postgres || true

pkill -f 'manage.py runserver' || true
pkill -f 'run_dji_sync_scheduler' || true
sleep 2

pgrep -af 'manage.py runserver' || true
pgrep -af 'run_dji_sync_scheduler' || true
```

如果上面两条 `pgrep` 还有输出，说明仍然有写入进程没退出。先手工结束对应 PID，再继续：

```bash
kill -9 <PID>
```

然后正式执行第一段，显式把 PostgreSQL 端口定成 `15432`：

```bash
python scripts/precheck_backup_export.py \
  --backup-dir /tmp/dikong/migrate-now \
  --postgres-port 15432
```

执行后建议立刻确认状态文件：

```bash
python scripts/show_migration_state.py \
  --backup-dir /tmp/dikong/migrate-now \
  --summary
```

确认输出里有：

```text
postgres_port=15432
```

如果你已经执行过第一段，但当时写入的是 `5432`，也可以直接重新执行这一段并保持同一个 `--backup-dir`，把状态文件更新为 `15432`。在仍然停写的前提下，这样做比手工编辑 `migration_state.json` 更稳。

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

### 3.1 现网推荐执行方式

```bash
python scripts/prepare_postgres_import.py \
  --backup-dir /tmp/dikong/migrate-now \
  --postgres-password '你自己设置的PG密码'
```

这里的密码是 PostgreSQL 容器里的数据库密码，不是 Django 管理员密码，也不是业务账号密码。第一次成功创建容器时用什么密码，后面校验和启动 Django 时就继续用同一个。

如果这一步仍然报宿主机端口冲突：

- 先执行 `docker rm -f dikong-postgres || true`
- 再检查 `15432` 是否也被占用
- 如果 `15432` 也被占用，就回到第一段，把 `--postgres-port` 改成另一个空闲端口，例如 `25432`

示例：

```bash
python scripts/precheck_backup_export.py \
  --backup-dir /tmp/dikong/migrate-now \
  --postgres-port 25432
```

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

现网推荐命令：

```bash
python scripts/verify_and_restore.py \
  --backup-dir /tmp/dikong/migrate-now \
  --postgres-password '同一个PG密码' \
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

如果你当前对外服务原本就是跑在 `8010`，那就保持 `8010` 不变，只把底层数据库换成 PostgreSQL：

```bash
python scripts/verify_and_restore.py \
  --backup-dir /tmp/dikong/migrate-now \
  --postgres-password '同一个PG密码' \
  --mode start-postgres \
  --host 0.0.0.0 \
  --port 8010
```

启动后可以直接看日志：

```bash
tail -f /tmp/dikong/migrate-now/runserver.log
```

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

## 6. 常见问题：`5432` 端口冲突怎么处理

典型报错包括：

- `port is already allocated`
- `address already in use`
- `failed to bind host port 127.0.0.1:5432/tcp`

这类错误说明问题出在宿主机端口冲突，不在 PostgreSQL 镜像本身。合理处理顺序是：

1. 查清楚是谁占用了 `5432`
2. 如果那不是你这次迁移必须复用的实例，就不要动它
3. 把这次迁移的 PostgreSQL 宿主机端口改成 `15432` 或其他空闲端口
4. 用同一个 `--backup-dir` 重跑第一段，让 `migration_state.json` 记录新的端口
5. 再继续第二段和第三段

排查命令：

```bash
ss -ltnp | rg ':5432\b|:15432\b' || true
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | rg '5432|15432|dikong-postgres' || true
```

不推荐默认去强行停掉现有 `5432` 的占用者。对现网来说，单独为这次迁移使用一个新的宿主机端口通常更安全。

## 7. 脚本边界

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
