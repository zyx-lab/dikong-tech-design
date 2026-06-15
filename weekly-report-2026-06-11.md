# 周报 - 2026-06-11

## 本周概览

**一句话总结：** 本周围绕 1 个活跃仓库推进，重点是功能开发、问题修复、测试补强，并结合 10 个活跃会话还原需求背景。

**详细信息：**

- 统计窗口：最近 7 天
- 跟踪仓库数：1，其中活跃仓库数：1
- 归并后的开发事项数：12，涉及提交数：14
- 活跃会话数：10
- 本周主线：功能开发、问题修复、测试补强
- 具体产出：修复 v2 drone status sync；新增 structured external call logging；Refactor v2 pilot profiles into IAM account profiles
这周事情不算单一，前后大致围绕梳理接口、数据流和业务对象之间的关系、把服务部署起来，并打通访问链路、排查运行过程里的问题和异常，以及追查无人机直播链路和前端取流方式在推进。
从 Git 看，真正落到代码上的主要是修复 v2 drone status sync、新增 structured external call logging、Refactor v2 pilot profiles into IAM account profiles，以及推进 add v2 DJI camera controls。

## 按仓库/分支的变更记录

**一句话总结：** 本周提交集中在 dikong-tech-design，共归并出 12 个开发事项。

**详细信息：**

### dikong-tech-design

- 仓库路径：`/home/charles/dikong-tech-design`
- 分支 `feat/dji-v2-platforms`
- 事项：修复 v2 drone status sync；时间：2026-06-09 16:47 到 2026-06-09 16:47
- 变更范围：`DEPLOY.md, apps/api_v2, apps/inspection_v2, apps/resource_v2`
- 关键文件：`views.py, tests.py, DEPLOY.md, docs_metadata.py`
- 说明：修复 v2 drone status sync，主要涉及 apps/resource_v2, apps/api_v2, apps/inspection_v2
- 事项：新增 structured external call logging；时间：2026-06-09 20:03 到 2026-06-09 20:03
- 变更范围：`apps/access, apps/api_v2, apps/dji_cloud, config/settings.py`
- 关键文件：`external_call_logging.py, request_logging.py, storage_backends.py, test_external_call_logging.py`
- 说明：新增 structured external call logging，主要涉及 apps/access, apps/dji_cloud, apps/api_v2
- 分支 `refactor/remove-v1-legacy`
- 事项：Refactor v2 pilot profiles into IAM account profiles；时间：2026-06-04 19:33 到 2026-06-04 19:33，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/api_v2, apps/iam_v2, apps/inspection_v2, apps/workforce_v2`
- 关键文件：`urls.py, views.py, tests.py, models.py`
- 说明：Refactor v2 pilot profiles into IAM account profiles，主要涉及 apps/iam_v2, apps/inspection_v2, apps/workforce_v2
- 事项：推进 add v2 DJI camera controls；时间：2026-06-04 21:07 到 2026-06-04 22:04，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/api_v2, apps/dji_cloud, apps/dji_mock, apps/inspection_v2`
- 关键文件：`tests.py, views.py, docs_metadata.py, urls.py`
- 说明：推进 add v2 DJI camera controls，主要涉及 apps/inspection_v2, apps/api_v2, apps/dji_mock
- 事项：补文档expand camera action API guide；时间：2026-06-05 09:56 到 2026-06-05 10:08，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/api_v2, docs/api-v2-frontend-guide.md`
- 关键文件：`docs_metadata.py, test_schema_docs_sync.py, api-v2-frontend-guide.md, openapi_hooks.py`
- 说明：补文档expand camera action API guide，主要涉及 apps/api_v2, docs/api-v2-frontend-guide.md
- 事项：Allow v2 super admins to bind resources；时间：2026-06-05 11:53 到 2026-06-05 11:53，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/resource_v2`
- 关键文件：`services.py, tests.py`
- 说明：Allow v2 super admins to bind resources，主要涉及 apps/resource_v2
- 事项：推进 expose DJI execution refresh APIs；时间：2026-06-05 12:09 到 2026-06-05 12:09，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/api_v2, apps/inspection_v2, apps/resource_v2, docs/api-v2-frontend-guide.md`
- 关键文件：`tests.py, serializers.py, docs_metadata.py, test_schema_docs_sync.py`
- 说明：推进 expose DJI execution refresh APIs，主要涉及 apps/inspection_v2, apps/api_v2, apps/resource_v2
- 事项：Document v2 compose deployment services；时间：2026-06-05 15:21 到 2026-06-05 15:21，相关分支：`feat/dji-v2-platforms`
- 变更范围：`.gitignore, DEPLOY.md`
- 关键文件：`DEPLOY.md`
- 说明：Document v2 compose deployment services，主要涉及 .gitignore, DEPLOY.md
- 事项：修复handle DJI MQTT loop failures；时间：2026-06-06 19:46 到 2026-06-06 19:46，相关分支：`feat/dji-v2-platforms`
- 变更范围：`Stop replaying stored latest MQTT messages when a WebSocket client subscribes. Subscriptions now only acknowledge the accepted filters and stream live MQTT broadcast events, matching the realtime-only behavior., Update coverage for successful MQTT loop return values, MQTT loop connection-loss reporting, and realtime-only WebSocket subscription behavior., apps/inspection_v2, apps/resource_v2`
- 关键文件：`Stop replaying stored latest MQTT messages when a WebSocket client subscribes. Subscriptions now only acknowledge the accepted filters and stream live MQTT broadcast events, matching the realtime-only behavior., Update coverage for successful MQTT loop return values, MQTT loop connection-loss reporting, and realtime-only WebSocket subscription behavior., run_v2_dji_worker.py, tests.py`
- 说明：修复handle DJI MQTT loop failures，主要涉及 apps/inspection_v2, apps/resource_v2, Stop replaying stored latest MQTT messages when a WebSocket client subscribes. Subscriptions now only acknowledge the accepted filters and stream live MQTT broadcast events, matching the realtime-only behavior.
- 事项：新增 config；时间：2026-06-08 11:16 到 2026-06-08 11:16，相关分支：`feat/dji-v2-platforms`
- 变更范围：`config/settings.py`
- 关键文件：`settings.py`
- 说明：新增 config，主要涉及 config/settings.py
- 事项：整理remove legacy v1 API；时间：2026-06-08 13:30 到 2026-06-08 13:30，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/access, apps/dji_bff, apps/drone, apps/mission`
- 关键文件：`urls.py, tests.py, apps.py, models.py`
- 说明：整理remove legacy v1 API，主要涉及 apps/access, apps/dji_bff, apps/drone
- 事项：修复remove trailing EOF whitespace；时间：2026-06-09 11:21 到 2026-06-09 11:21，相关分支：`feat/dji-v2-platforms`
- 变更范围：`apps/access`
- 关键文件：`middleware.py`
- 说明：修复remove trailing EOF whitespace，主要涉及 apps/access


