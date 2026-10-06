# 阶段 6：真实业务链路验收记录

## 验收范围

本阶段在真实模型配置下验证核心面向用户的业务链路，并使用独立临时目录承载 SQLite、上传文件、LangGraph checkpoint 和本地 Chroma。验收数据均为合成内容，不使用真实候选人资料。

1. 简历 TXT 上传、隐私替换、结构化解析和向量索引
2. JD 提交、结构化解析和原文证据保留
3. 简历-JD 证据检索与匹配报告
4. 模拟面试出题、回答反馈和主动结束后的复盘

附加验证包括：上传/JD 的 Idempotency-Key 重试、聊天 SSE 事件净化，以及浏览器页面状态同步。

P0-2 附加验证还包括真实 QCC 雇主背调以及多候选主体确认 HITL 流程；该流程单独运行，因为当前 `evaluate_business_flows.py` 尚未将外部 QCC 调用纳入通用五项计分。

## 后端真实模型结果

- 运行日期：2026-10-06
- 运行模式：`LLM_MODE=openai`
- 验收命令：`scripts/evaluate_business_flows.py`
- 结果：`5/5` 通过，退出码为 `0`，约 46 秒

| 验收项 | 断言 |
| --- | --- |
| 简历解析与索引 | 异步任务完成，状态为 `indexed`，结构化结果标记隐私已过滤，向量索引可用于后续匹配。 |
| JD 解析 | 异步任务完成，状态为 `completed`，存在必备技能并保留原文证据标记。 |
| 聊天与 SSE | 真实 Supervisor/Worker 回应完成，持久化事件不含 tool call、raw state、路由审计、置信度或 API key。 |
| 匹配分析 | 返回五个受限维度、分数范围合法、至少一条检索证据。 |
| 模拟面试 | 生成问题，回答后返回分数和反馈，结束后返回复盘和行动项。 |

## P0-2 雇主背调与 HITL 验收

- 运行日期：2026-10-06；使用真实配置的 Supervisor/Worker、QCC MCP 服务；业务 SQLite、checkpoint、上传目录和 Chroma 均位于临时目录。
- 真实输入：`查一下腾讯科技`。系统进入 `employer_entity_confirmation` 中断，QCC 返回 5 个候选主体。
- 通过 API 提交候选主体确认后，会话到达 `completed`；数据库保存 1 条 `completed` 背调报告，助手输出包含企查查信息边界免责声明。
- 未在日志或报告中记录候选统一社会信用代码、API 凭据或供应商原始响应。
- 修复：确认 API 是同步 FastAPI 端点，在工作线程中调用了只能由事件循环线程调用的 `asyncio.create_task()`；`LangGraphRuntime.spawn()` 现通过 `call_soon_threadsafe()` 将协程创建投递回应用事件循环。新增回归测试覆盖主体确认后的异步恢复。

## Supervisor 真实路由评估

- 验收命令：`scripts/evaluate_supervisor_routes.py --json-output output/supervisor-route-report-p0-2-final.json`
- 结果：`30/30` 通过，准确率 `1.000`，非法工具调用 `0`；每个 Worker 准确率均为 `1.000`，默认门禁通过。
- 评估集新增公司名背调、精确短句“查一下腾讯科技”、统一社会信用代码、主体确认上下文、仅咨询雇主的一般问题等样本。
- 报告仅包含样本 ID、预期/实际 Worker 与汇总指标，不包含样本消息正文。

## 浏览器验收

使用 Playwright CLI 在隔离本地服务上完成以下真实 UI 操作：

1. 打开简历库，上传合成 TXT，页面显示“已建立索引”和隐私隔离提示。
2. 打开 JD 分析，提交 JD，页面显示结构化岗位职责和已保留原文证据。
3. 打开匹配报告，确认已选简历/JD，生成包含维度得分和证据数量的报告。
4. 打开模拟面试，启动题目、提交回答、查看逐题反馈，结束并查看复盘报告。

本次浏览器运行未使用前端轮询作为主同步方式；页面通过 SSE 事件失效并刷新当前 thread state。浏览器截图和合成上传文件属于本地 `output/` 产物，不纳入版本控制。

## 发现并修复

真实 E2E 首次执行时，Windows 上的临时 Chroma SQLite 文件在应用退出后仍被持有，导致临时目录清理失败。已为 `VectorStore` 增加幂等 `close()`，并在 FastAPI lifespan 退出阶段调用它。修复后同一验收脚本以退出码 `0` 完成。

P0-2 排查还发现两项遗漏：真实 Supervisor 曾把“查一下腾讯科技”路由到 `chat_worker`，且确认主体的同步 API 路由无法从线程池线程创建 asyncio Task。现已补充 Supervisor 雇主边界示例、真实路由评估样本和线程安全任务调度；修复后真实 QCC 多候选确认及报告持久化通过。

## 复现

从 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -u .\scripts\evaluate_business_flows.py
```

命令要求本地 `.env` 已完成真实模型认证。它不会打印模型输出、合成简历正文、JD 正文、trace payload 或任何凭据。
