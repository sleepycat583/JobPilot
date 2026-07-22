# 前后端对接开发规划书

> 依据：当前 `frontend/src/App.tsx`、现有前端 Hook/组件、`app/api.py`、冻结 Schema 与 Week2/Week3 文档。本文只描述已注册接口和已冻结契约；未在后端注册的能力明确标为缺口，不补造接口。

## 0. 规划范围与对接原则

- 前端仍采用现有 React + Vite 与既有 `useAgentProgress`、`useThreadReview`、`ThreadReviewPanel` 等模块，不新增依赖。
- `/api/*` 是异步任务与 SSE 进度边界；`/v1/*` 是本项目在 `app/api.py:create_app()` 中显式注册的同步分析、Checkpoint 查询与 HITL 恢复边界。`/v1/threads/*` **不是** LangGraph 自动路由。
- 所有真实请求统一保留 `X-Session-ID`：首次可省略，由后端生成 UUIDv4；后续任务使用同一会话 ID。恢复请求必须新生成 UUIDv4 `idempotency_key`，禁止用本地状态假装 Graph 已恢复。
- 接口失败统一读取 `{ "error": { "code": string, "message": string } }`。HTTP 422 是输入或 HITL 命令校验失败，404 是线程/Checkpoint 不存在，409 是恢复请求处理中或幂等键冲突，500 是图或审计基础设施失败。

## 1. JD 解析对接（JD Analysis）

### 1.1 阶段目标与前置条件

将 `App.tsx:mockAnalyzeJob()` 的 JD mock 替换为真实启动、结果读取和最终审核。先完成第 3 章的任务/线程状态通道，才能在页面中正确处理异步审批。

### 1.2 涉及的真实接口

| 路径与方法 | 请求 | 成功响应 | 说明 |
| --- | --- | --- | --- |
| `POST /v1/job-analysis` | Header `X-Session-ID` 可选 UUIDv4；JSON `{ jd_text, resume_version? }`。`jd_text` 去空白后必须为 20 至 `MAX_INPUT_LENGTH` 字符；禁止额外字段。 | `200`：`thread_id`、`session_id`、`jd_parsed`、`match_result`、`interview_state`、`review_status`、`review_target`、`current_node`、`execution_history`、`error_log`、`final_output`、`status`（`completed`/`interrupted`）及可选 `interrupt`。 | 同步执行。仅做 JD 时不传 `resume_version`；后端将输入构造成“请解析以下岗位要求”。 |
| `GET /v1/threads/{thread_id}/state` | 路径 `thread_id`。 | `200`：`thread_id`、`session_id`、`status`（`interrupted`/`completed`）、`review_status`、`review_target`、`current_node`、`interrupt`（或 `null`）。 | 用于刷新后重建审核表单；不返回完整 `jd_parsed`。 |
| `POST /v1/threads/{thread_id}/resume` | 推荐 JSON `{ idempotency_key: UUIDv4, command: {...} }`。JD 最终审核命令为 `{ type: "final_review", action: "approve" }`，或 `{ type: "final_review", action: "reject", feedback: 非空字符串 }`。 | `200`：与 `POST /v1/job-analysis` 相同的完整状态响应。 | 后端以当前 Checkpoint 的 interrupt 校验命令类型，命令的 `type` 可由后端从 interrupt 补全，但前端应显式发送。 |

`JDParsed` 的真实字段为：`job_title`、`seniority`、`company_name`、`responsibilities`、`skills[{ name, category, priority, evidence }]`、`experience_requirements`、`education_requirements`、`interview_focus`、`company_context`、`ambiguities`、`source_language`。

### 1.3 对应的前端改动点

- `frontend/src/App.tsx`：删除 `defaultResult` 和 `mockAnalyzeJob()`；将 `analyze()` 拆为启动/读取状态的调用点。`JDResult` 补充渲染真实 `JDParsed` 中已定义但当前 mock 丢失的字段。
- `frontend/src/types.ts`：复用并补充 API DTO，避免 `App.tsx` 内重复声明缩减版 `JDParsed`。
- `frontend/src/hooks/useThreadReview.ts`：作为审核状态和 `resume` 请求的唯一入口；入口组件重新接入该 Hook 与 `ThreadReviewPanel`。
- `frontend/src/components/ThreadReviewPanel.tsx`、`LowScoreReviewForm.tsx`：JD `final_review` 使用现有审核组件，驳回必须保留反馈输入和禁用重复提交。

