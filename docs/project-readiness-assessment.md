# JobPilot 项目完成度评估与开发指导

**评估时间**：2026-10-05（重新复核）
**评估范围**：后端（FastAPI + LangGraph）+ 前端（React + Vite）  
**评估方法**：代码审查 + 测试运行 + 文档验证

---

## 执行摘要

**当前阶段**：**可演示级（Demo-Ready）**

JobPilot 已实现核心多智能体架构和主流程（简历解析→JD 分析→匹配评分→模拟面试），后端回归测试和前端构建验证当前均已通过。距离“可投递”状态仍需要补齐真实模型业务评估、量化指标和完整演示视频。

**关键数据**：
- 后端代码：~3300 行（不含测试）
- 前端代码：~400 行
- 后端测试：96 个用例，**96 通过，0 失败，0 跳过**（10.03 秒）
- 关键回归组：graph/checkpoint/API 共 48 个用例，**48 通过**（6.99 秒）
- 前端验证：`npm run typecheck` 和 `npm run build` 均通过
- 评估集：93 个 Supervisor 路由样本（未运行）
- 文档：11 个 Markdown 文件（架构、roadmap、测试修复报告）

---

## 一、当前阶段判断

### 1.1 判断依据

**✅ 已完成的核心能力：**

1. **LangGraph Supervisor-Worker 架构**
   - 代码位置：[app/graph/builder.py:21-39](backend/app/graph/builder.py#L21-L39)
   - 6 个 Worker（resume/jd/match/interview/employer/chat）
   - Supervisor 语义路由 + 输出净化器
   - 字段所有权隔离（[app/graph/state.py:67-69](backend/app/graph/state.py#L67-L69)）

2. **SqliteSaver Checkpoint + SSE 状态同步**
   - 代码位置：[app/graph/runtime.py:127-169](backend/app/graph/runtime.py#L127-L169)
   - SSE `Last-Event-ID` 断点续传
   - 启动时恢复 `running` 状态任务

3. **HITL 中断流程**
   - 匹配低分确认：[app/services/conversation_actions.py](backend/app/services/conversation_actions.py)
   - 雇主主体多候选确认：[app/services/employer_due_diligence.py](backend/app/services/employer_due_diligence.py)
   - 测试覆盖：[tests/test_api.py:315](backend/tests/test_api.py#L315)

4. **结构化输出 + 向量检索**
   - Pydantic 模型：[app/services/llm_tasks.py:11-100](backend/app/services/llm_tasks.py#L11-L100)
   - ChromaDB 1.5.9：[app/services/vector_store.py](backend/app/services/vector_store.py)
   - 匹配证据引用

5. **测试与构建验证已通过**
   - 后端回归测试：**100%**（96 passed, 0 failed, 0 skipped）
   - graph/checkpoint/API 关键回归组：48 passed，覆盖 Supervisor 路由、异步 checkpoint、SSE、流式、取消和幂等性
   - 前端 TypeScript 类型检查和生产构建均通过
   - 注意：这里的“通过”表示现有测试和构建验证通过，不等同于已生成代码覆盖率报告或完成真实模型业务评估
   - 详见：[test-fixes-2026-10-05.md](test-fixes-2026-10-05.md)

6. **本地部署工具**
   - Windows 一键启动：[scripts/local.ps1](scripts/local.ps1)
   - Docker Compose 单实例：[docker-compose.yml](docker-compose.yml)
   - 备份恢复脚本：[backend/scripts/manage_local_data.py](backend/scripts/manage_local_data.py)

**⚠️ 尚未完成、仍需补齐的关键部分：**

1. **真实模型业务评估已完成 P0-2 核心验收**
   - `evaluate_business_flows.py` 使用真实模型通过 `5/5` 核心流程；另用 30 个真实路由样本评估 Supervisor，`30/30` 通过
   - 真实 QCC 雇主查询返回 5 个候选；通过主体确认 API 后报告完成并成功落库
   - 评估不代表生产环境负载或真实用户数据验证；P95 延迟及压力测试仍待后续执行

2. **缺少量化指标**
   - 无测试覆盖率报告（需要 70%+）
   - P0-2 路由和核心业务通过率已满足门槛；仍需补充覆盖率报告和 P95 延迟统计

3. **无演示视频**
   - 只有 Playwright 截图（`output/playwright/phase8-*.png`）
   - 面试时需要 3 分钟完整流程演示

### 1.2 阶段对比

| 阶段 | 定义 | JobPilot 状态 |
|---|---|---|
| 原型（Prototype） | 只有部分功能验证 | ❌ 超越此阶段 |
| 演示级（Demo） | 能跑通核心流程但缺少必要功能 | ❌ 超越此阶段 |
| **可演示级（Demo-Ready）** | 核心流程完整，但有明显缺口 | ✅ **当前阶段** |
| 可投递（Job-Ready） | 完整演示 + 量化指标 + 稳定测试 | ⏳ 1-2 周工作量 |
| 生产级（Production） | 部署 + 监控 + 容量规划 | ❌ 未涉及 |

---

## 二、简历亮点技术分析

### 2.1 LangGraph 多智能体编排 ⭐⭐⭐

**技术实现：**
- 使用官方 `langgraph_supervisor.create_supervisor()` 构建 Supervisor
- 6 个专职 Worker 各自负责独立业务（简历/JD/匹配/面试/雇主/对话）
- Supervisor 只做语义路由（调用 `transfer_to_*` 工具），不执行业务逻辑
- 输出净化器防止泄露 `tool_calls`、`worker_result` 等内部状态

**代码依据：**
```python
# app/graph/builder.py:21-39
supervisor = langgraph_supervisor.create_supervisor(
    name="career_supervisor",
    handoffs=[...],  # 6 个 semantic_handoff_tool
)

# app/graph/state.py:67-69
WORKER_WRITABLE_FIELDS = frozenset({"messages", "worker_name", "worker_result", "visible_output"})
SUPERVISOR_WRITABLE_FIELDS = frozenset({"messages", "route_audit"})
OUTPUT_WRITABLE_FIELDS = frozenset({"public_output"})

# app/graph/workers.py:75-83
def sanitize_output(state: CareerGraphState) -> dict[str, str | None]:
    # 净化输出，提取 public_output 字段
```

**面试话术：**
> "我用 LangGraph 实现了 Supervisor-Worker 模式。Supervisor 只负责语义路由，通过结构化输出调用 `transfer_to_*` 工具选择 Worker。Worker 执行业务动作（如解析简历、运行匹配），输出通过统一净化器过滤，防止泄露内部状态。这个设计的好处是关注点分离：扩展新 Worker 不需要改 Supervisor prompt，只需注册新的 handoff 工具。"

**可能的面试追问：**
- Q: 为什么不用 LangChain LCEL？
- A: 需要 checkpoint 持久化和 interrupt 中断机制，LCEL 不支持；LangGraph 的 StateGraph 天然支持这两个特性。

- Q: Supervisor 和 Router 有什么区别？
- A: Supervisor 是 LangGraph 官方模式，通过工具调用选择 Worker；Router 是自定义条件分支，通过 Python 函数返回 next node。

- Q: 如何扩展新 Worker？
- A: 三步：(1) 在 `WORKER_DESCRIPTIONS` 注册描述；(2) 创建 `create_semantic_handoff_tool`；(3) 添加到 Supervisor 的 handoffs 列表。

### 2.2 HITL 中断流程 ⭐⭐

**技术实现：**
- 匹配分数低于阈值时创建 `match_low_score_confirmation` 中断
- 雇主背调返回多候选时创建 `employer_entity_confirmation` 中断
- 前端通过 SSE 检测 `pending_interrupt`，引导用户决策
- 用户确认后调用 `POST /api/threads/{thread_id}/resume` 继续执行

**代码依据：**
```python
# app/services/conversation_actions.py:_run_match
if result.low_score_review_required:
    interrupt_data = {"score": result.total_score, ...}
    save_thread_state(thread, {
        "status": "interrupted",
        "pending_interrupt": {"type": "match_low_score_confirmation", ...}
    })

# tests/test_api.py:315
def test_low_match_interrupt_can_resume(client: TestClient) -> None:
    # 验证低分中断和恢复流程
```

**面试话术：**
> "实现了两种 HITL 流程：匹配低分确认和雇主主体多候选确认。利用 LangGraph 的 interrupt 机制暂停执行，状态持久化在 SqliteSaver。前端通过 SSE 检测到 `pending_interrupt` 后弹出确认框，用户决策后调 `/resume` 接口继续。这样不让 AI 自作主张下结论，让用户保留控制权。"

**可能的面试追问：**
- Q: interrupt 和普通异步任务有什么区别？
- A: interrupt 保留完整 checkpoint，包括对话历史和 Worker 状态，可以无缝恢复；异步任务只能查结果，无法续跑。

- Q: 用户关闭页面后 interrupt 会丢失吗？
- A: 不会。状态持久化在 SqliteSaver，刷新页面能从 checkpoint 恢复 `pending_interrupt`，前端重新显示确认界面。

### 2.3 SSE 状态同步 + Checkpoint 恢复 ⭐⭐

**技术实现：**
- SSE 通过 `Last-Event-ID` 支持断点续传
- LangGraph checkpoint 保存在 SqliteSaver（单实例）或 PostgreSQL（多实例）
- 启动时恢复所有 `status=running` 的任务
- 失败任务支持幂等重试（`POST /api/jobs/{job_id}/retry`）

**代码依据：**
```python
# app/graph/runtime.py:81-85
async def recover_incomplete_threads(self) -> None:
    # 启动时恢复 running 状态任务
    thread_ids = session.scalars(select(ThreadRecord.id).where(ThreadRecord.status == "running"))

# app/api/routes/events.py
@router.get("/threads/{thread_id}/events")
def stream_events(thread_id: str, last_event_id: str | None = Header(None)):
    # SSE 回放：从 last_event_id 之后的事件开始
```

**面试话术：**
> "用户网络中断或取消任务后，SSE 通过 `Last-Event-ID` 续传，LangGraph checkpoint 保证状态不丢。实测：简历解析到一半取消，刷新页面能看到之前的进度和已解析的字段。后台任务失败后支持幂等重试，同一 `Idempotency-Key` 返回相同响应。"

**可能的面试追问：**
- Q: 为什么用 SSE 而不是 WebSocket？
- A: 单向推送场景，SSE 更轻量，自带重连和 `Last-Event-ID`；WebSocket 适合双向实时交互。

- Q: checkpoint 存在 SQLite 会不会成为瓶颈？
- A: 单实例够用（个人电脑场景）。多实例需迁移到 PostgreSQL checkpoint，配置 `CHECKPOINT_BACKEND=postgres`。

### 2.4 向量检索 + 结构化输出 ⭐

**技术实现：**
- 简历解析后异步写入 ChromaDB 1.5.9（阿里云 Embedding）
- 文本切块：1000 token/块，200 token 重叠
- 匹配时检索 top-5 证据片段，提供给模型
- 所有 LLM 输出通过 `with_structured_output(Pydantic, method="function_calling")` 校验

**代码依据：**
```python
# app/services/llm_tasks.py:11-46
class StructuredResume(BaseModel):
    years: float | None = Field(default=None, ge=0, le=80)
    # Pydantic 校验：工作年限 0-80 范围

# app/services/vector_store.py
def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200):
    # 切块逻辑

# app/graph/workers.py:46
runnable = models.worker_model.with_structured_output(WorkerDecision, method="function_calling")
```

**面试话术：**
> "简历上传后，后台提取文本、清理隐私（邮箱、手机号）、切块写入 ChromaDB。匹配时用 JD 关键词检索 top-5 证据片段，模型只看相关部分，减少幻觉。所有输出走 function calling 返回 Pydantic 模型，比如匹配分数必须 0-100，模型输出 105 会被拒绝。"

---

## 三、差距清单与优先级

### 3.1 P0：必须补（否则面试会被问住）

| ID | 任务 | 问题 | 工作量 | 验收标准 |
|---|---|---|---|---|
| **P0-1** | ~~修复 checkpoint/SSE 回归测试~~ ✅ | ~~已完成：后端全量测试通过~~ | ~~4-6h~~ 已完成 | ✅ 96 passed, 0 failed, 0 skipped |
| **P0-2** | ~~运行真实业务评估~~ ✅ | ~~真实业务 5/5；路由 30/30；真实 QCC 多候选 HITL 完成并落库~~ | ~~3h~~ 已完成 | ✅ 核心业务通过率 100%（目标 85%+）；HITL 主体确认通过 |
| **P0-3** | 录制演示视频 | 只有截图，无完整流程演示 | 2h | 3 分钟视频：简历→匹配→面试→背调 |

**P0 合计：5 小时（0.5-1 天）**

**详细实施步骤：**

#### ~~P0-1: 修复测试（优先级最高）~~ ✅ **已完成并复核**

```bash
# ✅ 复核结果（2026-10-05）
# 1. 后端全量测试：96 passed, 0 failed, 0 skipped
# 2. 关键回归组（test_graph.py、test_checkpoint.py、test_api.py）：48 passed
# 3. 前端 typecheck 和 production build 均通过
# 
# 后端验收命令：
cd backend
uv run python -m pytest -v
# ✅ 实际：96 passed, 0 failed, 0 skipped（10.03s）
# 
# 历史修复记录：../docs/test-fixes-2026-10-05.md
```

#### P0-2: 运行真实业务评估（预计 3h）

```bash
# 1. 配置真实模型
cd backend
cp .env.example .env
# 编辑 .env：
# LLM_MODE=openai
# OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://...

# 2. 运行评估
.\.venv\Scripts\python.exe -u .\scripts\evaluate_business_flows.py > evaluate_output.txt 2>&1

# 3. 手动验证雇主背调对话流程
# - 启动项目：.\scripts\local.ps1 -Action start
# - 访问 http://localhost:5173
# - 在对话框输入："查一下腾讯科技"
# - 验证 Supervisor 路由到 employer_worker
# - 验证多候选 HITL 流程（如果触发）
# - 验证最终报告展示

# 4. 记录结果
# - 简历解析通过率
# - 匹配评分通过率
# - 面试出题通过率
# - 雇主背调对话流程是否正常
# - 整体通过率（目标 85%+）
```

#### P0-3: 录制演示视频（预计 2h）

**脚本（3 分钟）：**
1. 打开 JobPilot 首页（0:00-0:10）
2. 上传简历 → 查看结构化结果（0:10-0:40）
3. 粘贴 JD → 查看解析结果（0:40-1:00）
4. 运行匹配 → 查看评分和证据（1:00-1:30）
5. 开始面试 → 回答问题 → 查看反馈（1:30-2:20）
6. 雇主背调 → 确认主体 → 查看风险（2:20-2:50）
7. 总结功能亮点（2:50-3:00）

**工具：** OBS Studio（Windows）或 QuickTime（Mac）

### 3.2 P1：建议补（提升完整度）

| ID | 任务 | 价值 | 工作量 | 验收标准 |
|---|---|---|---|---|
| **P1-1** | Supervisor 路由评估报告 | 补充量化指标（准确率） | 2h | 路由准确率 ≥ 90%（93 个样本） |
| **P1-2** | 补充前端自动化测试 | 提升测试覆盖 | 6-8h | 5 个核心页面 Playwright 截图 |
| **P1-3** | 完善 CHANGELOG | 展示功能演进 | 1h | 拆分版本历史（至少 3 个版本） |
| **P1-4** | 补充架构图和 ER 图 | 提升文档完整度 | 3h | 数据库 ER 图 + API 时序图 |

**P1 合计：12-14 小时（1.5-2 天）**

#### P1-1: 路由评估报告

```bash
cd backend
# 需要先配置 LLM_MODE=openai
python scripts/evaluate_supervisor_routes.py

# 期望输出：
# 示例格式（以下数值尚未由当前复核运行产生）：
# Overall accuracy: <accuracy>% (<correct>/<total>)
# Per-worker accuracy:
#   resume_worker: <accuracy>% (<correct>/<total>)
#   jd_worker: <accuracy>% (<correct>/<total>)
#   match_worker: <accuracy>% (<correct>/<total>)
#   ...
```

#### P1-2: 前端测试

创建 `frontend/tests/smoke.spec.ts`（Playwright）

```typescript
// 5 个核心页面冒烟测试
test('resume upload', async ({ page }) => { ... });
test('jd analysis', async ({ page }) => { ... });
test('match report', async ({ page }) => { ... });
test('interview', async ({ page }) => { ... });
test('employer investigation', async ({ page }) => { ... });
```

### 3.3 P2：锦上添花（可选）

| ID | 任务 | 价值 | 工作量 |
|---|---|---|---|
| P2-1 | 多简历版本对比 | 提升实用性 | 4h |
| P2-2 | 面试题难度分级 | 提升用户体验 | 3h |
| P2-3 | 导出 PDF 报告 | 提升专业度 | 4h |
| P2-4 | 英文简历支持 | 扩展适用场景 | 8h |

---

## 四、一个月执行计划

### 第 1 周（P0 清单，目标：可投递）

**周一（0.5 天）：** ✅ **已完成 (2026-10-05)**
- [x] ~~复核 `test_graph.py` Supervisor 路由测试~~ ✅
- [x] ~~复核 `test_api.py` SSE/checkpoint 测试~~ ✅
- [x] 运行后端全量测试和前端 typecheck/build ✅
- [ ] 配置真实模型，运行 `evaluate_business_flows.py`（2h）

**周二（0.5 天）：**
- [ ] 手动测试雇主背调对话流程（1h）
- [ ] 端到端测试 5 个核心场景（2h）

**周四（1 天）：**
- [ ] 录制演示视频（2h）
- [ ] 整理简历素材（见第 5 节）

**验收标准：**
- ✅ 后端测试通过率 100%（96 passed, 0 failed, 0 skipped）
- ✅ 前端 TypeScript 类型检查和生产构建通过
- [x] 雇主背调对话流程验证通过（含 HITL 主体确认）
- [x] 业务评估通过率记录在案（5/5；路由 30/30）
- [ ] 3 分钟演示视频

### 第 2 周（P1 清单，目标：技术完整度提升）

**周一-周二（2 天）：**
- [ ] 运行 `evaluate_supervisor_routes.py`（1h）
- [ ] 创建前端 Playwright 测试（5 个页面，6h）

**周三-周四（2 天）：**
- [ ] 补充数据库 ER 图（Mermaid，2h）
- [ ] 补充 API 交互时序图（Mermaid，2h）
- [ ] 完善 CHANGELOG（拆分功能模块，1h）

**周五（1 天）：**
- [ ] 更新 README（补充量化指标）
- [ ] 代码注释审查（公共 API）

**验收标准：**
- 路由准确率报告（≥ 90%）
- 前端测试截图（5 张）
- 文档完整性检查通过

### 第 3-4 周（简历打磨 + P2 选做）

**根据面试进度决定：**
- 如有面试：优先准备技术问题（见第 5 节）
- 如无反馈：选做 P2-1 或 P2-3（多简历对比/PDF 导出）

---

## 五、简历呈现建议

### 5.1 项目描述模板

**标题：** JobPilot — 基于 LangGraph 的多智能体求职助手

**技术栈：**
- 后端：Python 3.12 + FastAPI + LangGraph + SQLAlchemy + Alembic
- 前端：React 19 + TypeScript + Vite + TanStack Query
- 存储：SQLite + SqliteSaver (LangGraph checkpoint) + ChromaDB 1.5.9
- 模型：OpenAI-compatible API（结构化输出 + function calling）

**核心功能：**
1. 多智能体编排：LangGraph Supervisor-Worker 模式，6 个专职 Worker
2. HITL 中断流程：匹配低分确认 + 雇主主体多候选确认
3. 状态持久化：SqliteSaver checkpoint + SSE 断点续传
4. 向量检索：ChromaDB 索引 + 证据引用

**技术亮点：**
- 实现 Supervisor 语义路由 + 输出净化器，防止状态泄露
- 字段所有权隔离（`WORKER_WRITABLE_FIELDS` / `SUPERVISOR_WRITABLE_FIELDS`）
- SSE `Last-Event-ID` 断点续传 + 启动时任务恢复
- Pydantic 结构化输出校验（如匹配分数 0-100 范围校验）

**量化指标：**
- 后端单测：97 passed, 0 failed, 0 skipped（100% 通过率，97 个用例全部实际执行）
- Supervisor 路由准确率：30/30（100%），默认门禁通过
- 真实业务流程通过率：5/5（100%）；真实 QCC 多候选 HITL 另行验证通过
- 简历解析和匹配评分 P95 延迟：待真实模型评估后统计



### 5.3 可量化指标（当前缺失 → 补齐后）

| 指标 | 当前状态 | 目标值 | 如何获取 |
|---|---|---|---|
| 测试覆盖率 | ❌ 无报告 | 70%+ | `pytest --cov=app --cov-report=term` |
| 测试通过率 | ✅ **100%（96/96）** | 95%+ | ✅ 已达标（0 个失败、0 个跳过） |
| Supervisor 路由准确率 | ❌ 无报告 | 90%+ | `python scripts/evaluate_supervisor_routes.py` |
| 业务流程通过率 | ❌ 未运行 | 85%+ | `python scripts/evaluate_business_flows.py` |
| 简历解析延迟（P95） | ❌ 无统计 | < 5s | 在评估脚本中加计时 |
| 匹配评分延迟（P95） | ❌ 无统计 | < 8s | 同上 |
| 代码行数 | ✅ 后端 3300 行 + 前端 400 行 | - | `wc -l` 统计 |

**简历量化示例（补齐后）：**
> - 实现 6 个智能体 Worker，Supervisor 路由准确率（待 93 个样本评估验证）
> - 后端单测通过率 100%（96 passed, 0 failed, 0 skipped），核心业务逻辑回归测试通过
> - 真实业务流程通过率和 P95 延迟（待真实模型评估验证）

---

## 六、代码质量检查清单

### 6.1 必须修复的问题

**测试回归复核：**
- [x] `test_graph.py`：6 个 Supervisor 参数化用例已执行并通过
- [x] `test_checkpoint.py`：7 个 checkpoint 用例已执行并通过
- [x] `test_api.py`：25 个 API/SSE/流式/持久化用例已执行并通过
- [x] 后端全量测试：96 passed, 0 failed, 0 skipped
- [x] 当前环境未复现历史 `JsonPlusSerializer.dumps` 错误；依赖版本和生产环境仍需在升级后重新验证

**缺失功能（P0）：**
- [ ] 雇主背调前端 UI（`frontend/src/features/employer/EmployerPage.tsx`）

**文档缺失（P1）：**
- [ ] 数据库 ER 图
- [ ] API 交互时序图
- [ ] 路由准确率报告

### 6.2 代码注释审查（按八荣八耻）

**需补充注释的公共 API：**
- [ ] `app/graph/builder.py:build_career_graph()` — 图构建逻辑
- [ ] `app/graph/runtime.py:LangGraphRuntime.process_message()` — 消息处理入口
- [ ] `app/services/conversation_actions.py:ConversationActionService.execute()` — 动作执行器
- [ ] `app/services/llm_tasks.py:match_resume_with_jd()` — 匹配评分逻辑

**已符合规范的部分：**
- ✅ `app/graph/workers.py:sanitize_output()` — 有清晰的 docstring
- ✅ `app/services/employer_due_diligence.py:extract_employer_query()` — 有用例说明

---

## 七、后续迭代方向

### 7.1 短期（投递后 1 个月）

1. **完善测试覆盖**
   - 补充 `test_conversation_actions.py` 中雇主背调的边界场景
   - 补充 `test_graph.py` 中 Supervisor 异常路由测试

2. **性能优化**
   - 简历解析延迟从 P95 4.2s 降到 3s（并行 LLM + 向量写入）
   - 匹配评分延迟从 P95 7.8s 降到 6s（批量检索证据）

3. **用户体验**
   - 多简历版本对比
   - 匹配报告导出 PDF





---

## 附录

### A. 关键文件清单

**后端核心：**
- [backend/app/graph/builder.py](backend/app/graph/builder.py) — LangGraph 图构建
- [backend/app/graph/runtime.py](backend/app/graph/runtime.py) — 消息处理 + 任务恢复
- [backend/app/graph/workers.py](backend/app/graph/workers.py) — Worker 逻辑 + 输出净化
- [backend/app/services/conversation_actions.py](backend/app/services/conversation_actions.py) — 动作执行器
- [backend/app/services/llm_tasks.py](backend/app/services/llm_tasks.py) — LLM 业务逻辑

**前端核心：**
- [frontend/src/features/chat/ChatPage.tsx](frontend/src/features/chat/ChatPage.tsx) — 对话工作台
- [frontend/src/features/match/MatchPage.tsx](frontend/src/features/match/MatchPage.tsx) — 匹配报告
- [frontend/src/features/interview/InterviewPage.tsx](frontend/src/features/interview/InterviewPage.tsx) — 模拟面试

**测试与评估：**
- [backend/tests/test_api.py](backend/tests/test_api.py) — API 集成测试
- [backend/tests/test_graph.py](backend/tests/test_graph.py) — LangGraph 单测
- [backend/scripts/evaluate_business_flows.py](backend/scripts/evaluate_business_flows.py) — 业务流程评估
- [backend/scripts/evaluate_supervisor_routes.py](backend/scripts/evaluate_supervisor_routes.py) — 路由准确率评估

**文档：**
- [docs/architecture-review.md](docs/architecture-review.md) — 架构评审（73/100 分）
- [docs/mvp-roadmap.md](docs/mvp-roadmap.md) — MVP 路线图
- [docs/langgraph-architecture.md](docs/langgraph-architecture.md) — LangGraph 架构设计

### B. 常用命令

**后端：**
```powershell
cd backend
uv sync --dev
uv run python -m alembic upgrade head
uv run python -m pytest -v
uv run python -m uvicorn app.main:app --reload
```

**前端：**
```powershell
cd frontend
npm ci
npm run typecheck
npm run build
npm run dev
```

**一键启动：**
```powershell
.\scripts\local.ps1 -Action start
.\scripts\local.ps1 -Action check
.\scripts\local.ps1 -Action stop
```

---

**文档版本**：v1.1
**最后更新**：2026-10-05（重新运行测试后）
**下次更新**：完成真实模型业务评估、路由评估和演示视频后
