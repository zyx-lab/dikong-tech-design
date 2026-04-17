# 宿主机 Django 切换到 Docker PostgreSQL 操作手册

适用场景：Django 运行在宿主机，PostgreSQL 运行在 Docker 容器里。目标是把现有 SQLite 里的业务数据完整迁移到 PostgreSQL，不丢数据。整个过程会短暂停机。

下面的命令按顺序执行。每做完一步，再看下一步。

## 0. 先看清楚这几条

1. 导出 SQLite 数据之前，不要把 `DB_ENGINE` 改成 `postgres`。
2. `POSTGRES_PASSWORD` 只在 PostgreSQL 数据卷第一次初始化时生效。
3. 如果 5432 端口已经被占用，就把下面所有 `5432` 改成一个新端口，比如 `15432`，并且 Django 也要用同一个新端口。
4. 如果 `docker` 命令报权限问题，前面加 `sudo` 再试一次。
5. 这份手册只迁数据库里的数据，不迁本地文件。

## 1. 先进入项目目录

把下面这行里的路径改成你服务器上的实际项目路径。

```bash
cd /path/to/dikong-tech-design
```

## 2. 停掉 Django 和后台写入任务

先确认当前有哪些相关进程：

```bash
ps -ef | grep -E 'gunicorn|manage.py runserver|run_dji_sync_scheduler' | grep -v grep
```

如果有输出，先停掉它们：

```bash
pkill -f "manage.py runserver" || true
pkill -f gunicorn || true
pkill -f "run_dji_sync_scheduler" || true
```

再确认一次，应该没有输出：

```bash
ps -ef | grep -E 'gunicorn|manage.py runserver|run_dji_sync_scheduler' | grep -v grep
```

如果你是用 `systemd` 启动的，把上面的 `pkill` 换成你自己的停止命令，例如：

```bash
sudo systemctl stop <你的服务名>
```

## 3. 备份 SQLite 文件

先准备一个放备份的目录。这个目录放在项目外面，避免误提交。

```bash
export BACKUP_DIR="$HOME/dikong-backup/$(date +%F_%H%M%S)"
mkdir -p "$BACKUP_DIR"
```

把 SQLite 主文件和可能存在的辅助文件一起备份：

```bash
for f in db.sqlite3 db.sqlite3-wal db.sqlite3-shm db.sqlite3-journal; do
  [ -f "$f" ] && cp -a "$f" "$BACKUP_DIR/"
done

ls -lh "$BACKUP_DIR"
```

## 4. 导出 SQLite 里的业务数据

这一条命令会把业务应用的数据导成 JSON 文件。请在 **还没有切到 PostgreSQL** 之前执行。

```bash
DB_ENGINE=sqlite .venv/bin/python manage.py dumpdata \
  access drone drone_assignment route waypoint mission flight_record media_file dji_bff \
  --indent 2 \
  > "$BACKUP_DIR/business-data.json"
```

如果以后你新增了业务 app，就把 app 名字也加到这条命令里。

## 5. 启动 PostgreSQL 容器

先输入数据库密码。请记住这个密码，后面 Django 也要用同一个密码。

```bash
read -s -p "请输入 PostgreSQL 密码: " POSTGRES_PASSWORD; echo
```

再设置容器变量：

```bash
export POSTGRES_CONTAINER=dikong-postgres
export POSTGRES_DB=dikong
export POSTGRES_USER=postgres
export POSTGRES_PORT=5432
```

如果你怀疑 5432 可能被占用，可以先看一下：

```bash
ss -ltnp | grep ":${POSTGRES_PORT} " || true
```

如果 5432 已经被占用，就把 `POSTGRES_PORT` 改成 `15432`，然后后面的命令里也要一起改。

启动容器前，先清掉同名旧容器，不要删数据卷，除非你明确要重来：

```bash
docker rm -f "$POSTGRES_CONTAINER" 2>/dev/null || true
docker volume create dikong_pgdata >/dev/null
```

启动 PostgreSQL：

```bash
docker run -d \
  --name "$POSTGRES_CONTAINER" \
  --restart unless-stopped \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -p 127.0.0.1:${POSTGRES_PORT}:5432 \
  -v dikong_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine
```

确认数据库已经起来：

```bash
docker logs --tail 50 "$POSTGRES_CONTAINER"
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" -i "$POSTGRES_CONTAINER" \
  psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1;"
```

看到 `SELECT 1` 正常返回后，再继续。

## 6. 把 Django 切到 PostgreSQL

从这一步开始，后面的 `manage.py` 命令都要连 PostgreSQL。

