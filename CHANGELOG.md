# Changelog

本项目遵循语义化版本号。当前版本：`0.1.0`。

## [0.1.0] - 2026-08-22

### Added

- LangGraph Supervisor-Worker 编排、语义意图路由和统一用户输出净化。
- 简历解析、结构化存储、异步 ChromaDB 向量索引和版本管理。
- JD 解析、简历-JD 匹配评分、证据检索和低分 HITL 确认。
- AI 模拟面试、逐题反馈、最终复盘和历史结果回看。
- SQLite 业务数据、SqliteSaver checkpoint、SSE 状态同步和任务恢复。
- 本地隐私与数据管理、备份恢复和可再生历史清理能力。
- Windows 本地一键启动、健康检查、停止服务和本地发布文档。

### Security

- API Key 仅通过环境变量或本机 OAuth 注入，不写入仓库、日志或发布记录。
- 默认 `LLM_MODE=stub`，首次本地启动不需要第三方密钥。

