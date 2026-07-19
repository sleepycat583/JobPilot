<!--
Week3 详细规划书。
用途：沉淀 Week3 已确认决策、开放问题与任务拆分，供后续开发对齐。
调用方：当前由人工评审与后续开发任务引用，不作为运行时代码输入。
外部依赖：基于 09-求职多智能体辅助系统架构设计.md、当前仓库实现状态与已确认对话决策整理。
-->

# Week3 详细规划书

## 一、Week3已完成工作清单

1. **Task 0：依赖声明与公共 Graph fixture，已完成并已提交。**
   - `pyproject.toml`：已声明 `SQLAlchemy==2.0.51`、`alembic==1.18.5`。
   - `tests/conftest.py`：已加入 `FixtureChatModel`、`FixtureResumeStore`、`graph_test_state_sample`、`graph_test_factory`、`graph_test_graph`、`graph_test_config`、`checkpoint_graph_test_factory`。
   - `tests/unit/test_graph_fixtures.py`：新增 2 个示范测试。
   - 提交记录：`d7b73b4 (HEAD -> master) test(week3): 声明SQLAlchemy/Alembic依赖并新增公共Graph测试基线`
   - 验证结果：全量测试 `246 passed in 38.04s`。

2. **SSE 第一轮只读调研，已完成，无代码改动。**
   - `app/services/observability.py`：确认 `observe_node` 当前只写 stdout 与滚动 JSONL 文件，没有进程内事件总线、队列或 pub-sub。
   - `app/api.py`：确认 `/v1/job-analysis` 在请求内同步阻塞执行 `graph.invoke()`。
   - 已确认事实：
     - 首次任务发起与进度订阅当前耦合在同一次请求中。
     - `node_retrying` / `run_failed` / `run_completed` 目前没有统一产出点。
     - 一个 `session` 可包含多个 `thread`，仅按 `session` 订阅存在事件归属问题。

## 二、已确定的架构决策清单

1. 采用**方案 C**：新增异步启动入口；现有 `/v1/job-analysis` 保留不变，继续作为同步兼容入口。
2. 事件总线按 `thread_id` 存储，SSE 端点按文档要求基于 `session_id` 订阅，由服务端负责多路复用。
3. 需要短期**进程内内存事件缓冲**，解决“返回 ID 到 SSE 建连之间”的丢事件窗口；不引入 Redis 等新依赖。
4. 复用现有 `observe_node` 事件数据结构与 `event_id`，SSE 不另造一套事件模型。
5. SQLAlchemy 首批范围只覆盖两类：
   - Review 审计历史
   - `data/experiments.sqlite3` 迁移
   其余（面试记录、简历元数据、报告版本、`execution_events`）延后。
6. Week3 任务顺序按优先级采用：**SSE → React骨架 → SQLAlchemy → interrupt表单**。
7. 不修改 Graph 拓扑、Agent 业务逻辑、HITL/Checkpoint 恢复机制。

## 三、尚未决策的开放问题（已解决，已通过 ADR-001 冻结）

> 历史记录保留如下，当前 1-4 项均已通过 `docs/adr-001-async-task-and-sse-contract.md` 冻结，不再作为开放问题讨论。

1. 异步启动端点的具体路径命名、请求结构、响应结构与状态码。**已通过 ADR-001 冻结：新增 `POST /api/tasks`，返回 `202 Accepted` 与 `{session_id, thread_id, status:"accepted"}`。**
2. 内存事件缓冲的具体保留时长、最大条数、清理策略。**已通过 ADR-001 冻结：每个 `thread_id` 保留最近 60 秒或最近 50 条事件（取先到者）。**
3. `session -> 多 thread` 事件多路复用在当前 FastAPI + asyncio 模型下的具体实现方式与并发边界。**已通过 ADR-001 冻结：采用 `thread` 级缓冲 + `session` 级广播、正反向索引、`loop.call_soon_threadsafe(...)` 回主事件循环发布。**
4. `node_retrying` / `run_failed` / `run_completed` 是否可直接由现有 `retry_count`、`ErrorEntry`、API 500 处理和 Graph 终态派生，还是需要额外统一机制。**已通过 ADR-001 冻结：全部复用现有信号派生，不新增机制。**

## 四、剩余任务分解（按依赖顺序）

