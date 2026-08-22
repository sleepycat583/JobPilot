# 第 11 阶段发布验收记录

验收日期：2026-08-22

## 自动化门禁

| 检查 | 结果 |
|---|---|
| `uv run pytest` | 通过，75 passed |
| `uv run alembic check` | 通过，No new upgrade operations detected |
| `npm run typecheck` | 通过 |
| `npm run build` | 通过，Vite production build |
| PowerShell `local.ps1` 语法解析 | 通过 |
| 文档与发布文件 `git diff --check` | 通过 |
| 发布文档/模板敏感信息扫描 | 通过，未发现真实密钥模式 |

## Windows 启动器验收

使用 `scripts/local.ps1` 在本机 Stub 模式执行：

- `-Action start -SkipInstall`：自动执行迁移，后端 live/ready 和前端页面全部就绪。
- `-Action check`：三个健康检查全部通过并返回退出码 0。
- 第二次 `-Action start`：安全拒绝重复启动并返回退出码 1。
- `-Action stop`：停止脚本记录的进程树，两个 HTTP 地址不可访问，`backend/data` 保留。
- `-BackendPort 8001 -FrontendPort 5174`：自定义端口启动、ready 检查和前端代理全部通过。
- 启动器未覆盖已有 `backend/.env`，没有打印环境变量或密钥内容。

启动产物位于被忽略的 `output/local/`：PID 文件、后端日志、前端日志和错误日志。停止服务后 PID 文件清理，业务数据仍保留。

## 浏览器验收

通过 Playwright 真实浏览器访问本地 Vite 页面，并经代理访问 FastAPI：

- `output/playwright/phase11-chat-desktop.png`：对话工作台桌面布局。
- `output/playwright/phase11-resume.png`：简历库与结构化结果。
- `output/playwright/phase11-jd.png`：JD 分析与原文证据。
- `output/playwright/phase11-match.png`：匹配任务完成、85 分报告和历史记录。
- `output/playwright/phase11-interview.png`：模拟面试题目与面试提纲。
- `output/playwright/phase11-interview-mobile.png`：移动端模拟面试布局。

匹配流程实际经历“运行中 -> SSE 状态更新 -> 完成”，并在页面展示历史报告；面试页成功生成第 1 题。

## Docker

本机未安装 Docker CLI，因此 `docker compose config --quiet` 和容器启动属于可选验收，未执行。PowerShell 单机启动路径已完成完整验收。
