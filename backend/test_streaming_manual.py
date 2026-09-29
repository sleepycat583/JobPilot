"""手动测试流式输出修复 - 通过实际启动 API 测试"""
import requests
import time
import uuid

def test_sse_streaming():
    """测试 SSE 端点是否实时推送 message_delta 事件"""

    # 1. 创建 thread
    print("1. 创建 thread...")
    resp = requests.post(
        "http://localhost:8000/api/threads",
        headers={
            "X-API-Key": "test-key-jobpilot-2026",
            "Idempotency-Key": str(uuid.uuid4())
        },
        json={}
    )
    print(f"   Status: {resp.status_code}")
    print(f"   Response: {resp.text}")
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    thread_id = resp.json()["thread_id"]
    print(f"   Thread ID: {thread_id}")

    # 2. 发送消息
    print("\n2. 发送消息...")
    resp = requests.post(
        f"http://localhost:8000/api/threads/{thread_id}/messages",
        headers={
            "X-API-Key": "test-key-jobpilot-2026",
            "Idempotency-Key": str(uuid.uuid4())
        },
        json={"content": "请简单介绍一下你自己"}
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    print(f"   Run ID: {run_id}")

    # 3. 连接 SSE 端点,监听实时事件
    print("\n3. 监听 SSE 事件流...")
    print("   期望看到: message_delta 事件逐个到达 (打字机效果)")
    print("   如果失败: 长时间没输出,最后一次性返回所有内容\n")

    url = f"http://localhost:8000/api/threads/{thread_id}/events"
    headers = {"X-API-Key": "test-key-jobpilot-2026"}

    delta_count = 0
    last_event_time = time.time()

    with requests.get(url, headers=headers, stream=True) as resp:
        for line in resp.iter_lines():
            if not line:
                continue

            line = line.decode('utf-8')
            if line.startswith('data:'):
                event_data = line[5:].strip()
                if '"event_type":"message_delta"' in event_data:
                    now = time.time()
                    interval = now - last_event_time
                    last_event_time = now
                    delta_count += 1
                    print(f"   ✓ Delta #{delta_count} (间隔 {interval:.3f}s)")

                    # 如果间隔超过 2 秒,说明不是实时流式
                    if delta_count > 1 and interval > 2.0:
                        print(f"\n   ⚠️  间隔过长 ({interval:.1f}s),可能不是实时流式!")

                elif '"event_type":"message_completed"' in event_data:
                    print(f"\n   消息完成,共收到 {delta_count} 个 delta 事件")
                    break

    if delta_count == 0:
        print("\n   ✗ 失败: 没有收到任何 message_delta 事件!")
        return False
    elif delta_count == 1:
        print("\n   ⚠️  只收到 1 个 delta,可能是一次性返回全部内容")
        return False
    else:
        print(f"\n   ✓ 成功: 收到 {delta_count} 个 delta 事件,流式输出正常!")
        return True

if __name__ == "__main__":
    print("=" * 60)
    print("流式输出测试 - 需要先启动 API 服务器")
    print("=" * 60)

    try:
        test_sse_streaming()
    except requests.exceptions.ConnectionError:
        print("\n✗ 无法连接到 API 服务器")
        print("  请先运行: cd backend && uvicorn app.main:app --reload")
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()

