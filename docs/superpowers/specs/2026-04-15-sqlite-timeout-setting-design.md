# 2026-04-15 SQLite Timeout Setting Design

## 1. 背景

当前项目默认数据库是 SQLite，配置位于 [`config/settings.py`](/home/charles/dikong-tech-design/config/settings.py)。

最近线上已经出现过典型的 SQLite 锁竞争报错：

- `django.db.utils.OperationalError: database is locked`

现状里，SQLite 分支只配置了：

- `ENGINE = django.db.backends.sqlite3`
- `NAME = BASE_DIR / "db.sqlite3"`

没有显式配置连接等待时间。这意味着遇到短时写锁竞争时，Django 会较快失败并抛出 `database is locked`，进而把业务请求打成 500。

当前目标很明确：

- 不重构数据库访问链路
- 不引入新的数据库基础设施
- 只在 settings 里给 SQLite 增加一个更宽松的等待时间，降低短时锁竞争直接打爆接口的概率

## 2. 目标

1. 仅对 SQLite 默认数据库增加 `timeout` 配置
2. 将等待时间设置为 20 秒
3. PostgreSQL 分支保持不变
4. 改动面只限于 settings

## 3. 非目标

1. 不引入 WAL 模式
2. 不增加 `PRAGMA busy_timeout` 连接钩子
3. 不增加环境变量配置项
4. 不修改事务边界或 ORM 调用方式
5. 不承诺彻底消除 SQLite 锁竞争

## 4. 方案对比

### 4.1 方案 A：直接在 SQLite 分支写死 `timeout = 20`

做法：

1. 仅修改 SQLite 分支
2. 新增：

```python
"OPTIONS": {
    "timeout": 20,
}
```

优点：

1. 改动最小
2. 与当前诉求完全一致
3. 不引入新的配置面

缺点：

1. 超时时间固定写死

### 4.2 方案 B：通过环境变量控制超时时间

做法：

1. 新增类似 `SQLITE_TIMEOUT_SECONDS` 的环境变量
2. settings 里给默认值 20

优点：

1. 部署时更灵活

缺点：

1. 当前没有真实需求
2. 会增加配置复杂度
3. 属于 YAGNI

### 4.3 方案 C：同时引入 WAL、busy timeout 和其他 SQLite 策略

优点：

1. 理论上能进一步缓解锁冲突

缺点：

1. 超出当前需求
2. 风险面更大
3. 不适合这次只想快速止损的目标

### 4.4 选型结论

采用方案 A。

原因：

1. 满足当前需求
2. 改动最小
3. 风险最低
4. 符合 KISS 和 YAGNI

## 5. 设计

### 5.1 修改位置

仅修改 [`config/settings.py`](/home/charles/dikong-tech-design/config/settings.py) 的 SQLite 分支。

当前：

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
```

调整后：

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 20,
        },
    }
}
```

### 5.2 行为预期

当 SQLite 遇到短时锁竞争时：

1. 连接最多等待 20 秒
2. 若锁在等待窗口内释放，请求继续执行
3. 若超过 20 秒仍无法获取锁，仍然会报错

因此，这次调整的准确语义是：

- 降低短时锁冲突导致的直接失败概率
- 不是从根本上消除 SQLite 的并发写限制

### 5.3 影响范围

只影响：

1. `DB_ENGINE != postgres` 时的默认数据库连接行为

不影响：

1. PostgreSQL 配置
2. 业务模型
3. API 契约
4. 数据迁移结构

## 6. 验证

这次改动的验证标准很简单：

1. settings 成功加载
2. `python manage.py check` 不报配置错误
3. SQLite 分支的 `DATABASES["default"]["OPTIONS"]["timeout"] == 20`

## 7. 风险与边界

### 7.1 这不是根治方案

如果业务里存在：

1. 长事务
2. 高频并发写
3. 同一个 SQLite 文件被多个进程长期争用

那么仅增加 timeout 只能缓解，不能根治。

### 7.2 这次不顺手扩展

本次明确不做以下延伸：

1. 不加 WAL
2. 不加 `busy_timeout` pragma 初始化
3. 不新增环境变量
4. 不调整事务拆分

如果后面仍持续出现锁竞争，再单独开一个 spec 讨论更进一步的 SQLite 策略或数据库切换。
