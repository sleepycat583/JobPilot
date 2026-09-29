# 流式输出修复记录

## 问题症状
- 用户发送消息后,前端等待较长时间没有反应
- AI 回复内容最后一次性全部显示,没有打字机效果
- 预期:逐字符显示,类似 ChatGPT 的打字机效果

## 根本原因
- 前端只会在收到 `message_delta` 后显示临时消息，单纯增加光标 CSS 不会产生增量内容。
- Worker 使用 `with_structured_output(...).invoke()`，模型先完整生成结构化结果；`graph.astream(..., stream_mode="values")` 只能在节点完成后返回完整 state。
- 因此原实现最多生成一个完整 delta，前端没有可逐步渲染的数据。

## 修复方案
当前实现分两步保证用户体验：
- 使用 LangGraph 的 `astream()` 异步模式，避免图运行阻塞 SSE 连接。
- Worker 先用结构化输出完成内部 action 决策；当 action 为 `respond` 时，再使用普通文本 `astream()` 生成用户回复，并通过 LangGraph `custom` stream 将每个文本 chunk 转成 `message_delta`。
- 如果模型供应商没有返回可用文本 chunk，后端仍会按 8 个字符、每 20ms 一个事件拆分最终文本；前端再按约 24ms 的节奏逐步显示，兼容不同 OpenAI-compatible 实现。
- `message_started`、`message_delta`、`message_completed` 共用同一个 `message_id`，避免正式消息和临时消息重复渲染。

结构化 action 本身不会流给前端，因为半截 JSON 或工具调用不是用户可读内容；只有确认是 `respond` 后才开始流式生成自然语言回复。

## 相关文件
- `backend/app/graph/runtime.py` - 核心修复点
- `backend/app/graph/models.py` - 已正确配置 streaming=True
- `backend/app/api/routes/events.py` - SSE 端点无需修改
- `frontend/src/app/WorkspaceProvider.tsx` - 前端逻辑无需修改
