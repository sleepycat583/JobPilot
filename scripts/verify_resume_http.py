"""第 3 章 resume 与错误协议的真实 HTTP 验证脚本。

使用 FastAPI TestClient 经过真实路由、真实业务 SQLite、真实 Graph/Checkpoint；
模型替身只消除外部 LLM 依赖，输出原始 HTTP 响应体供验收记录使用。
"""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from verification_server import build_verification_app

JD_TEXT = "后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。"


def emit(name: str, response: object) -> None:
    """输出每个真实 HTTP 响应的状态码与未经改写的 JSON body。"""

    print(json.dumps({"scenario": name, "status": response.status_code, "body": response.json()}, ensure_ascii=False))


def main() -> None:
    """创建 final_review interrupt 后验证同键重放、冲突与失败协议。"""

    app = build_verification_app()
    with TestClient(app) as client:
        initial = client.post("/v1/job-analysis", json={"jd_text": JD_TEXT})
        emit("initial_interrupt", initial)
        thread_id = initial.json()["thread_id"]
        key = str(uuid4())
        first = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"idempotency_key": key, "command": {"type": "final_review", "action": "approve"}},
        )
        emit("resume_first_approve", first)
        replay = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"idempotency_key": key, "command": {"type": "final_review", "action": "approve"}},
        )
        emit("resume_same_key_same_command", replay)
        changed = client.post(
            f"/v1/threads/{thread_id}/resume",
            json={"idempotency_key": key, "command": {"type": "final_review", "action": "reject", "feedback": "changed"}},
        )
        emit("resume_same_key_changed_command", changed)
        missing = client.get(f"/v1/threads/{uuid4()}/state")
        emit("unknown_thread_404", missing)
        malformed = client.post(f"/v1/threads/{thread_id}/resume", content=b"{")
        emit("malformed_json_422", malformed)


if __name__ == "__main__":
    main()