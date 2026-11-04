# ============================================================
# AI Aim Coach
# Local Application Launcher
#
# 前提：
# 1. 已激活 aimcoach Conda 环境
# 2. DASHSCOPE_API_KEY is optional; only Agent planning needs it.
#
# 启动：
# FastAPI + Frontend + Backend API
# ============================================================

if (-not $env:DASHSCOPE_API_KEY) {
    Write-Warning "Agent planning is unavailable without DASHSCOPE_API_KEY; training and analysis can still run."
}

Write-Host "Starting AI Aim Coach..."
Write-Host "Open: http://127.0.0.1:8000"

# Resolve imports from the repository even when launched from another directory.
Push-Location $PSScriptRoot
try {
python -m uvicorn backend.main:app `
    --host 127.0.0.1 `
    --port 8000
} finally {
    Pop-Location
}