### 1.4 状态流转说明

冻结 Review 生命周期：`pending -> in_review -> approved`，或 `pending -> in_review -> rejected -> revising -> pending -> in_review`。

| 页面状态 | 真实来源 | 当前 mock | 对接补充 |
| --- | --- | --- | --- |
| `loading/running` | 请求未完成或 SSE `node_started` | 已有 `loading`，但仅定时器 | 以请求和事件驱动，保留错误重试。 |
| `awaiting_approval` | `status=interrupted` 且 `interrupt.type=final_review`、`target=jd_parsed` | 仅本地 `review` | 由 Checkpoint 状态渲染；刷新后重新获取状态。 |
| `approved` | `resume approve` 的 200 响应 | 已有 `completed` 本地切换 | 只接受后端响应，不直接置完成。 |
| `rejected/revising` | `resume reject` 后端图执行 | “驳回并重试”直接重新 mock | 必传反馈；显示后端重算过程与下一次审核。 |

### 1.5 联调验证方式

1. 仅提交合法 JD、不选简历，确认返回 `jd_parsed` 且 UI 显示所有字段。
2. 用少于 20 字符、空白、超长 JD 分别验证 `INPUT_TOO_SHORT`、`INPUT_EMPTY`、`INPUT_TOO_LONG` 的错误展示。
3. 触发 `final_review` 后刷新页面，调用线程状态接口并确认仍显示相同审核表单。
4. 点击批准，确认请求携带新 UUIDv4 幂等键，最终 UI 来自 200 响应；点击驳回但不填反馈，应在前端阻止提交，绕过前端时应收到 `HITL_COMMAND_INVALID`。

### 1.6 风险与依赖

- 同步接口不提供 SSE 过程，若目标是原型中的实时进度，应采用第 3 章的异步接口。
- `GET /v1/threads/{thread_id}/state` 不返回完整 JD；刷新后的结果显示需保留最近成功完整响应，或在接口层确认是否需要报告查询端点。当前不能臆造该端点。

## 2. 简历匹配对接（Resume Matching）

### 2.1 阶段目标与前置条件

使用已获选的 `resume_version` 发起组合分析，并正确处理匹配结果、低分 Gate 和最终审核。依赖第 1 章的 JD 结果展示和第 3 章的线程恢复能力。

### 2.2 涉及的真实接口

| 路径与方法 | 请求 | 成功响应 | 说明 |
| --- | --- | --- | --- |
| `POST /v1/job-analysis` | `{ jd_text, resume_version }`；Header `X-Session-ID`。 | 同步完整状态响应。`match_result` 可能为空或为对象。 | `resume_version` 存在时后端构造“先分析 JD，再匹配指定简历”的任务。 |
| `POST /api/tasks` | 与上行相同。 | `202`：`session_id`、`thread_id`、`status: "accepted"`。 | 推荐用于带进度的匹配。 |
| `POST /v1/threads/{thread_id}/resume` | 低分命令：`{ type: "low_match_score", action: "continue"|"revise_inputs"|"cancel", feedback?, resume_version?, jd_text? }`；`revise_inputs` 至少给出非空反馈、简历版本或 JD 之一。最终核可仍使用 `final_review` 命令。外层同样使用 `idempotency_key`。 | `200` 完整状态响应。 | 当前 Checkpoint 只接受其 `accepted_actions` 内的操作。 |

`MatchResult` 的真实字段为：`total_score`、`dimension_scores`、`matched_items[{ requirement, status, score, evidence[{ chunk_id, quote, relevance }], rationale }]`、`strengths`、`gaps`、`recommendations`、`low_score_review_required`、`resume_version`。不可用时为 `MatchUnavailableResult`：`status: "MATCH_UNAVAILABLE"`、`resume_version`、`retrieval_evidence[{ requirement, evidence[] }]`、`message`。

### 2.3 对应的前端改动点

