# media file 逻辑模型

- generated_at: 2026-03-08T11:18:06.896282Z

## 实体主表
- table: media_files
- 主键: id (BigAutoField)

## 状态机
- N/A

## 关系与约束
- flight_record -> "flight_record.FlightRecord"

## 生命周期入口
- 创建入口: N/A
- 更新入口: PATCH /api/v1/media-files/{id}

## 本轮增补（2026-03-08）
- 本轮聚焦 API: GET /api/v1/media-files/{id}
- 读取模型语义: 以 `media_files.id` 为对象标识进行单条读取，并沿 `flight_record -> mission/drone` 关系返回关联上下文。
- 可见性约束: 逻辑删除数据（`is_deleted=true`）不参与详情读取，统一按资源不存在处理。
- 权限约束: `retrieve` 与 `list` 共用 `media_file.view_media_file` 权限码，属于同一只读能力域。
- 业务码映射:
  - SUCCESS -> 对象存在且可读
  - RESOURCE_NOT_FOUND -> 对象不存在或被逻辑删除
  - PERMISSION_DENIED -> 访问者无认证或无权限

## 本轮增补（2026-03-08，迭代16）
- 本轮聚焦 API: DELETE /api/v1/media-files/{id}
- 删除模型语义: 以 `media_files.id` 定位对象，执行逻辑删除（`is_deleted=true`、`deleted_at=now`）。
- 一致性约束: 不做物理删除，保留与 `flight_record` 的追溯关系。
- 权限约束: `destroy` 走 `media_file.manage_media_file`，与 `view_media_file` 分离。
- 业务码映射:
  - SUCCESS -> 删除动作完成
  - RESOURCE_NOT_FOUND -> 目标不存在或已删除
  - PERMISSION_DENIED -> 无认证或无删除权限










## 本轮增补（2026-03-08，迭代17）
- 本轮聚焦 API: POST /api/v1/media-files
- 创建模型语义:
  - 通过媒体写模型创建 `media_files` 记录，输入字段限定在业务白名单；
  - 创建后立即进入可读状态（`is_deleted=false`），不引入额外状态机。
- 关系语义:
  - 可选关联 `flight_record`，用于把媒体记录挂接到飞行记录主链路；
  - 不改变既有 `flight_record -> media_files` 一对多关系。
- 权限语义:
  - 创建动作与删除动作同属 `media_file.manage_media_file` 写权限域。
- 业务码映射:
  - SUCCESS -> 创建成功
  - INVALID_PARAMS -> 字段缺失/字段不可写
  - PERMISSION_DENIED -> 未认证或无创建权限

## 本轮增补（2026-03-09）
- 本轮聚焦 API: PATCH /api/v1/media-files/{id}
- 更新模型语义:
  - 以 `media_files.id` 定位单条记录，对媒体元数据做局部更新；
  - 更新范围限定在 `flight_record`、媒体类型、文件名/URL、拍摄位置、拍摄时间等白名单字段。
- 一致性约束:
  - 不开放逻辑删除状态恢复，不修改 `is_deleted`、`deleted_at`；
  - `is_deleted=true` 的记录不参与更新，避免已删除数据重新暴露到活跃链路。
- 权限约束:
  - `partial_update` 走 `media_file.manage_media_file`，与创建、删除同属写权限域。
- 审计语义:
  - 成功更新后写入 `MEDIA_FILE_UPDATE`，保存 before/after 快照。
- 业务码映射:
  - SUCCESS -> 更新成功
  - INVALID_PARAMS -> 空 body / 字段不可写 / 字段校验失败
  - RESOURCE_NOT_FOUND -> 目标不存在或已逻辑删除
  - PERMISSION_DENIED -> 未认证或无更新权限

<!-- stage6_doc_sync::media_file::logical_model.md::start -->
## Stage6 本轮同步
- 业务目标: N/A
- 业务动作: N/A
- 状态机: N/A
- 业务约束: N/A
- 事件闭环: EVT-001->PATCH /api/v1/media-files/{id}
- 权限边界: 代码权限码: view_media_file (可查看媒体文件), manage_media_file (可管理媒体文件)
<!-- stage6_doc_sync::media_file::logical_model.md::end -->
