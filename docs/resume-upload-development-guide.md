# 简历上传功能开发指导书

> 文档状态：第 1 至 7 步已实现并验证；Python 全量回归（310 passed）、前端单元测试（57 passed）、前端构建及真实浏览器上传全流程均已通过。
>
> 目标：为“上传简历并完成索引”的后续开发冻结已确认的产品规则、职责边界、接口方向、前端交互和验收标准。本文不代表现有后端已经注册这些接口；实现前仍须以仓库外的《09-求职多智能体辅助系统架构设计》对应章节为准，确认 Schema、职责边界和验收标准。

## 1. 范围与已确认规则

本期仅打通简历上传、原始内容保存、异步索引、简历库展示，以及将已索引版本用于现有职位分析/简历匹配的完整链路。

| 项目 | 已确认规则 |
| --- | --- |
| 支持格式 | 仅支持 UTF-8 编码的 `.txt` 文件。第一版不支持 PDF、DOCX 或图片简历。 |
| 文件上限 | 单文件最大 2 MB。 |
| 版本策略 | 用户每次主动上传均创建新的简历版本。 |
| 标识语义 | `resume_id` 是 UUIDv4 的不可变资源标识，用于 API、数据库关联、Graph State、Chroma metadata、RAG 检索和职位分析请求；`display_version` 是全局递增整数，仅用于 UI 显示为 `v1`、`v2`、`v3`。 |
| 重复请求 | 网络重试或前端误触发的同一上传请求使用幂等键去重，不应创建重复版本。 |
| 原始内容 | 保存原始文件，或至少保存完整原始文本；不得只保存分块后的 embedding。 |
| 索引模式 | 使用异步模式：上传接口返回 `202 Accepted`，前端轮询索引状态。 |
| 简历库归属 | 当前单人、无鉴权阶段不按 `X-Session-ID` 隔离简历库。`X-Session-ID` 仅关联一次分析任务上下文；将来接入认证后，通过数据库迁移引入真实 `user_id` 并在 Repository 层隔离。 |
| 上传幂等键 | 使用 `Idempotency-Key` 请求 Header。同一网络重试或重复点击必须复用同一键；新的主动上传必须使用新键。 |
| 原始文件存储 | 原始 `.txt` 文件保存到项目 `data/resumes/`；数据库保存相对路径和文件元数据。 |
| 后台索引 | 使用 FastAPI 进程内的既有后台任务机制。服务重启时遗留的 `pending` 或 `indexing` 记录标记为 `failed`，由用户手动重试。 |
| 前端轮询 | 每 1 秒查询一次单版本状态，最长 2 分钟；网络错误最多重试 3 次。 |
| 上传请求指纹 | 使用 `SHA-256(原始文件字节 + 分隔符 + UTF-8 文件名)`；同一幂等键但内容或文件名不同返回 `409`。不使用文件大小或浏览器 MIME 类型作为身份判断。 |
| 展示版本号 | `display_version` 由数据库单行计数器在同一事务内用原子 `UPDATE ... RETURNING` 递增后分配，并加唯一约束。号码永久不重用，即使未来软删除、索引失败或物理删除。 |
| 幂等记录过期 | 记录在 24 小时内有效；过期后创建新上传时惰性清理旧记录，同一幂等键可再次使用，但会创建新的 `resume_id` 和新的 `display_version`。 |
| Chroma 重试策略 | 每次索引或重试先按 `resume_id` 删除全部旧 chunk，再重新切块并 upsert。Chroma 与 SQLite 无跨库事务；中断后的残留由下一次同样的先删后写重试收敛。 |
| 当前技术边界 | 不新增技术栈、不新增 Agent、不把 Tool 改为 Agent。前端继续使用 React + TypeScript + Vite，后端继续使用 FastAPI、既有 Service/Repository/RAG 分层和 Chroma。 |

