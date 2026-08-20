# Phase 8: Local Workflow Verification

第八阶段面向个人电脑单实例使用，重点是让长任务可控、结果可回看，并用真实浏览器完成验收。

## 本阶段交付

- 匹配报告写入 SQLite，并在匹配页展示历史报告与证据详情。
- 模拟面试完成后写入 SQLite，并在面试页展示历史复盘。
- 对话与匹配任务支持幂等取消；取消后的迟到模型结果不会覆盖 `cancelled` 状态。
- 面试处于中断态时，匹配接口返回 `THREAD_BUSY`，前端禁用并发匹配操作。
- 简历同名上传自动递增版本号。

## 自动化验收

```powershell
cd backend
uv run alembic upgrade head
uv run alembic check
uv run pytest

cd ..\frontend
npm run build
```

本阶段完成时的结果：`73 passed`，`alembic check` 无待执行迁移，Vite 生产构建成功。

## 浏览器证据

以下截图由本地 Stub 模式服务和 Playwright 真实浏览器生成，均位于 `output/playwright/`：

- `phase8-chat-desktop.png`：对话工作台桌面布局。
- `phase8-chat-mobile.png`：对话工作台移动布局。
- `phase8-resume-desktop.png`：简历版本与结构化结果。
- `phase8-jd-desktop.png`：JD 解析结果与原文证据。
- `phase8-match-evidence-desktop.png`：匹配分数、历史报告和证据详情。
- `phase8-match-mobile.png`：匹配报告移动布局。
- `phase8-interview-question-desktop.png`：模拟面试题目与提纲。
- `phase8-interview-feedback-desktop.png`：逐题反馈。
- `phase8-interview-report-mobile.png`：移动端面试复盘与历史记录。
- `phase8-thread-busy-mobile.png`：面试进行中时匹配按钮被禁用。
