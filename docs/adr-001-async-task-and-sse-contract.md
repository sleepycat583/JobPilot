<!--
ADR-001：冻结 Week3 异步启动与 SSE 契约。
用途：作为后续 Task 2-4 的唯一实现依据，固定异步任务启动、SSE 订阅、事件缓冲与边界处理规则。
调用方：后续 FastAPI API、事件总线、SSE 端点与前端联调任务。
外部依赖：09-求职多智能体辅助系统架构设计.md、docs/week3_plan.md、当前 app/api.py 与 observability 调研结论。
-->

# ADR-001：冻结异步启动与 SSE 契约

- 状态：已接受
- 日期：2026-07-18
- 适用范围：Week3 Task 2-4 及后续基于 SSE 的前后端联调

## 一、背景

第一轮 SSE 只读调研已经确认：

1. `app/services/observability.py` 中的 `observe_node` 当前只把事件写到 `stdout` 与滚动 JSONL 文件，没有进程内事件总线、队列或 pub-sub 机制可供 HTTP 实时订阅。
2. `app/api.py` 中现有 `POST /v1/job-analysis` 为同步 `graph.invoke()` 执行模型：请求接收后，路由在返回前阻塞等待 Graph 执行到完成或中断。
3. 因此，“发起任务”和“订阅进度”当前耦合在同一次同步请求里。仅增加一个 SSE GET 端点，不能让客户端在当前长流程执行期间实时看到节点事件。

Week3 需要在不修改现有同步兼容入口语义的前提下，引入一条**异步启动 + 独立 SSE 订阅**的新路径，让前端能在当前连接内实时看到节点执行进度。

## 二、决策

### 决策 1：新增异步启动端点，保留现有同步/恢复端点不变

新增端点：

- `POST /api/tasks`
  - 请求体复用现有 `/v1/job-analysis` 的结构：
    - `jd_text`
    - 可选 `resume_version`
  - Header 复用现有 `X-Session-ID` 语义：
    - 未提供时由服务端生成 UUIDv4
    - 提供时按现有规则校验 UUIDv4
  - 响应：`202 Accepted`
  - 响应体固定为：

```json
{
  "session_id": "uuidv4",
  "thread_id": "uuidv4",
  "status": "accepted"
}
```

该端点只负责：

- 创建/复用 `session_id`
- 生成新的 `thread_id`
- 注册任务归属关系
- 触发后台 Graph 执行
- 立即返回，不等待 Graph 完成

现有端点兼容性保持如下：

- `POST /v1/job-analysis`：**保留不变**，继续作为同步兼容入口
- `POST /v1/threads/{thread_id}/resume`：**保留不变**，继续按现有同步恢复语义工作

### 决策 2：SSE 端点与缓冲窗口固定

SSE 端点固定为：

- `GET /api/sessions/{session_id}/events`
- `Content-Type: text/event-stream`

短期缓冲窗口按 `thread_id` 保存，用于覆盖“`202 Accepted` 已返回，但 SSE 尚未建连”之间的事件丢失窗口。

缓冲保留策略固定为：

- 每个 `thread_id` **保留最近 60 秒**内的事件
- 且**最多保留最近 50 条事件**
- 两者取先到者；超出任一条件后，最旧事件被淘汰

该缓冲只用于：

- 首次建连时补发早于连接建立的短窗口事件
- 页面刷新或短暂断开后，在窗口期内回放最近事件

本 ADR 不覆盖 `Last-Event-ID` 的跨更长时间补发能力；那属于后续持久化事件能力范围。

### 决策 3：按文档原样实现 session 级订阅，多路复用采用最小设计

不降级为 thread 级公开订阅端点。对外仍然严格使用：

- `GET /api/sessions/{session_id}/events`

内部事件组织采用“**thread 级缓冲 + session 级实时广播**”的最小设计：

1. `session_id -> set[thread_id]`
   - 表示一个 session 当前关联的全部 thread

2. `thread_id -> session_id`
   - 反向定位 thread 所属 session，避免发布时扫描所有 session

3. `thread_id -> deque[event]`
   - 保存该 thread 的短期缓冲事件，容量与时间窗口受上一节约束

4. `session_id -> set[subscriber_queue]`
   - 保存当前订阅该 session 的全部 SSE 订阅者队列

这里明确**不采用**“一个 SSE 连接同时直接管理 N 个独立 thread 队列”的重量级方案。
原因是该方案会引入：

- 动态新增 thread 时对已有 SSE 连接追加消费者任务
- 多队列公平合并与取消管理
- 每个连接维护更多运行中协程

本 ADR 选用更简单的结构：

- **缓冲按 thread 保存**，便于按 thread 进行短期回放和终态保留
- **实时广播按 session 聚合**，让 SSE 连接只消费自己对应 session 的单一广播队列

### 决策 4：工作线程与主事件循环之间的通信固定为线程安全投递

当前部署边界以架构文档冻结为前提：

- 单实例 FastAPI
- 单 Uvicorn worker
- Graph 执行仍以同步模型运行

