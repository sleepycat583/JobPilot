# 第十阶段质量评测记录

本阶段针对真实模型配置完成 Supervisor 路由和核心业务链路回归，评测过程不记录模型密钥、请求头、原始简历、职位描述或完整模型响应。

## Supervisor 路由

- 数据集：`backend/evals/supervisor_routes.json`
- 案例数：24
- 整体准确率：1.000（24/24）
- 非法工具调用：0
- 各 Worker 准确率：`resume_worker`、`jd_worker`、`match_worker`、`interview_worker`、`chat_worker` 均为 1.000
- 评测门槛：整体至少 0.900，单 Worker 至少 0.800

结论：当前路由边界和 Supervisor Prompt 满足数据集中的领域互斥、上下文指代、面试中断恢复和闲聊降级场景，本阶段没有基于失败案例的 Prompt 修改。

## 真实业务链路

使用临时 SQLite、临时上传目录、临时 checkpoint 和临时 Chroma 目录运行，未写入本地业务数据：

- 简历解析与向量索引：通过
- JD 解析：通过
- 对话 SSE 输出净化：通过
- 匹配证据链路：通过
- 模拟面试问题、回答反馈与复盘：通过

## 工程回归

- `uv run pytest`：75 passed
- `uv run alembic check`：No new upgrade operations detected
- `npm run build`：通过

评测输出文件仅作为本地临时证据保留，未纳入 Git，避免将业务文本或运行数据提交到仓库。