`Embedding` 指把文本转换为向量，供 Chroma 按语义检索；它不能代替原始文件或原始文本，因为后者用于审计、重新切块和重新索引。

## 实施进度

| 步骤 | 状态 | 已交付内容 |
| --- | --- | --- |
| 第 1 步：冻结契约 | 已完成 | 已冻结 `resume_id`、`display_version`、状态机、幂等、文件规则和 HTTP 契约。 |
| 第 2 步：持久化 | 已完成 | 已新增 `resume_versions`、全局展示版本计数器、上传幂等记录、Repository 与 Alembic `20260724_0004`。 |
| 第 3 步：存储与索引 | 已完成 | 已新增 UTF-8 TXT 校验/原始文件原子保存、Chroma 先删后写、索引状态回写，并完成全链路 `resume_version -> resume_id` 迁移。 |
| 第 4 步：HTTP API | 已完成 | 已注册上传、列表、单项状态和重试接口，使用 FastAPI 后台任务执行索引；新增 `python-multipart==0.0.9` 作为 multipart 解析依赖。 |
| 第 5 步：前端简历库 | 已完成 | 已新增 `api/resumes.ts`、`useResumeLibrary` 和 `ResumeLibrary`，实现上传、轮询、选择、刷新与失败重试 UI。 |
| 第 6 步：分析工作流接入 | 已完成 | 已将所选且已索引的 `resume_id` 传给 `POST /api/tasks`，中间区域回显已选文件名。 |
| 第 7 步：端到端验证 | 已完成 | 已通过 Python 全量回归、前端构建、Vitest 和真实浏览器上传/索引/选择/分析请求验证；验证服务使用本地确定性 embedding 与存储替身，不依赖外部模型。 |

截至本次前端接入完成后的回归，执行 `python -m pytest -q` 的结果为 **310 passed**；执行 `npm --prefix frontend test` 的结果为 **57 passed**。

## 2. 现状与改造目标

### 2.1 当前现状

- `frontend/src/App.tsx` 已通过 `useResumeLibrary` 加载真实简历资源，左侧提供可用的上传入口、刷新、状态展示与失败重试。
- 前端已接入 `POST /v1/resumes`、`GET /v1/resumes`、`GET /v1/resumes/{resume_id}` 和 `POST /v1/resumes/{resume_id}/retry`，开发服务器将 `/v1` 代理到 FastAPI。
- 现有 RAG 层已能处理“已获得的文本”：`chunk_resume()` 负责切块，`index_resume_chunks()` 负责生成 embedding 并写入 Chroma。
- 当前 `resume_version` 是写入 chunk metadata 并用于检索过滤的字符串，还不是持久化的简历版本实体；本期将全链路重命名为 `resume_id`，消除“UUID 身份标识”与“展示版本号”的语义混淆。
- `POST /v1/threads/{thread_id}/resume` 是 LangGraph 的人工审核恢复接口，不是上传简历接口，不得复用或改造为文件上传。

### 2.2 本期完成后的目标

1. 用户可以在 UI 选择合规 `.txt` 简历并上传。
2. 后端创建新的简历版本，保存原始文件/文本，并异步执行切块和向量索引。
3. 前端能观察 `pending`、`indexing`、`indexed`、`failed` 四种状态。
4. 只有 `indexed` 简历可被选中并作为 `resume_id` 传给现有分析任务。
5. 索引失败可显示明确原因并发起重试，不影响已有已索引简历。

## 3. 领域模型与状态机

### 3.1 简历版本实体

每一次主动上传对应一个独立的简历版本。该实体至少需要关联以下信息：