因此未来实现中：

- Graph 执行与 `observe_node` 事件产出发生在线程池工作线程中
- SSE 生成器与订阅者队列维护发生在主 `asyncio` 事件循环中

为避免跨线程直接操作共享字典或 `asyncio.Queue`，固定采用：

- 工作线程调用 `loop.call_soon_threadsafe(...)`
- 将“发布事件”的操作投递回主事件循环
- 由主事件循环统一完成：
  - 归属校验
  - session/thread 索引维护
  - thread 缓冲更新
  - session 广播

这意味着：

- 工作线程**不得**直接修改事件总线中的共享字典
- 工作线程**不得**直接向 `asyncio.Queue` 写入事件
- 所有订阅状态只由主循环持有和更新

### 决策 5：缺失事件全部复用现有信号派生，不新增事件机制

SSE 事件来源以现有 `observe_node` 和现有异常/终态信号为基础，不新增新的业务执行机制。

#### 5.1 `node_retrying`

从现有 `ErrorEntry` 直接派生，条件固定为：

- `retryable = true`
- 且 `attempt < MAX_FORMAT_RETRIES`

满足该条件的错误记录，表示该节点还会继续尝试，SSE 侧派生为 `node_retrying`。

#### 5.2 `run_failed`

复用现有 Graph 异常时返回 `500 GRAPH_EXECUTION_FAILED` 的异常捕获点。

适用范围包括：

- 现有同步入口 `/v1/job-analysis`
- 新增异步入口 `/api/tasks` 对应的后台执行包装层
- 现有同步恢复入口 `/v1/threads/{thread_id}/resume`

一旦 Graph 执行落入该异常捕获点，应额外发出 `run_failed` 事件。

#### 5.3 `run_completed`

当 Graph 执行到 `END` 且 `final_output` 非空时触发。

该规则表示：

- 仅走到图终点还不够
- 必须确认已有最终可展示产物，才对外声明本次 run 已完成

## 三、端到端事件流程时序说明

以下流程描述从客户端异步发起任务到收到 SSE 事件的完整链路。

### 1. 任务启动

1. 客户端调用 `POST /api/tasks`
2. 服务端校验请求体与可选 `X-Session-ID`
3. 服务端创建或复用 `session_id`
4. 服务端生成新的 `thread_id`
5. 服务端在主事件循环中注册：
   - `session_id -> thread_id`
   - `thread_id -> session_id`
   - 该 thread 的空缓冲队列
6. 服务端触发后台执行包装层
7. 服务端立即返回：

```json
{
  "session_id": "...",
  "thread_id": "...",
  "status": "accepted"
}
```

### 2. SSE 建连

1. 客户端拿到 `session_id` 后建立：
   - `GET /api/sessions/{session_id}/events`
2. SSE 路由在主事件循环中：
   - 注册一个新的 subscriber queue 到该 `session_id`
   - 查找该 session 当前已关联的全部 thread
   - 回放这些 thread 在短期缓冲窗口内尚可见的事件
3. 回放完成后，连接持续监听该 session 的实时广播事件

### 3. 后台 Graph 执行与事件桥接

1. 后台执行包装层在线程池中运行同步 `graph.invoke()`
2. `observe_node` 在节点开始、结束、失败、中断时产出统一事件
3. 工作线程不直接写异步队列，而是调用：
   - `loop.call_soon_threadsafe(publish_event, event)`
4. 主事件循环中的 `publish_event` 执行：
   - 校验 `thread_id -> session_id` 归属
   - 更新该 `thread_id` 的缓冲 deque
   - 如有需要，为该 session 分配单调递增的会话级序号
   - 将事件广播给该 `session_id` 下所有 subscriber queue
5. SSE 生成器从自身 subscriber queue 取事件并以 `text/event-stream` 写出给客户端

### 4. 终态事件

1. 若执行过程中发生未恢复异常并命中统一异常捕获点：
   - 产出 `run_failed`
2. 若执行到 `END` 且 `final_output` 非空：
   - 产出 `run_completed`
3. 即使 run 已完成，thread 缓冲与索引也不会立刻删除，而是进入保留窗口，供稍后建连或刷新回放使用

## 四、边界情况处理规则

以下规则属于本 ADR 冻结内容，后续实现必须直接遵守。

### 4.1 SSE 先建立、thread 尚未创建

处理策略：**允许连接保持空闲等待，不报错、不自动关闭。**

- 如果该 `session_id` 当前尚无已注册 thread，SSE 连接仍然建立成功
- 连接在空闲期间只等待未来属于该 session 的新 thread 事件
- 后续新 thread 注册到该 session 时，事件会自然广播到该已有连接

### 4.2 thread 已执行完毕、SSE 后建立时的回放规则

处理策略：**仅回放仍处于缓冲窗口内的事件。**

- 回放范围受“双限制”控制：
  - 最近 60 秒
  - 最近 50 条