## 根据提交与会话推断的工作意图

**一句话总结：** 本周工作意图由提交和会话共同指向，既有可落地的代码变更，也有需求澄清、设计边界和调试链路的确认。

**详细信息：**

### dikong-tech-design

这周事情不算单一，前后大致围绕梳理接口、数据流和业务对象之间的关系、把服务部署起来，并打通访问链路、排查运行过程里的问题和异常，以及追查无人机直播链路和前端取流方式在推进。
比较具体的提问包括：现在不打算让直播功能和任务耦合了。可以吗、commit 当前代码。并给出详细的commit message，以及$understand-anything:understand。
从 Git 看，真正落到代码上的主要是修复 v2 drone status sync、新增 structured external call logging、Refactor v2 pilot profiles into IAM account profiles，以及推进 add v2 DJI camera controls。
- 修复 v2 drone status sync：在分支 `feat/dji-v2-platforms` 上进行稳定性修补，围绕 apps/resource_v2, apps/api_v2 目标是消除已知问题或降低故障率。关键文件包括 views.py, tests.py, DEPLOY.md。
- 新增 structured external call logging：在分支 `feat/dji-v2-platforms` 上推进新能力落地，围绕 apps/access, apps/dji_cloud 重点是把接口或业务能力补到可用状态。关键文件包括 external_call_logging.py, request_logging.py, storage_backends.py。
- Refactor v2 pilot profiles into IAM account profiles：在分支 `refactor/remove-v1-legacy` 上整理代码结构，围绕 apps/iam_v2, apps/inspection_v2 目标是降低后续开发和维护成本。关键文件包括 urls.py, views.py, tests.py。
- 推进 add v2 DJI camera controls：在分支 `refactor/remove-v1-legacy` 上推进新能力落地，围绕 apps/inspection_v2, apps/api_v2 重点是把接口或业务能力补到可用状态。关键文件包括 tests.py, views.py, docs_metadata.py。
- 补文档expand camera action API guide：在分支 `refactor/remove-v1-legacy` 上补齐验证手段，围绕 apps/api_v2, docs/api-v2-frontend-guide.md 目标是提升回归信心。关键文件包括 docs_metadata.py, test_schema_docs_sync.py, openapi_hooks.py。
- Allow v2 super admins to bind resources：在分支 `refactor/remove-v1-legacy` 上补齐验证手段，围绕 apps/resource_v2 目标是提升回归信心。关键文件包括 services.py, tests.py。
- 推进 expose DJI execution refresh APIs：在分支 `refactor/remove-v1-legacy` 上推进新能力落地，围绕 apps/inspection_v2, apps/api_v2 重点是把接口或业务能力补到可用状态。关键文件包括 tests.py, serializers.py, docs_metadata.py。
- Document v2 compose deployment services：在分支 `refactor/remove-v1-legacy` 上完善说明材料，围绕 .gitignore, DEPLOY.md 目标是让交接或使用更顺畅。关键文件包括 DEPLOY.md。
- 修复handle DJI MQTT loop failures：在分支 `refactor/remove-v1-legacy` 上进行稳定性修补，围绕 apps/inspection_v2, apps/resource_v2 目标是消除已知问题或降低故障率。关键文件包括 Stop replaying stored latest MQTT messages when a WebSocket client subscribes. Subscriptions now only acknowledge the accepted filters and stream live MQTT broadcast events, matching the realtime-only behavior., Update coverage for successful MQTT loop return values, MQTT loop connection-loss reporting, and realtime-only WebSocket subscription behavior., run_v2_dji_worker.py。
- 新增 config：在分支 `refactor/remove-v1-legacy` 上推进新能力落地，围绕 config/settings.py 重点是把接口或业务能力补到可用状态。关键文件包括 settings.py。
- 整理remove legacy v1 API：在分支 `refactor/remove-v1-legacy` 上推进新能力落地，围绕 apps/access, apps/dji_bff 重点是把接口或业务能力补到可用状态。关键文件包括 urls.py, tests.py, apps.py。
- 修复remove trailing EOF whitespace：在分支 `refactor/remove-v1-legacy` 上进行稳定性修补，围绕 apps/access 目标是消除已知问题或降低故障率。关键文件包括 middleware.py。