| 字段概念 | 用途 |
| --- | --- |
| `resume_id` | UUIDv4 不可变主键，同时写入 Chroma chunk metadata，供后续匹配请求过滤检索。 |
| `display_version` | 全局递增正整数，仅用于 UI 展示为 `vN`，不用于 API 路由、检索或任务请求。 |
| `file_name` | 原始上传文件名，用于 UI 展示和审计。 |
| `file_size` | 原始文件字节数，用于展示和校验记录。 |
| 原始文件路径或原始文本 | 用于审计、重新切块和重新索引。 |
| `index_status` | 当前索引生命周期状态。 |
| `error_code` / `error_message` | 索引失败时供 UI 展示和诊断。 |
| `created_at` / `updated_at` | 版本与状态变更的时间记录。 |
| 上传幂等关联信息 | 用于区分“新的主动上传”和“同一请求重试”。 |

字段名称和数据库表名必须在实现前由架构设计文档冻结；上表描述的是必须具备的业务信息，不是可直接照抄的最终数据库 Schema。当前阶段不得记录或按会话隔离简历资源。

### 3.2 索引状态

```text
上传受理
  -> pending
  -> indexing
  -> indexed
  -> failed

failed --重试索引--> pending -> indexing -> indexed | failed
```

| 状态 | 含义 | 前端行为 |
| --- | --- | --- |
| `pending` | 已创建版本，索引任务尚未开始或正在等待。 | 展示“等待建立索引”，不可用于匹配。 |
| `indexing` | 正在从原始文本切块、生成 embedding 并写入 Chroma。 | 展示“正在建立索引”，不可用于匹配。 |
| `indexed` | 原始内容已成功写入可检索索引。 | 可选中并用于职位分析/简历匹配。 |
| `failed` | 索引未完成。 | 展示错误原因和“重试索引”，不可用于匹配。 |

状态不得由前端推测。前端只渲染后端返回的状态；后端不得在 Chroma 写入失败时把版本标记为 `indexed`。

## 4. 后端职责边界

### 4.1 API 层：`app/api.py`