| Task | 目标 | 涉及文件/模块 | 前置 | 验收标准 |
|---|---|---|---|---|
| 1 | 冻结异步启动与 SSE 契约 ADR | `docs/`、API 设计稿 | 开放问题 1-4 经确认 | 明确路径、DTO、状态码、事件映射、缓冲与并发规则；先不实现业务代码 |
| 2 | 实现 thread 事件缓冲与 session 索引 | 新增 `app/services` 事件总线模块、单元测试 | Task 1 | 多 thread 发布 / 单 session 订阅有序；建连前事件可补发；断开清理可测；无 Redis |
| 3 | 让 observer 发布同源 SSE 事件 | `app/services/observability.py`、`app/graph/builder.py`、相关测试 | Task 2 | JSONL 行为不变；同一 `event_id` 同时进入总线；按最终确认的事件规则通过测试 |
| 4 | 实现异步启动端点与 session SSE 端点 | `app/api.py`、相关 Schema、集成测试 | Task 3 | 新入口可立即返回 ID；旧 `/v1/job-analysis` 行为保持不变；`GET /api/sessions/{session_id}/events` 返回 `text/event-stream` |
| 5 | 建立 React/Vite TypeScript 骨架 | 新增 `frontend/`、测试与构建配置、API client 骨架 | Task 1 | 可启动、可构建；能保存 `session_id/thread_id`；尚不实现业务表单 |
| 6 | 实现 React SSE 进度状态机 | 前端事件 reducer、进度视图、前端测试 | Task 4、Task 5 | 当前连接内按 thread 展示节点开始/结束/中断/失败/完成；多 thread 不串线 |
| 7 | 建立 SQLAlchemy/Alembic 基础设施 | 新增 DB engine/session、Base、migration 配置、测试 fixture | **（无强制技术依赖，可与Task 5-6并行）** | 临时 SQLite 可重复初始化；不触碰 LangGraph Checkpoint 库；迁移测试通过。**备注：经确认，Task 7与Task 6在技术上互不依赖，此前的顺序标注仅为优先级建议，不是硬性阻塞关系。当前证据：`66cbb34` 使用 SQLite batch mode 验证空库、干净已有库、重复 upgrade 和重复键失败回滚。** |
| 8 | 迁移 experiments 数据访问 | `scripts/experiment_case1.py`、repository/model、相关测试 | Task 7 | 移除脚本直接 `sqlite3` DDL/DML；实验行为兼容；测试通过。**当前证据：`171bf21` 对只读 `data/experiments.sqlite3` 按 architecture/time/id 重编号并导入，生成 source id 映射；`285ef82` 修复增量 run_index。Case2/Case3 尚无真实归档数据。** |
| 9 | 持久化 Review 审计历史 | 审计 model/repository、API resume 边界、相关测试 | Task 7 | 每次审核提交可记录 session/thread/target/action/feedback/timestamp/result；不改 HITL 语义。**当前证据：审计模型、Repository、resume 边界和合法/非法命令测试已存在；未修改 `app/graph/`。** |
| 10 | 补齐刷新恢复与 resume 幂等契约 | thread state API、resume API、集成测试 | Task 4、Task 9 | 刷新可恢复当前 interrupt；重复提交不重复执行；校验 session/thread 归属 |
| 11 | 实现 interrupt 表单 | React 低分、面试、最终核可及评价不可用 UI | Task 6、Task 10 | 四类现有 interrupt payload 均可操作；刷新可恢复；重复提交不重复执行 |
| 12 | Week3 总验收与状态文档 | 测试、README、Week3 验收说明 | Task 8-11 | SSE + React + interrupt 主链跑通；关闭前端后后端核心测试仍独立通过；准确标注未完成项 |

## 五、每个任务对应的当前状态

| Task | 状态 |
|---|---|
| Task 0 / SSE 第一轮调研 | 已完成 |
| Task 1 | 已完成（ADR-001 已冻结异步启动与 SSE 契约） |
| Task 2 | 已完成 |
| Task 3 | 已完成(有证据)：`119eb66`、`tests/unit/test_observability.py` 覆盖同源事件发布和节点生命周期 |
| Task 4 | 已完成(有证据)：`1dc798b`、`app/api.py`、`tests/integration/test_api.py` 和 `test_api_async.py`；基础端点完成，断线补发/`Last-Event-ID`未完成 |
| Task 5 | 部分完成(说明具体缺口)：`37dceea` 有 React/Vite 骨架并可构建；无前端测试，刷新恢复未完成 |
| Task 6 | 部分完成(说明具体缺口)：`frontend/src/hooks/useAgentProgress.ts` 已有事件 reducer；无前端测试，未完成多 thread 隔离和 interrupt 交互验收 |
| Task 7 | 已完成(有证据)：`66cbb34`、`migrations/versions/20260720_0002_unique_experiment_sampling_key.py`、`tests/integration/test_alembic_migrations.py`；batch mode 覆盖干净已有库和重复数据失败回滚 |
| Task 8 | 已完成(有证据)：`171bf21`、`285ef82`、`scripts/migrate_experiment_archive.py`、`docs/experiment_run_index_migration_map.md`；18 条 Case1 归档导入后按 architecture 为 1-9，源文件只读；Case2/3 尚无真实记录 |
| Task 9 | 已完成(有证据)：`08fcf8f`、`app/db/models.py`、`app/repositories/review_audit.py`、`app/api.py`及相关测试；未改 Graph/HITL 生命周期 |
| Task 10 | 未开始(无对应代码/测试)：无刷新恢复 API 与 resume 幂等独立验收证据 |
| Task 11 | 未开始(无对应代码/测试)：无四类 interrupt 表单、刷新恢复和重复提交幂等的前端证据 |
| Task 12 | 部分完成(说明具体缺口)：全量回归和状态文档已完成，但 SSE + React + interrupt 完整主链尚未跑通 |

## 六、当前说明

- 本文件为**Week3 的持续更新规划文档**，随开放问题被确认和任务推进同步更新；每次更新需要提交，保持与实际进度同步。
- 若后续开放问题被确认，应优先更新本文件，再继续实施对应任务。