- `frontend/src/App.tsx`：`resumes` 仍可先保留展示数据，但不得把它当作后端简历库；`MatchResultView` 需使用真实 `matched_items`、证据引用、维度分数和 `MATCH_UNAVAILABLE` 分支。
- `frontend/src/components/LowScoreReviewForm.tsx`：接入实际 `low_match_score` interrupt，传递选择的新 `resume_version`/`jd_text` 和反馈。
- `frontend/src/components/ThreadReviewPanel.tsx`：按 `interrupt.type` 分派低分表单与最终核可表单，而非由 `App.tsx` 自行切换 `State`。

### 2.4 状态流转说明

正常路径：`pending -> running -> final_review(match_result) -> approved`，或 `... -> rejected -> revising -> pending`。

低分路径：`pending -> running -> low_match_score -> continue -> final_review`；或 `low_match_score -> revise_inputs -> revising -> pending`；或 `low_match_score -> cancel -> completed/cancelled`。冻结规则为 `total_score < 60.0` 必须进入低分 Gate，`60.0` 不进入低分 Gate 但仍须最终核可。

当前 mock 只显示固定 `82` 分，未实现低分、`MATCH_UNAVAILABLE`、证据空结果、`continue/revise_inputs/cancel` 和匹配最终审核。

### 2.5 联调验证方式

1. 选择有索引版本并提交，核对 `resume_version` 回显与 `match_result.matched_items[].evidence[].chunk_id/quote`。
2. 用后端 fixture 触发低于 60 分，确认显示 `top_gaps`、三种动作及对应的命令载荷。
3. 用空检索场景验证 `RAG_EMPTY_RESULT`/不可用结果不会被渲染成 0 分成功匹配。
4. 复用同一幂等键重复提交，确认返回缓存响应；同键换命令确认收到 `IDEMPOTENCY_KEY_REUSED`。

### 2.6 风险与依赖

- 当前仓库未发现“上传简历/列举简历版本”的 HTTP 路由；左侧简历库无法在本阶段完成真实数据对接，需要后端另行提供冻结契约。
- UI 必须避免由 `index_status` 推断检索可用性，实际可用性以后端 `match_result`/错误响应为准。

## 3. 任务/线程路由状态显示对接（Task & Thread State）

### 3.1 阶段目标与前置条件

以异步任务、SSE 和 Checkpoint 状态替换工作台右栏的静态进度、伪 `thread_id` 和本地审核状态；该阶段为前三阶段的共同基础。

### 3.2 接口分层与路由性质说明（`/api` 与 `/v1`）

- `POST /api/tasks` 和 `GET /api/sessions/{session_id}/events` 是本项目异步执行与事件订阅接口：前者立即受理，后者以 `text/event-stream` 推送图节点事件。
- `POST /v1/job-analysis` 是本项目同步分析接口；`GET/POST /v1/threads/*` 是 `app/api.py` 明确实现的 Checkpoint 查询/HITL 恢复接口，内部调用 `graph.get_state()` 和 `Command(resume=...)`，不是 LangGraph Server 自动路由。

### 3.3 涉及的真实接口

| 路径与方法 | 请求 | 成功响应 | 说明 |
| --- | --- | --- | --- |
| `POST /api/tasks` | Header `X-Session-ID` 可选；JSON `{ jd_text, resume_version? }`。 | `202`：`{ session_id, thread_id, status: "accepted" }`。 | 后台启动图。 |
| `GET /api/sessions/{session_id}/events` | `session_id` 路径参数。浏览器 `EventSource` 自动使用最后事件 ID 重连。 | SSE：`id` 为 `event_id`，`event` 为事件名，`data` 至少包含 `timestamp,event,event_id,session_id,thread_id,node,node_kind,node_run_id,started_at,ended_at,duration_ms,input_summary,success,error_code`；按事件还可能有 `detail`、`attempt`、`message`、`raw_output_excerpt`、`error_entry`。 | 前端已有 `useAgentProgress` 与 `AgentEvent` 类型可复用。 |
| `GET /v1/threads/{thread_id}/state` | 路径参数。 | 线程状态 DTO：`thread_id`、`session_id`、`status`、`review_status`、`review_target`、`current_node`、`interrupt`。 | 刷新恢复。 |
| `POST /v1/threads/{thread_id}/resume` | `{ idempotency_key, command }`。 | `200` 完整状态响应；见第 1、2、4 章命令。 | 409 表示仍在处理或幂等冲突。 |