- 超出窗口的历史事件不补发
- 如果一个已完成 thread 的缓冲仍在窗口内，SSE 建连后仍可收到其回放事件，包括终态事件

### 4.3 多个 thread 并发产生事件时的顺序保证范围

处理策略：

- **保证单个 thread 内事件顺序与发布顺序一致**
- **跨 thread 不承诺真实时间全局顺序**
- 同一 session 内，服务端为广播事件分配**会话级单调递增序号**，作为主事件循环接收并广播时的稳定顺序依据

这意味着：

- 同一 thread 的开始/重试/结束顺序必须保持
- 不同 thread 同时执行时，只保证它们在主事件循环中的广播顺序稳定可复现，不以墙钟时间先后作为强保证

### 4.4 thread 完成后的缓冲和索引清理时机

处理策略：**不在 `run_completed` 或 `run_failed` 后立即删除。**

- thread 进入终态后，仍保留：
  - `thread_id -> session_id` 反向索引
  - 该 thread 的缓冲 deque
- 保留直到以下任一条件成立：
  - 最后一条事件距离当前时间超过 60 秒
  - 缓冲因容量限制自然淘汰到空
- 超出窗口后，才允许删除该 thread 的缓冲与索引

### 4.5 session 下部分 thread 完成、部分仍运行时，SSE 连接何时关闭

处理策略：**服务端不主动因单个 thread 完成而关闭整个 session SSE。**

- 只要连接未断开，SSE 可以持续保持
- 即使某些 thread 已完成，只要该 session 后续还可能有新 thread，连接仍可继续复用
- 是否关闭连接由客户端决定；服务端仅在连接断开、网络失败或订阅队列溢出等异常情况下结束该 SSE 响应

### 4.6 SSE 连接断开或页面刷新时的清理

处理策略：**只清理订阅者，不取消 Graph。**

- 连接断开时：
  - 从 `session_id -> subscriber_queue 集合` 中移除该订阅者
  - 释放该连接对应的异步队列资源
- 不得因为 SSE 连接断开：
  - 取消后台 Graph 任务
  - 删除 session/thread 索引
  - 删除仍在窗口内的 thread 缓冲

页面刷新被视为“旧连接断开 + 新连接重建”的普通场景。

### 4.7 慢消费者策略

处理策略固定为：

- 每个 subscriber queue 容量上限：**100 条事件**
- 当某个订阅者队列已满时：**立即断开该 SSE 连接**
- 同时向服务器日志记录该连接因慢消费者被断开的原因

不采用“静默丢弃旧事件”策略，原因是：

- 静默丢弃会导致前端误以为流程完整，实际却丢了中间节点
- Week3 当前更重视进度正确性而不是尽量保活异常连接

因此明确选择：

- **不丢事件继续保连接**
- **而是断开异常连接，让客户端按既定重连/重建流程恢复**

### 4.8 防止 thread 被伪造注册到错误 session

处理策略：**thread 与 session 的归属关系只能在任务创建时写入一次，后续不可静默改绑。**

- `POST /api/tasks` 创建任务时写入：
  - `thread_id -> session_id`
- 后续事件发布时，必须依据已有反向索引校验归属
- 若出现以下任一情况，应视为内部协议错误并拒绝发布：
  - 未注册 thread 直接发布事件
  - 已注册 thread 试图写入另一个 session
  - session 正反向索引不一致

该类错误应记录为服务器内部异常，不允许把事件错误广播到其他 session。

## 五、与现有端点的兼容性说明

本 ADR 明确要求以下端点**不受影响**：

1. `POST /v1/job-analysis`
   - 继续保持同步语义
   - 继续在一个请求内执行 `graph.invoke()` 并返回完整响应
   - 不因为引入 `/api/tasks` 而修改路径、请求体、响应体或中断行为

2. `POST /v1/threads/{thread_id}/resume`
   - 继续保持现有同步恢复语义
   - 继续基于已有 checkpoint 与 HITL contract 工作

也就是说：

- 新异步路径是新增能力，不是替换现有同步路径
- 后续实现 Task 2-4 时，不得顺手改变旧端点签名或业务行为

## 六、明确排除范围

本 ADR **不覆盖**以下内容：

1. **多 worker / 多进程部署下的跨进程事件同步**
   - 当前设计严格建立在“单 Uvicorn worker、单进程内存总线”假设之上
   - 若未来启用多个 worker，每个进程都会拥有独立事件总线，届时需要单独 ADR 讨论跨进程同步或任务/订阅亲和策略

2. **基于持久化存储的长期事件补发**
   - 本 ADR 只冻结 60 秒 / 50 条的内存级短期补发窗口
   - 超过该窗口的历史补发，以及 `Last-Event-ID` 的完整持久化恢复，不在本次范围内

3. **前端 EventSource 生命周期与 UI 策略**
   - 本 ADR 只定义后端契约和边界，不定义 React 的 reducer、重连策略或展示细节

## 七、待确认细节清单

本 ADR 主体决策已经冻结，后续实现不需要再次讨论以下核心问题。本次无额外待确认细节。