API 层负责 HTTP 请求解析、文件基础校验、调用 Service、返回已冻结的 DTO，以及将异常映射为项目统一错误 envelope：

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "面向用户的错误说明"
  }
}
```

API 层不得直接读写 Chroma、直接执行 embedding，也不得把上传文件处理编排塞进路由函数。

### 4.2 Service 层：`app/services/`

新增的简历服务负责以下业务编排：

1. 校验文件为空、扩展名、UTF-8 编码和 2 MB 上限。
2. 为每次新的主动上传创建新的简历版本。
3. 对同一上传请求的重试执行幂等去重。
4. 保存原始文件或完整原始文本，并创建 `pending` 记录。
5. 异步调度索引工作，将状态转为 `indexing`。
6. 调用既有 RAG 能力将文本切块并写入 Chroma。
7. 成功时设置为 `indexed`；失败时设置为 `failed` 并记录可展示的错误。
8. 仅允许失败版本进入重试流程，并确保重试不产生重复 chunk。

### 4.3 Repository 与数据库层：`app/repositories/`、`app/db/`、`migrations/`

Repository 负责简历版本、原始内容引用、索引状态和上传幂等记录的持久化，不承担切块或 embedding 工作。数据库迁移必须在实现时新增，不能把简历版本只保存在前端状态或 Chroma metadata 中。

### 4.4 RAG 层：`app/rag/`

复用现有能力：

- `chunk_resume(text, resume_id, source_id)`：把完整文本切为检索 chunk。
- `index_resume_chunks(chunks, embedding_model, store)`：生成 embedding 并写入既有 Chroma collection。

必要时可新增“单份已验证文本的索引包装函数”，但必须将既有 `resume_version` metadata 全链路迁移为 `resume_id`，且不得改变按资源标识过滤检索的语义。索引或重试前必须先定义旧 chunk 的清理/覆盖策略，避免相同 `resume_id` 产生重复 chunk。

### 4.5 异步边界

上传接口只负责受理和创建版本，成功应返回 `202 Accepted`。切块和写入 Chroma 在后台执行，前端通过单版本状态接口轮询。

本期不把上传索引做成新的 Agent，也不把 RAG Tool 改成 Agent。它是服务层编排的基础设施流程。

## 5. HTTP 契约方向

以下为开发前需冻结的接口方向。路径、字段名和状态码只有在架构文档确认后才能进入实现；其中已确认的业务规则已在注释中明确。

### 5.1 已冻结的资源标识迁移

本期执行一次不保留兼容字段的跨层契约迁移：

| 当前名称 | 冻结后名称 | 适用范围 |
| --- | --- | --- |
| `resume_version` | `resume_id` | `/api/tasks`、`/v1/job-analysis`、低分复核命令、Graph State、RAG 函数参数、Chroma metadata/filter、匹配结果、审计事件、前端类型和全部测试。 |
| 无 | `display_version` | 简历列表/单项 DTO 和前端 UI，仅作 `vN` 展示。 |

不得保留 `resume_version` 作为 API、Graph State 或 Chroma metadata 的兼容字段。旧字段在一次迁移中删除，以避免同一个 UUID 被误称为“版本号”。现有离线 fixture 索引脚本也必须同步改为传递测试用 `resume_id`。

### 5.2 已冻结的 Resume DTO

所有下列字段名和类型已冻结：

```json
{
  "resume_id": "UUIDv4",
  "display_version": 3,
  "file_name": "张三-后端开发.txt",
  "file_size": 18342,
  "created_at": "2026-07-24T02:00:00Z",
  "updated_at": "2026-07-24T02:00:03Z",
  "index_status": "pending | indexing | indexed | failed",
  "error_code": null,
  "error_message": null
}
```

- `resume_id`：UUIDv4，服务端生成；前端不能自行生成或修改。
- `display_version`：服务端按全局单一简历时间线递增；首次上传为 `1`，UI 显示为 `v1`。
- `file_size`：原始上传文件的字节数，按原始字节数执行 2 MB 上限校验。
- `created_at`、`updated_at`：UTC ISO 8601 时间字符串。
- `error_code`、`error_message`：仅在 `failed` 时允许为非空；其他状态必须为 `null`。

### 5.3 查询简历列表

`GET /v1/resumes`

用途：页面初始加载、上传后刷新简历库。

建议响应形态：

```json
{
  "resumes": [
    {
      "resume_id": "f9a9e6e4-1ad0-45af-8bb5-0fca970b3fc5",
      "display_version": 3,
      "file_name": "张三-后端开发.txt",
      "file_size": 18342,
      "created_at": "2026-07-24T02:00:00Z",
      "updated_at": "2026-07-24T02:00:03Z",
      "index_status": "indexed",
      "error_code": null,
      "error_message": null
    }
  ]
}
```

### 5.4 上传简历

`POST /v1/resumes`

请求类型：`multipart/form-data`。

| 输入 | 规则 |
| --- | --- |
| `file` | 必填，仅 `.txt`，UTF-8 编码，最大 2 MB。 |
| 幂等键 | 同一上传请求的网络重试或重复点击必须提供并复用同一幂等键；用户重新选择并主动上传时必须使用新幂等键。 |
| `Idempotency-Key` Header | 必填。网络重试或重复点击复用同一值；新的主动上传生成新值。 |
| `X-Session-ID` | 不作为简历库归属、列表过滤或访问控制依据；可不随上传请求发送。 |

建议成功响应：

```json
{
  "resume_id": "f9a9e6e4-1ad0-45af-8bb5-0fca970b3fc5",
  "display_version": 3,
  "file_name": "张三-后端开发.txt",
  "index_status": "pending"
}
```

HTTP 成功状态为 `202 Accepted`，表示后端已受理上传并创建版本，不表示向量索引已经完成。

### 5.5 查询单个索引状态

`GET /v1/resumes/{resume_id}`

用途：前端只轮询刚上传或正在处理的版本，避免重复刷新整个列表。

响应使用与列表项相同的简历版本 DTO，含 `index_status` 和失败时的错误信息。

### 5.6 重试索引

`POST /v1/resumes/{resume_id}/retry`

仅 `failed` 状态允许重试。受理后返回 `202 Accepted`，路由会先原子抢占为 `indexing`，再启动后台任务；这样重复重试、正在索引或已索引的版本都会立即返回 `409`，不会产生两个并发索引任务。

### 5.7 HTTP 状态与错误方向

所有失败沿用项目统一错误 envelope。至少应覆盖：

| 错误代码方向 | 触发条件 |
| --- | --- |
| `RESUME_FILE_EMPTY` | 上传文件为空。 |
| `RESUME_FILE_TYPE_UNSUPPORTED` | 文件不是 `.txt`。 |
| `RESUME_FILE_TOO_LARGE` | 文件超过 2 MB。 |
| `RESUME_FILE_ENCODING_INVALID` | 文件不能按 UTF-8 解码。 |
| `RESUME_TEXT_EMPTY` | 解码后内容为空或只有空白。 |
| `RESUME_NOT_FOUND` | 请求不存在的版本。 |
| `RESUME_INDEX_FAILED` | 索引任务失败或重试失败。 |
| `RESUME_INDEX_CONFLICT` | 不允许的并发索引或重试状态冲突。 |

最终错误码命名、HTTP 状态映射及消息文案以冻结 Schema 为准。

已冻结的 HTTP 映射如下：

| 场景 | HTTP 状态 |
| --- | --- |
| 上传或失败版本重试已受理 | `202 Accepted` |
| 列表或单版本状态查询成功 | `200 OK` |
| 文件格式、大小、编码、空文件或空白文本不合法 | `422 Unprocessable Entity` |
| `resume_id` 不存在 | `404 Not Found` |
| 同一幂等键对应不同文件，或对非 `failed` 版本发起重试 | `409 Conflict` |

`Idempotency-Key` 必须是 UUIDv4；幂等记录保留 24 小时。相同键和相同请求指纹在有效期内返回原始受理结果，不创建新的简历版本。

## 6. 上传与幂等规则

### 6.1 主动上传与重试的区分

“每次主动上传新版本”和“重复请求去重”同时成立：

- 用户点击上传、选择文件并确认提交，属于一次新的主动上传，服务端应创建新的 `resume_id` 与新的 `display_version`。
- 同一请求因网络超时、浏览器自动重试或按钮误触发再次发送时，客户端复用同一幂等键，服务端必须返回原请求对应的结果，而不是创建第二个版本。
- 用户取消后重新选择文件，或完成一次上传后再次点击上传，即使文件内容相同，也属于新的主动上传，应使用新的幂等键并创建新的版本。

### 6.2 幂等记录

上传幂等记录需要至少关联：幂等键、请求指纹、创建的 `resume_id`、最终/当前受理结果。相同幂等键但不同请求内容必须返回明确冲突，不得静默复用错误版本。

幂等键固定使用 `Idempotency-Key` Header，必须为 UUIDv4，幂等记录保留 24 小时。请求指纹使用 `SHA-256(原始文件字节 + 分隔符 + UTF-8 文件名)`；同一键但指纹不同返回 `409`。

## 7. 前端实现方案

### 7.1 模块拆分

建议在不新增依赖的前提下按以下职责拆分：

```text
frontend/src/
  App.tsx                         页面编排，持有被选中的 resume_id
  components/ResumeLibrary.tsx    简历列表、上传入口、状态与重试按钮
  hooks/useResumeLibrary.ts       列表加载、FormData 上传、状态轮询、错误处理
  types.ts                        Resume DTO 和索引状态类型
  api/resumes.ts                  简历 API 调用封装（如项目最终接受独立 API 层）
  index.css                       简历库样式与响应式规则