### 3.4 对应的前端改动点

- `frontend/src/App.tsx:analyze()`：以 `POST /api/tasks` 替代 mock；保存真实 `session_id/thread_id`，删除硬编码 footer。
- `frontend/src/hooks/useAgentProgress.ts`：接入入口，负责 SSE 生命周期、`lastEventId`、当前节点、完成节点和 `run_failed`。
- `frontend/src/hooks/useThreadReview.ts`：在取得 thread 后加载状态，处理刷新、resume、错误与重试。
- `frontend/src/types.ts`：作为唯一的任务事件、状态、interrupt 与 command 契约定义；删除 `App.tsx` 的重复简化状态类型。

### 3.5 状态流转说明

任务：`idle -> submitting -> accepted -> running -> interrupted | completed | failed`；恢复：`interrupted -> resuming -> running -> interrupted | completed | failed`。

映射规则：`node_started` 更新 `currentNode`；`node_finished` 追加完成节点；`interrupt_required` 后读取线程状态并渲染对应表单；`run_resumed` 进入 `resuming/running`；`run_completed` 完成；`run_failed` 显示可重试错误。页面刷新：保存 `thread_id/session_id` 后先读取线程状态，若中断必须恢复同一表单。

当前 mock 只有 `review/loading/completed/error/empty`，没有 `accepted`、`running` 事件序列、`interrupted/resuming/failed`、断线重连、`Last-Event-ID`、刷新恢复或重复恢复冲突处理。

### 3.6 联调验证方式

1. 启动异步任务，确认收到 202 后进度按 SSE 节点事件更新，且 `thread_id` 与事件一致。
2. 在审核 interrupt 页面刷新，确认通过线程状态接口复现表单，不重新创建任务。
3. 断开网络后恢复，确认事件不重复、进度不倒退；服务端补发能力以 `event_id` 检查。
4. 触发 `run_failed`、404、409、500，确认错误码可见、按钮恢复可操作，且 409 不重复发送命令。

### 3.7 风险与依赖

- 现有 `useAgentProgress` 的请求路径、SSE 错误恢复和 `useThreadReview` 的状态 URL 必须逐一与本章接口统一，禁止保留旧路径混用。
- 生产反向代理必须允许 SSE、关闭响应缓冲；这是架构文档的部署依赖，不由 React 组件解决。

## 4. 模拟面试对接（Mock Interview）

### 4.1 阶段目标与前置条件

把当前“功能开发中”替换为可恢复的题目回答、上下文补充、评价失败处理和报告最终审核。依赖第 3 章线程与 SSE 能力。

### 4.2 涉及的真实接口

| 路径与方法 | 请求 | 成功响应 | 说明 |
| --- | --- | --- | --- |
| `POST /api/tasks` 或 `POST /v1/job-analysis` | 当前公开请求体只有 `{ jd_text, resume_version? }`。 | 分别为 202 受理 DTO 或 200 完整状态响应。 | 当前 API 没有“显式启动模拟面试”的独立路径或请求字段；只能由 Supervisor 根据输入语义路由，需先确认产品入口如何表达该意图。 |
| `GET /v1/threads/{thread_id}/state` | `thread_id`。 | 线程状态与 `interrupt`。 | 面试刷新恢复依赖该接口。 |
| `POST /v1/threads/{thread_id}/resume` | 外层 `{ idempotency_key, command }`。`interview_answer`：`submit_answer` 需非空 `answer`，`context_update` 需非空 `context`，或 `end_interview`。`interview_evaluation_unavailable`：`retry_evaluation`/`skip_evaluation`。报告审核：`final_review` 的 approve/reject。 | `200` 完整状态响应。 | 命令类型由当前 interrupt 限制。 |

面试 interrupt：`interview_answer` 为 `{ type, target: "interview_state", question_id, question, accepted_actions }`；评价不可用为 `{ type: "interview_evaluation_unavailable", target: "question_record", question_id, accepted_actions }`；最终报告为 `final_review` 且 `target: "interview_report"`，携带 `draft`。