```bash
export DB_ENGINE=postgres
export DB_HOST=127.0.0.1
export DB_PORT="$POSTGRES_PORT"
export DB_NAME="$POSTGRES_DB"
export DB_USER="$POSTGRES_USER"
export DB_PASSWORD="$POSTGRES_PASSWORD"
```

先让 Django 创建 PostgreSQL 里的表结构：

```bash
.venv/bin/python manage.py migrate
```

再检查一下配置有没有问题：

```bash
.venv/bin/python manage.py check
```

如果这一步报错，先不要继续，先修好报错再往下做。

## 7. 把 SQLite 数据导入 PostgreSQL

把刚才导出的 JSON 文件导入 PostgreSQL：

```bash
.venv/bin/python manage.py loaddata "$BACKUP_DIR/business-data.json"
```

如果这里报 `IntegrityError`、`foreign key`、`relation already exists` 之类的错误，先停下来，不要硬往下走。

## 8. 重置自增序列

导入完数据后，PostgreSQL 的自增序列要重新对齐，不然下一次插入可能撞主键。

```bash
.venv/bin/python manage.py sqlsequencereset \
  access drone drone_assignment route waypoint mission flight_record media_file dji_bff \
  > "$BACKUP_DIR/reset_sequences.sql"
```

把 SQL 执行到 PostgreSQL 容器里：

```bash
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" -i "$POSTGRES_CONTAINER" \
  psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < "$BACKUP_DIR/reset_sequences.sql"
```

如果 `reset_sequences.sql` 是空文件，也可以继续，说明这些表没有需要重置的序列。

## 9. 核对数据是否一致

最稳妥的办法是把 PostgreSQL 再导出一次，然后和 SQLite 的导出结果直接比对。

```bash
.venv/bin/python manage.py dumpdata \
  access drone drone_assignment route waypoint mission flight_record media_file dji_bff \
  --indent 2 \
  > "$BACKUP_DIR/business-data-postgres.json"

if diff -u "$BACKUP_DIR/business-data.json" "$BACKUP_DIR/business-data-postgres.json" > "$BACKUP_DIR/data-diff.txt"; then
  echo "OK: SQLite 和 PostgreSQL 的导出结果一致"
else
  echo "FAIL: 两边导出结果不一致，先不要启动服务。"
  echo "请查看差异文件：$BACKUP_DIR/data-diff.txt"
  exit 1
fi
```

如果这里通过，说明业务数据已经对齐。

## 10. 重启 Django

如果你是手动启动 Django，就在同一个终端里继续启动：

```bash
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

如果你是用 `gunicorn` 或 `systemd` 启动的，把同样的 `DB_*` 环境变量写回你的启动配置，然后重启原来的服务。

重启后，打开下面这个地址看一下：

```bash
curl -I http://127.0.0.1:8000/api/v1/docs/
```

能返回正常响应，说明 Django 已经成功连上 PostgreSQL。
如果你的 Django 不是跑在 8000 端口，把这个 URL 里的端口改成你自己的端口。

## 11. 回滚方案

如果切换后发现问题，而且 PostgreSQL 还没有接收新的业务写入，可以直接回滚回 SQLite。

先停掉 Django：

```bash
pkill -f "manage.py runserver" || true
pkill -f gunicorn || true
pkill -f "run_dji_sync_scheduler" || true
```

把数据库环境改回 SQLite：

```bash
unset DB_ENGINE
unset DB_HOST
unset DB_PORT
unset DB_NAME
unset DB_USER
unset DB_PASSWORD
```

把原来的 SQLite 文件恢复回来：

```bash
for f in db.sqlite3 db.sqlite3-wal db.sqlite3-shm db.sqlite3-journal; do
  [ -f "$BACKUP_DIR/$f" ] && cp -a "$BACKUP_DIR/$f" "$PWD/"
done
```

然后用原来的方式重新启动 Django。

重要提醒：如果 PostgreSQL 已经开始接收新的写入，回滚回 SQLite 就不再是零损失了。这个时候不要随便回滚，先确认是否接受丢掉切换后的新增数据。

## 12. 常见问题

1. `database is locked` 还在出现，通常是旧的 Django 进程没停干净，或者你其实还在连 SQLite。先回到第 2 步和第 6 步检查。
2. `password authentication failed`，通常是 `POSTGRES_PASSWORD` 没写对，或者 Django 没用同一个密码。
3. `could not connect to server`，通常是 PostgreSQL 容器没起来，或者端口映射写错了。
4. `relation already exists`，通常是你在旧的 PostgreSQL 数据卷上重复初始化了。只有在你明确要重来时，才执行：

```bash
docker rm -f "$POSTGRES_CONTAINER" || true
docker volume rm dikong_pgdata
```

然后从第 5 步重新开始。
