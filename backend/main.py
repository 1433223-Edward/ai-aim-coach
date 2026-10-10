import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict

from agent.experiment_planner import (
    run_experiment_planner,
)

import sqlite3
from backend.session_store import (
    get_session,
    init_database,
    save_session,
)

from analysis.session_validator import (
    validate_session_integrity,
)

from pathlib import Path

from fastapi.staticfiles import StaticFiles
# ============================================================
# AI Aim Coach
# Application API v0.1
#
# 当前职责：
#
# HTTP Request
#     ↓
# FastAPI
#     ↓
# Agent Runtime
#     ↓
# HTTP Response
#
# FastAPI 不负责分析数据，
# 也不负责 Agent 决策。
#
# 它只是把已经存在的 Agent Capability
# 暴露成稳定的 Application Interface。
# ============================================================

# ============================================================
# Frontend Path
#
# backend/main.py
#       ↓ parents[1]
# AimCoach/
#       ↓
# frontend/
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FRONTEND_DIR = PROJECT_ROOT / "frontend"

logger = logging.getLogger(__name__)


app = FastAPI(
    title="AI Aim Coach API",
    version="0.1.0",
)

init_database()

# ============================================================
# Local Frontend Access
#
# Trainer 前端和 FastAPI 当前运行在不同端口：
#
# Frontend: http://127.0.0.1:5500
# Backend:  http://127.0.0.1:8000
#
# 浏览器会把它们视为不同 Origin，
# 因此本地开发阶段允许 Trainer 调用 API。
# 架构变了所以不需要cors了
# ============================================================

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=[
#         "http://127.0.0.1:5500",
#         "http://localhost:5500",
#     ],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# ============================================================
# API Contract
# ============================================================

class PlanRequest(BaseModel):
    """
    客户端告诉 Agent:
    用户现在希望下一轮训练解决什么问题。
    """

    goal: str = Field(
        min_length=1,
        max_length=1000,
    )

class SessionUpload(BaseModel):
    """
    Trainer 上传的一次完整训练 Session。

    当前只严格验证 Analysis / Storage 真正依赖的
    顶层字段。

    attempts 内部暂时保持 Raw JSON,
    不在 API 层重复定义完整 Collector Schema。
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )

    schema_version: str = Field(
        alias="schemaVersion"
    )

    session_id: str = Field(
        alias="sessionId"
    )

    experiment_id: str = Field(
    alias="experimentId"
    )

    started_at: str = Field(
        alias="startedAt"
    )

    ended_at: str | None = Field(
        default=None,
        alias="endedAt",
    )

    trainer_sensitivity: float = Field(
        alias="trainerSensitivity"
    )

    target_count: int = Field(
        alias="targetCount",
        gt=0,
    )

    experiment_order: int | None = Field(
        default=None,
        alias="experimentOrder",
    )

    sensitivity_repeat: int | None = Field(
        default=None,
        alias="sensitivityRepeat",
    )

    attempts: list[dict]


class SessionSaveResponse(BaseModel):
    session_id: str
    saved: bool

class PlanResponse(BaseModel):
    """
    API 对外暴露的稳定结果。

    answer:
        Agent 给用户的最终实验建议。

    tools_used:
        Agent 在本轮决策中实际调用过的工具。
        用于 Debug / Demo / Observability。

    不直接暴露完整 Tool Result,
    避免前端依赖 Analysis Layer 的内部数据结构。
    """

    goal: str
    answer: str
    tools_used: list[str]


# ============================================================
# Agent Planner Endpoint
# ============================================================

@app.post(
    "/agent/plan",
    response_model=PlanResponse,
)
def plan_next_experiment(
    request: PlanRequest,
) -> PlanResponse:
    """
    将用户 Goal 交给已经存在的 Agent Runtime。

    Data Flow:

    HTTP Request
        ↓
    PlanRequest
        ↓
    run_experiment_planner()
        ↓
    Qwen + Analysis Tools
        ↓
    PlanResponse
    """

    try:
        result = run_experiment_planner(
            request.goal
        )

    except Exception as exc:
        # 对服务器日志保留完整错误，
        # 但不把内部异常细节直接暴露给客户端。
        logger.exception(
            "Agent planner failed."
        )

        raise HTTPException(
            status_code=502,
            detail="Agent planner failed.",
        ) from exc

    return PlanResponse(
        goal=request.goal,
        answer=result["answer"],
        tools_used=result["tools_used"],
    )

@app.post(
    "/sessions",
    response_model=SessionSaveResponse,
)
def create_session(
    request: SessionUpload,
) -> SessionSaveResponse:
    """
    保存 Trainer 产生的一次完整 Raw Session。

    当前流程：

    Trainer JSON
        ↓
    Pydantic 顶层验证
        ↓
    SQLite
    """

    # 使用 alias 重新生成原始 Trainer 字段名：
    #
    # session_id
    #     ↓
    # sessionId
    #
    # 这样 SQLite raw_data 保存的 Schema
    # 与 Trainer 原始 JSON 保持一致。
    session_data = request.model_dump(
        by_alias=True,
    )

    # ========================================================
    # Raw Data Integrity Check
    #
    # 数据进入 SQLite 之前先验证：
    # “这是不是一份完整的 Training Session？”
    # ========================================================

    validation_errors = (
        validate_session_integrity(
            session_data
        )
    )


    if validation_errors:

        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Session integrity "
                    "validation failed."
                ),
                "errors": validation_errors,
            },
        )

    try:
        save_session(
            session_data
        )

    except sqlite3.IntegrityError as exc:

        # session_id 是 PRIMARY KEY。
        # 同一个 Session 不应该被静默覆盖。
        raise HTTPException(
            status_code=409,
            detail="Session already exists.",
        ) from exc

    except Exception as exc:

        logger.exception(
            "Failed to save training session."
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to save training session.",
        ) from exc

    return SessionSaveResponse(
        session_id=request.session_id,
        saved=True,
    )

@app.get(
    "/sessions/{session_id}",
)
def read_session(
    session_id: str,
) -> dict:

    session = get_session(
        session_id
    )

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found.",
        )

    return session

# ============================================================
# Frontend Hosting
#
# 必须放在 API routes 后面。
#
# 因为 "/" 是 catch-all static route，
# 如果放得太早，可能会拦截 /sessions、
# /agent/plan 等 API。
# ============================================================

app.mount(
    "/",
    StaticFiles(
        directory=FRONTEND_DIR,
        html=True,
    ),
    name="frontend",
)