```

`useResumeLibrary` 是管理网络副作用的 Hook（Hook 是 React 中复用状态和副作用逻辑的函数）；组件只负责展示数据和触发用户操作。它不能复用 `useAgentProgress` 的 reducer，因为简历索引和 LangGraph 分析任务是两套独立状态机。

### 7.2 前端状态与交互约束

```text
初始加载 -> ready
ready -> choosing_file -> uploading -> polling_index_status -> indexed
                                           |-> failed -> retrying -> polling_index_status
```

- 初始加载调用简历列表接口，删除 `EMPTY_RESUMES` 等静态占位数据。
- 用户点击“上传新版本简历”后，使用原生 `input[type=file]` 打开系统文件选择器。
- 选择文件后，前端先校验扩展名和 2 MB 上限；后端仍必须重复校验，前端校验只为即时反馈。
- 使用 `FormData` 上传文件和幂等信息；上传期间禁用上传入口，避免同一组件产生重复请求。
- 收到 `202` 后，将该版本插入/刷新到列表并轮询单版本状态。
- `pending` 与 `indexing` 简历不能被选中用于匹配。
- `indexed` 简历可以点击选中；分析任务只传递后端返回的 `resume_id`。
- `failed` 简历显示错误原因与重试动作，但不得影响其它已索引简历。
- 页面卸载时必须取消轮询；每 1 秒轮询一次，最长 2 分钟，网络错误最多重试 3 次。超时或重试耗尽时必须展示用户可见错误。

### 7.3 UI 可视化效果

#### 空状态

```text
简历库

