#!/bin/bash
echo "=== 启动后端服务 ==="
cd backend
python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "后端 PID: $BACKEND_PID"

echo ""
echo "=== 启动前端服务 ==="
cd ../frontend
npm run dev &
FRONTEND_PID=$!
echo "前端 PID: $FRONTEND_PID"

echo ""
echo "✅ 服务已启动!"
echo "前端: http://localhost:5173"
echo "后端: http://localhost:8000"
echo ""
echo "测试步骤:"
echo "1. 打开浏览器访问 http://localhost:5173"
echo "2. 发送一条消息"
echo "3. 观察 AI 响应是否逐字符显示(打字机效果)"
echo "4. 检查浏览器开发者工具 Network > EventSource,确认收到 message_delta 事件"
echo ""
echo "按 Ctrl+C 停止服务"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
