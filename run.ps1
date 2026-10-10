# ============================================================
# AI Aim Coach
# Local Application Launcher
#
# 前提：
# 1. 已激活 aimcoach Conda 环境
# 2. DASHSCOPE_API_KEY 已配置
#
# 启动：
# FastAPI + Frontend + Backend API
# ============================================================

if (-not $env:DASHSCOPE_API_KEY) {
    Write-Error "DASHSCOPE_API_KEY is not configured."
    exit 1
}

Write-Host "Starting AI Aim Coach..."
Write-Host "Open: http://127.0.0.1:8000"

python -m uvicorn backend.main:app `
    --host 127.0.0.1 `
    --port 8000