暂无简历，请上传并完成索引。

┌────────────────────────────────┐
│        +  上传新版本简历        │
└────────────────────────────────┘
支持 UTF-8 .txt · 最大 2 MB
```

上传按钮为可操作状态，不再永久禁用。

#### 上传中与索引中

```text
简历库

┌────────────────────────────────┐
│ 张三-后端开发.txt               │
│ 正在上传…                       │
└────────────────────────────────┘

┌────────────────────────────────┐
│ 李四-全栈开发.txt               │
│ 正在建立索引 · 暂不可用于匹配   │
└────────────────────────────────┘
```

如果后端没有真实进度事件，UI 只显示阶段状态，不展示虚假的百分比进度条。

#### 索引成功与选中态

```text
简历库

┌────────────────────────────────┐
│ ● 张三-后端开发.txt             │
│   已索引 · 可用于匹配           │
│   版本：v3                       │
└────────────────────────────────┘

┌────────────────────────────────┐
│        +  上传新版本简历        │
└────────────────────────────────┘
```

点击已索引卡片后，卡片用现有设计系统的选中边框和背景高亮；中间分析区域回显“已选择简历：张三-后端开发.txt”。用户点击开始分析后，现有任务请求携带该卡片对应的 `resume_id`。

#### 索引失败

```text
┌────────────────────────────────┐
│ 张三-后端开发.txt               │
│ 索引失败：无法生成简历索引      │
│                    [重试索引]  │
└────────────────────────────────┘
```

失败版本不可选中。点击“重试索引”后状态回到“等待建立索引/正在建立索引”。

#### 文件不合规

选择 PDF、DOCX、超过 2 MB、空文件或非 UTF-8 文本时，在上传入口附近展示明确原因。例如：

```text
暂不支持该文件格式，请上传 UTF-8 编码的 .txt 简历。
```

错误文案必须可行动，不能只显示“上传失败”。

### 7.4 响应式要求

- 桌面端沿用现有三栏工作台布局，简历库维持左侧区域。
- 现有 `1050px` 以下布局中，右侧进度区域下移时，简历库继续显示上传和选择状态。
- 现有 `680px` 以下单列布局中，上传按钮宽度适配容器；文件名、错误信息和版本号允许换行，不得遮挡相邻控件。
- 使用现有样式体系和原生控件，不新增图标或上传组件库。

## 8. 与现有职位分析的集成

上传功能完成后，简历库只负责提供经过后端确认的 `resume_id`：

```json
{
  "jd_text": "...",
  "resume_id": "已索引简历的 UUIDv4"
}
```

该字段由现有 `POST /api/tasks` 或 `POST /v1/job-analysis` 消费。前端不得以 `index_status` 推断简历匹配一定成功；实际匹配可用性仍以分析接口返回的 `match_result` 或错误响应为准。

## 9. 测试与验收标准

### 9.1 后端

- [x] 上传空文件、非 `.txt` 文件、超过 2 MB 文件、非 UTF-8 文件和空白文本，均按统一错误 envelope 返回。
- [x] 合法 `.txt` 上传返回 `202`，并获得唯一 UUIDv4 `resume_id` 与递增的 `display_version`。
- [x] 每次新的主动上传均创建新版本；同一幂等键的请求重放不创建重复版本。
- [x] 列表和单版本状态接口可正确反映 `pending`、`indexing`、`indexed`、`failed`。
- [x] 成功索引后的版本可被现有分析任务按 `resume_id` 检索。
- [x] Chroma 写入失败时，版本不会显示为 `indexed`，且可重试。
- [x] 重试不会给相同 `resume_id` 产生重复 chunk。
- [x] API 测试覆盖上传、列表、状态查询、失败和重试；RAG 单测覆盖单文件文本索引包装。

### 9.2 前端

- [x] 初始渲染从真实列表读取，不再依赖 `EMPTY_RESUMES`。
- [x] 空状态、上传中、索引中、成功、失败均有明确视觉状态。
- [x] 前端在提交前校验 `.txt` 和 2 MB 上限，并正确展示后端错误。
- [x] 只有 `indexed` 版本可选中并传入分析请求。
- [x] 上传失败或轮询失败不清空已有可用简历。
- [x] 重试动作仅针对失败版本，且界面避免重复点击。
- [x] 组件测试覆盖失败重试、选中态和分析请求载荷；文件校验与轮询由 Hook 实现并通过 TypeScript 构建检查。
- [x] 浏览器验证覆盖：首次进入、上传合法 `.txt`、索引完成、选中简历、发起分析，全程无控制台错误。

## 10. 推荐实施顺序

1. [x] 冻结 Schema、资源归属、异步任务触发方式和验收标准。
2. [x] 新增简历版本持久化模型、Repository 和数据库迁移。
3. [x] 完成 Service 层的上传、原始内容保存、异步索引和重试编排。
4. [x] 注册 API 路由和统一错误映射，并完成后端 API/RAG 测试。
5. [x] 实现 `useResumeLibrary`、`ResumeLibrary` 和相应类型，替换 `App.tsx` 的静态简历库。
6. [x] 将选中的已索引 `resume_id` 接入现有分析任务启动请求，并验证既有 `resume_version` 已全链路删除。
7. [x] 运行前端测试和浏览器全流程验证，完成文档收口。

## 11. 待确认

以下事项目前没有来自架构设计文档或用户确认的依据，开发时不得自行假设：

1. 将来接入真实认证后的 `user_id` 迁移策略、用户/租户授权校验和历史无归属简历的处理方式。
2. 原始文件在 `data/resumes/` 的访问控制、保留期限、是否需要加密，以及数据库同时保存文件路径和原始文本还是只保存路径。
3. FastAPI 进程内后台任务的具体调用入口、任务超时和并发上限。
4. 索引写入前后如何清理或覆盖同一 `resume_id` 的 Chroma chunk，以保证重试幂等。
5. 轮询达到 2 分钟或网络重试 3 次耗尽时的最终 UI 文案、是否允许继续后台轮询和手动刷新行为。
6. 是否需要提供删除、原始文件下载、重新命名、版本归档等资源管理能力；本期未纳入范围。
7. 原始文本中可能包含个人信息时，日志、错误详情、SSE 事件和测试 fixture 的脱敏规则。
