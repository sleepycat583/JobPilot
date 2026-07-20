<!--
Week3 实际状态核实报告。
用途：根据提交 diff、代码/测试证据和现场命令结果，校正 week3_plan.md 与 README.md 的状态描述。
核实范围：截至 `0b3e5be`；本报告在后续 Task10 实现完成后同步状态与验证证据。
-->

# Week3 状态核实报告

## 一、核实范围与结论

初始核实基准为 `08fcf8fac191e78aba0d5d8b0bcba08e577b0f5f`。Task10 后续实现提交为 `3f0b2cc`、`32b9991`、`5a6d204`、`f601459`，收尾验证提交为 `b986f80` 与 `0b3e5be`；均未修改 `app/graph/` 业务逻辑或 Graph 拓扑。

提交改动新增了独立业务数据库基础设施：`app/db/base.py`、`app/db/engine.py`、`app/db/session.py`、`app/db/models.py`；配置中的业务库为 `sqlite:///./data/app.sqlite3`，Checkpoint 为 `./data/checkpoints.sqlite3`。业务 engine/session 与 `app/graph/checkpoint.py` 的 Checkpointer 使用不同模块、文件和连接边界；本 commit 未修改 `app/graph/`，未发现改变 Review Gate/HITL 生命周期的证据。

提交还包含 `alembic.ini`、`migrations/env.py`、`migrations/script.py.mako` 和 `migrations/versions/20260719_0001_create_business_tables.py`。`66cbb34` 新增的 batch mode revision 及 `tests/integration/test_alembic_migrations.py` 已验证全新库、干净已有库、重复 `upgrade head`、重复键失败回滚和 Checkpoint 隔离。`171bf21` 已在独立临时目标库验证真实 `data/experiments.sqlite3` 归档导入与重编号，源文件保持只读。

## 二、Task 1-12 当前真实状态

状态仅使用计划要求的三种值：`已完成(有证据)`、`部分完成(说明具体缺口)`、`未开始(无对应代码/测试)`。

| Task | 当前状态 | 证据与具体说明 |
|---|---|---|
| 1 | 已完成(有证据) | `7116dc0039f2700272decd454ab92789313b36ba`；`docs/adr-001-async-task-and-sse-contract.md` 已冻结异步启动、SSE DTO、事件映射、缓冲和并发规则。 |
| 2 | 已完成(有证据) | `3802d24f91bffd72fb52b43952f91bd7316d092a`；`app/services/event_bus.py`、`tests/unit/test_event_bus.py` 覆盖 thread 缓冲、session 索引、回放、清理和慢消费者断开。 |
| 3 | 已完成(有证据) | `119eb665ecd291cdc3ddc54f8eca51bfba88943d`；`app/services/observability.py`、`tests/unit/test_observability.py` 覆盖同源 event_id、节点生命周期、interrupt 和 retry 事件发布。 |
| 4 | 已完成(有证据) | `1dc798bc568cdb2c4a7551dc10ef59aee0e9eee0`；`app/api.py` 已有 `POST /api/tasks` 和 `GET /api/sessions/{session_id}/events`，`tests/integration/test_api.py`、`tests/integration/test_api_async.py` 覆盖 202、SSE 完成和失败事件。尚未实现断线补发/`Last-Event-ID`，因此该缺口属于后续范围，不改变本任务基础端点已完成的判断。 |
| 5 | 部分完成(说明具体缺口) | `37dceea78be26a3b1bd4ce3d2bf62e523d3df6a2`；`frontend/` React/Vite/TypeScript 骨架存在且构建可用，但没有前端测试，session/thread 仅在内存状态中保存，刷新恢复未完成。 |
| 6 | 部分完成(说明具体缺口) | `37dceea78be26a3b1bd4ce3d2bf62e523d3df6a2`；`frontend/src/hooks/useAgentProgress.ts` 与 `frontend/src/App.tsx` 可处理节点进度事件，但没有前端测试，尚未证明多 thread 隔离及完整 interrupt 交互。 |
| 7 | 已完成(有证据) | `66cbb34`、`migrations/versions/20260720_0002_unique_experiment_sampling_key.py`、`tests/integration/test_alembic_migrations.py`。SQLite batch mode 已验证干净已有数据升级后行数和版本保持正确；重复五元组时明确失败，原表数据和 revision 保留；全新库和重复 upgrade 也通过。 |
| 8 | 已完成(有证据) | `285ef82`、`171bf21`、`scripts/migrate_experiment_archive.py`、`docs/experiment_run_index_migration_map.md`、`tests/unit/test_experiment_archive_migration.py`。真实只读归档 18 行已导入独立临时目标库，baseline/multi_agent 各为 1-9 共 9 行，source id 映射可审计且源文件字节未改变；脚本后续运行从已有最大编号继续。 |
| 9 | 已完成(有证据) | `08fcf8fac191e78aba0d5d8b0bcba08e577b0f5f`；`app/db/models.py`、`app/repositories/review_audit.py`、`app/api.py` 及 `tests/unit/test_review_audit_repository.py`、`tests/integration/test_api.py`。测试覆盖合法审核写入、完成状态和非法命令不写审计；未修改 `app/graph/`。 |
| 10 | 已完成(有证据) | `3f0b2cc`、`32b9991`、`5a6d204`、`f601459`、`b986f80`、`0b3e5be`；`GET /v1/threads/{thread_id}/state` 支持刷新恢复 interrupt，resume 使用前端 UUIDv4 幂等键及业务库快照重放；同 key 重放 200、不同 key 完成态 404、key 复用 409、30 秒租约接管和线程级有效租约 409 均由 `tests/integration/test_api.py` 覆盖。前端独立 `useThreadReview` 在真实 HTTP 响应前禁用最终核可表单，刷新从 sessionStorage + state API 恢复。 |
| 11 | 部分完成(说明具体缺口) | 已实现四类前端表单、幂等键生命周期和基础测试；当前缺口为错误码差异化处理、low score cancel 可选 feedback、context-only payload 严格移除 `answer` 字段，需后续独立提交修复验证。 |
| 12 | 部分完成(说明具体缺口) | 当前全量测试基线已更新且核心 Week3 代码存在，但原计划的 SSE + React + interrupt 完整主链尚未跑通，且本报告正用于补齐状态文档。证据：`08fcf8fac191e78aba0d5d8b0bcba08e577b0f5f`、本报告及下述测试结果。 |