## 本周活跃会话摘要

**一句话总结：** 本周匹配到 10 个活跃会话，来源 codex:10，主要围绕梳理接口、数据流和业务对象之间的关系、把服务部署起来，并打通访问链路、排查运行过程里的问题和异常，以及追查无人机直播链路和前端取流方式。

**详细信息：**

- `charles/dikong-tech-design`：10 个会话，来源 codex:10 比较具体的提问包括：现在不打算让直播功能和任务耦合了。可以吗、commit 当前代码。并给出详细的commit message，以及$understand-anything:understand。

## 风险/未完成事项

**一句话总结：** 当前主要风险集中在活跃功能分支的收口、回归验证和合并前兼容性检查。

**详细信息：**

- `dikong-tech-design` 的分支 `feat/dji-v2-platforms` 本周仍有活动，当前事项是“修复 v2 drone status sync”，可能还有收尾、合并或验证工作未完成。
- `dikong-tech-design` 的事项“修复 v2 drone status sync，主要涉及 apps/resource_v2, apps/api_v2, apps/inspection_v2”带有修复或验证特征，建议继续确认是否已经稳定收口。
- `dikong-tech-design` 的分支 `feat/dji-v2-platforms` 本周仍有活动，当前事项是“新增 structured external call logging”，可能还有收尾、合并或验证工作未完成。
- `dikong-tech-design` 的分支 `refactor/remove-v1-legacy` 本周仍有活动，当前事项是“Refactor v2 pilot profiles into IAM account profiles”，可能还有收尾、合并或验证工作未完成。
- `dikong-tech-design` 的分支 `refactor/remove-v1-legacy` 本周仍有活动，当前事项是“推进 add v2 DJI camera controls”，可能还有收尾、合并或验证工作未完成。
- `dikong-tech-design` 的分支 `refactor/remove-v1-legacy` 本周仍有活动，当前事项是“补文档expand camera action API guide”，可能还有收尾、合并或验证工作未完成。
- `dikong-tech-design` 的分支 `refactor/remove-v1-legacy` 本周仍有活动，当前事项是“Allow v2 super admins to bind resources”，可能还有收尾、合并或验证工作未完成。
- `dikong-tech-design` 的分支 `refactor/remove-v1-legacy` 本周仍有活动，当前事项是“推进 expose DJI execution refresh APIs”，可能还有收尾、合并或验证工作未完成。

## 建议下周计划

**一句话总结：** 下周应同步推进功能分支收口和回归验证，避免新增能力与稳定性工作脱节。

**详细信息：**

- 对 `dikong-tech-design` 的 `feat/dji-v2-platforms` 分支继续做回归验证，确认“修复 v2 drone status sync”已经闭环。
- 继续推进 `dikong-tech-design` 的 `feat/dji-v2-platforms` 分支，围绕“新增 structured external call logging”把本周新增能力补齐到可交付状态。
- 评估 `dikong-tech-design` 的 `refactor/remove-v1-legacy` 分支是否还需要围绕“Refactor v2 pilot profiles into IAM account profiles”补文档、补测试或准备合并。
- 继续推进 `dikong-tech-design` 的 `refactor/remove-v1-legacy` 分支，围绕“推进 add v2 DJI camera controls”把本周新增能力补齐到可交付状态。
- 对 `dikong-tech-design` 的 `refactor/remove-v1-legacy` 分支继续做回归验证，确认“补文档expand camera action API guide”已经闭环。