### 4.3 对应的前端改动点

- `frontend/src/components/InterviewAnswerForm.tsx`：接入真实 `interview_answer` 命令与 `isResuming` 禁用逻辑。
- `frontend/src/components/EvaluationUnavailableForm.tsx`：接入真实评价重试/跳过命令。
- `frontend/src/components/ThreadReviewPanel.tsx`：基于 `ThreadInterrupt` 分派所有四类面试/审核表单。
- `frontend/src/App.tsx`：移除“功能开发中”的伪入口，只有确认了启动意图的 API 契约后才显示可操作入口。

### 4.4 状态流转说明

面试状态：`planning -> asking -> waiting(interview_answer interrupt) -> evaluating -> asking` 循环；结束后 `completed -> final_review(interview_report) -> approved`，或 `rejected -> revising -> pending`。评价异常为 `evaluating -> interview_evaluation_unavailable -> retry_evaluation -> evaluating`，或 `skip_evaluation -> asking/completed`。

当前 mock 完全未覆盖上述状态；已有表单组件与单测覆盖部分提交校验，但入口未挂载，且没有真实面试启动动作。

### 4.5 联调验证方式

1. 先与后端确认可复现的面试路由输入，再启动线程并确认收到 `interview_answer`。
2. 提交空回答和空上下文，验证前端与后端都拒绝；提交回答后确认下一题或评价阶段事件。
3. 人为使评价不可用，验证重试和跳过均使用新幂等键且可刷新恢复。
4. 结束面试后验证报告进入 `final_review`；驳回复盘不得重新提问或改写历史题答记录。

### 4.6 风险与依赖

- **后端缺口：**当前 HTTP 请求 Schema 中没有 `task_type`、`action` 或 `interview_config`，也没有独立的“启动模拟面试”端点。必须先冻结由 `jd_text` 语义路由还是新增业务请求契约，才能完成真实入口。
- `GET /v1/threads/{thread_id}/state` 不返回完整 `interview_state`，刷新后题目内容依赖 interrupt payload；历史题答展示需要额外查询契约或保留状态策略，当前不得假设存在。

## 5. 当前已知缺口

### 5.1 Mock 层未拆分的技术债

- `frontend/src/App.tsx` 同时包含 `Resume`、缩减版 `JDParsed`/`MatchResult`、`defaultResult`、`resumes` 和 `mockAnalyzeJob()`，没有独立 mock service、fixture 或 API client。
- 当前 `JDParsed` mock 少于后端字段，且 `MatchResult` 缺少 `dimension_scores`、`matched_items` 与证据；替换时不应继续依赖该缩减类型。

### 5.2 TODO 标注与待确认接口

- 唯一 TODO 是 `App.tsx:mockAnalyzeJob()`，把 `/api/tasks`、SSE 与线程状态写在同一处，未分别标记具体的 resume、state 和错误码处理位置。
- 左侧简历上传、列表、索引状态不存在已注册 API；需要后端先提供接口与 Schema。
- 模拟面试没有显式启动接口或启动字段；需要先冻结入口契约。

### 5.3 前端测试未覆盖的状态场景

- `App.test.tsx` 只覆盖初始渲染、切换匹配标签与本地批准；未覆盖 mock error（`[error]`）、空 JD、上传空状态、驳回重试和 loading 完成。
- 未覆盖 202/SSE、`run_failed`、断线补发、刷新恢复、四类 interrupt、404/409/422/500、低分三动作、`MATCH_UNAVAILABLE` 和幂等键重放。
- `useAgentProgress`、`useThreadReview`、各 HITL 表单已有独立测试，但当前工作台入口没有集成这些模块，缺少端到端组件级覆盖。

### 5.4 后端接口或契约缺口

- 真实简历库管理接口未在 `app/api.py` 注册。
- 真实模拟面试启动入口未在 `JobAnalysisRequest` 或路由中显式表达。
- 线程状态 DTO 仅用于恢复判断，不含完整业务产物；刷新后结果展示的持久化读取策略需在联调前明确。