## 三、数据库与 Review 风险

1. **提交说明风险**：`08fcf8f` 标题未按 §19.2 记录三点变更说明。该问题属于提交记录合规性，不代表发现了架构阻塞。
2. **历史实验数据范围**：Task 8 已验证 Case1 归档导入与重编号；Case2/Case3 尚无真实实验记录。
3. **Alembic 验证边界**：空库初始化、业务表创建、Checkpoint 不触碰、已有干净业务数据 upgrade、重复 upgrade 和重复键失败回滚均已验证，适用 §16.6 完成版模板。
4. **Review Gate 冻结检查**：本 commit 未触碰 `app/graph/`，未发现修改已冻结 Review Gate 业务逻辑的证据；当前不标记为“需要人工确认是否违反 §19 Architecture Freeze”。审计写入位于 `app/api.py` 的 resume 边界，Graph 仍接收原有 `Command(resume=...)`。
5. **物理隔离**：业务 SQLite 使用 `data/app.sqlite3`，Checkpoint 使用 `data/checkpoints.sqlite3`，相关代码和连接生命周期独立；`tests/unit/test_db_engine.py::test_database_and_checkpoint_paths_must_be_isolated` 与迁移集成测试提供证据。
6. **实验范围限制**：当前归档的 18 条真实 LLM 记录全部属于 `case1_simple_python_backend_jd`；Case2（跨语言简历匹配）和 Case3（复杂多轮模拟面试）没有真实 LLM 实验记录，18 条不能被表述为三 Case 对比实验已完成。映射证据见 `docs/experiment_run_index_migration_map.md`。

## 四、测试基线

执行环境：`D:\求职助手\.venv\Scripts\python.exe`，项目根目录为 `D:\求职助手`，`job-assistant` 已以 editable 方式安装。

```text
295 passed in 36.43s
```

使用项目 `.venv` 的 `python -m pytest -q` 已通过 295 个测试，无 failed、无 skipped。Task10 收尾的聚焦验证为 `python -m pytest tests/integration/test_api.py tests/unit/test_resume_idempotency_repository.py -q`，通过 46 个测试，包含 `test_resume_with_new_key_reclaims_expired_thread_lease_and_marks_old_record` 与 `test_resume_with_new_key_rejects_active_thread_lease`。Task11 已引入 Vitest + React Testing Library；`npm --prefix frontend run test -- --reporter=dot` 通过 6 个测试文件、17 个用例，`npm --prefix frontend run build` 通过。测试文件为 `App.test.tsx`、`hooks/useThreadReview.test.tsx`、`components/ThreadReviewPanel.test.tsx`、`components/LowScoreReviewForm.test.tsx`、`components/InterviewAnswerForm.test.tsx`、`components/EvaluationUnavailableForm.test.tsx`。

## 五、后续核实范围

- Task 8：Case1 归档导入和编号映射已验证；Case2/Case3 的真实实验记录仍需 Week4 规划与执行。
- Task 11：已完成前端表单与组件测试；后续仅可补充真实后端联调或端到端证据，不改变本任务完成状态。
- SSE：当前仅宣称基础实时事件流；断线补发和 `Last-Event-ID` 仍按架构文档 §16.5 保持未完成表述。