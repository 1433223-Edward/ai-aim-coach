import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, field_validator

from agent.experiment_planner import (
    run_experiment_planner,
    ModelUnavailable,
    PlannerFailure,
)
from analysis.analysis_runtime import create_analysis_snapshot

import sqlite3
from backend.session_store import (
    get_session,
    init_database,
    save_session,
    get_experiment,
    get_or_create_experiment,
    load_experiment_evidence,
    ExperimentConflict,
    ExperimentNotFound,
)

from analysis.session_validator import (
    validate_session_integrity,
)

from pathlib import Path

from fastapi.staticfiles import StaticFiles
from backend.session_store import storage_ready
from agent.experiment_planner import model_settings
import importlib.util
import os
import json
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


@asynccontextmanager
async def lifespan(app):
    # Database initialization belongs to startup, not module import or model setup.
    init_database()
    yield


app = FastAPI(
    title="AI Aim Coach API",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(sqlite3.Error)
async def storage_error(request, exc):
    # Return a retryable storage failure without exposing paths or SQL values.
    return JSONResponse(status_code=503, content={"detail": "Storage is unavailable. Retry later."})


@app.exception_handler(json.JSONDecodeError)
async def corrupt_storage_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": "Stored data is invalid; preserve it for inspection."})


@app.exception_handler(ExperimentConflict)
async def evidence_conflict(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health")
def health():
    # Configuration readiness is local only; this endpoint never calls the paid provider.
    storage = storage_ready()
    try:
        model_settings()
        configured = bool(os.getenv("DASHSCOPE_API_KEY", "").strip()) and importlib.util.find_spec("openai") is not None
    except (ModelUnavailable, ValueError, ImportError):
        configured = False
    return JSONResponse(status_code=200 if storage else 503, content={
        "storage": "ready" if storage else "unavailable",
        "agent": "configured_not_probed" if configured else "unavailable",
    })


@app.exception_handler(RequestValidationError)
async def upload_validation_error(request, exc):
    if request.url.path not in ("/sessions", "/sessions/incomplete", "/experiments", "/agent/plan", "/scenarios/sessions", "/scenarios/experiments", "/agent/training-plans") and not request.url.path.startswith("/training-plans/"):
        return await request_validation_exception_handler(request, exc)
    # Do not echo malformed input (including NaN/Infinity) into JSON responses.
    return JSONResponse(status_code=422, content={
        "detail": [
            {key: error[key] for key in ("loc", "msg", "type")}
            for error in exc.errors()
        ],
    })

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

    model_config = ConfigDict(strict=True, extra="forbid")

    @field_validator("goal", "experiment_id")
    @classmethod
    def require_nonblank(cls, value):
        # Blank goals cannot guide planning; identity whitespace must not select another scope.
        if not value.strip():
            raise ValueError("Value must not be blank.")
        return value

    goal: str = Field(
        min_length=1,
        max_length=1000,
    )
    # Select evidence explicitly; a later upload must not change the experiment identity.
    experiment_id: str = Field(min_length=1)


from backend.settings import ExperimentSettings


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    trainer_width: int = Field(gt=50)
    trainer_height: int = Field(gt=50)
    settings: ExperimentSettings | None = None

class SessionUpload(BaseModel):
    """
    Trainer 上传的一次完整训练 Session。

    顶层不做隐式类型转换；嵌套 Raw JSON 由共享的
    Session Integrity Validator 验证，并在 Storage / Analysis 再次守卫。
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        strict=True,
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
    # Returned to the browser so it can detect stale recommendations.
    evidence: dict
    status: str
    request_id: str
    trace: list[dict]
    usage: dict


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
            request.goal, request.experiment_id,
        )
    except ExperimentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExperimentConflict as exc:
        # Empty, legacy or inconsistent experiments need user action, not another model call.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PlannerFailure as exc:
        # The runtime exposes only safe codes, trace metadata and a request ID.
        raise HTTPException(status_code=exc.http_status, detail=exc.detail) from None
    except Exception as exc:
        # 对服务器日志保留完整错误，
        # 但不把内部异常细节直接暴露给客户端。
        request_id = str(uuid4())
        logger.error("Agent planner failed request=%s type=%s.", request_id, type(exc).__name__)

        raise HTTPException(
            status_code=502,
            detail={"code": "internal_error", "message": "Agent planner failed.",
                    "request_id": request_id, "trace": []},
        ) from exc

    return PlanResponse(
        goal=request.goal,
        answer=result["answer"],
        tools_used=result["tools_used"],
        evidence=result["evidence"],
        status=result["status"], request_id=result["request_id"],
        trace=result["trace"], usage=result["usage"],
    )


@app.post("/experiments")
def create_or_resume_experiment(request: ExperimentRequest) -> dict:
    # Concurrent tabs share the one active experiment in this local single-user app.
    try:
        return get_or_create_experiment(request.trainer_width, request.trainer_height,
                                        request.settings.model_dump() if request.settings else None)
    except ExperimentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/experiments/current")
def read_current_experiment() -> dict:
    # A completed experiment remains selected across refresh until the next explicit start.
    try:
        state = get_experiment()
    except ExperimentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=404, detail="No managed experiment found.")
    return state


@app.get("/experiments/{experiment_id}")
def read_experiment(experiment_id: str) -> dict:
    try:
        state = get_experiment(experiment_id)
        if state is None:
            # Distinguish unknown identity from legacy rows that have no verified protocol.
            load_experiment_evidence(experiment_id)
        return state
    except ExperimentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExperimentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/experiments/{experiment_id}/analysis")
def read_analysis(experiment_id: str) -> dict:
    # Expose the existing deterministic capability without requiring any model configuration.
    try:
        snapshot = create_analysis_snapshot(experiment_id)
        return {"analysis": snapshot.result(), "evidence": snapshot.metadata()}
    except ExperimentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExperimentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    except ExperimentConflict as exc:
        # A stale condition may already have been saved by another tab.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except sqlite3.Error:
        # Let the shared handler classify locked or unavailable storage as retryable.
        raise

    except json.JSONDecodeError:
        raise

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

from fastapi import Query
from datetime import date as CalendarDate
from backend.session_store import save_incomplete_session, history_sessions, history_experiments, history_session_detail


@app.post("/sessions/incomplete", response_model=SessionSaveResponse)
def archive_incomplete(request: SessionUpload):
    try:
        save_incomplete_session(request.model_dump(by_alias=True))
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ExperimentConflict):raise HTTPException(409,str(exc)) from None
        raise HTTPException(422,"Invalid incomplete Session report.") from None
    return SessionSaveResponse(session_id=request.session_id,saved=True)


@app.post("/experiments/{experiment_id}/end")
def end_selected_experiment(experiment_id: str):
    from backend.session_store import end_experiment
    try:return end_experiment(experiment_id)
    except ExperimentNotFound:raise HTTPException(404,"Experiment not found.") from None


@app.get("/history/sessions")
def session_history(experiment_id: str | None = None, date: CalendarDate | None = None,
                    scene: str | None = None, limit: int = Query(100,ge=1,le=500), offset: int = Query(0,ge=0,le=1000000)):
    return history_sessions(experiment_id,date.isoformat() if date else None,scene,limit,offset)


@app.get("/history/experiments")
def experiment_history(date: CalendarDate | None = None, scene: str | None = None,
                       limit: int = Query(100,ge=1,le=500), offset: int = Query(0,ge=0,le=1000000)):
    return history_experiments(date.isoformat() if date else None,scene,limit,offset)


@app.get("/history/sessions/{session_id}")
def session_history_detail(session_id: str):
    try:return history_session_detail(session_id)
    except ExperimentNotFound:raise HTTPException(404,"Session not found.") from None
    except (ValueError,TypeError,KeyError,OverflowError):raise HTTPException(409,"Stored Session cannot be analyzed safely.") from None


@app.get("/history/experiments/{experiment_id}")
def experiment_history_detail(experiment_id: str):
    state=get_experiment(experiment_id)
    rows=history_sessions(experiment_id,limit=500)
    if state is None and not rows:raise HTTPException(404,"Experiment not found.")
    return {"experiment":state,"managed":state is not None,"sessions":rows,"protocol_verified":state is not None}


from analysis.scenarios import ScenarioConfig, registry as scenario_registry
from backend.scenario_store import save as save_scenario

class ScenarioStart(BaseModel):
    model_config=ConfigDict(strict=True,extra="forbid")
    trainer_width:int=Field(ge=100,le=10000)
    trainer_height:int=Field(ge=100,le=10000)
    scenario:ScenarioConfig
    preferences:ExperimentSettings | None = None

@app.get("/scenarios")
def list_scenarios():return scenario_registry()

@app.post("/scenarios/experiments")
def start_scenario(request:ScenarioStart):
    config=request.scenario.model_dump()
    from analysis.scenarios import snapshot,target_at
    try:target_at(snapshot(config),request.trainer_width,request.trainer_height,0)
    except ValueError:raise HTTPException(422,"Viewport too small for the selected difficulty.") from None
    # Carry user appearance into a new free-training snapshot without overriding scenario-owned counts or clocks.
    settings=request.preferences.model_dump() if request.preferences else {}
    settings.update(sessions=1,attempts=config["target_count"] if snapshot(config)["category"]=="static" else 1,
                    sensitivity=1.0,time_limit_seconds=0,countdown_seconds=0)
    state=get_or_create_experiment(request.trainer_width,request.trainer_height,settings,config)
    if state["protocol"].get("scenario")!=snapshot(config):raise HTTPException(409,"End the current experiment explicitly before changing scenarios.")
    # A restored experiment keeps its viewport; do not collect evidence that cannot be saved.
    if (state["protocol"]["trainer_width"],state["protocol"]["trainer_height"])!=(request.trainer_width,request.trainer_height):
        raise HTTPException(409,"Restore the experiment viewport or explicitly end it before starting another.")
    return state

@app.post("/scenarios/sessions")
def upload_scenario(request:SessionUpload):
    session=request.model_dump(by_alias=True)
    try:result=save_scenario(session)
    except ExperimentNotFound:raise HTTPException(404,"Experiment not found.") from None
    except ExperimentConflict as exc:raise HTTPException(409,str(exc)) from None
    except (ValueError,TypeError,KeyError,OverflowError):raise HTTPException(422,"Invalid scenario evidence.") from None
    return {"saved":True,"session_id":session["sessionId"],"result":result}

@app.get("/scenarios/sessions")
def scenario_history(limit:int=Query(50,ge=1,le=500)):
    from contextlib import closing
    from backend import session_store as store
    with closing(store._connect()) as connection:
        records=[]
        for row in connection.execute("SELECT * FROM scenario_sessions ORDER BY saved_at DESC,rowid DESC LIMIT ?",(limit,)):
            record={key:row[key] for key in ("session_id","experiment_id","scenario_id","status")}
            try:
                raw=json.loads(row["raw_data"])
                record.update(result=json.loads(row["result_json"]),parameters=raw["scenarioSnapshot"],
                              viewport=[raw["trainerWidth"],raw["trainerHeight"]],started_at=raw["startedAt"])
            except (ValueError,TypeError,KeyError):
                # A damaged row remains visible without blocking every other history card.
                record.update(status="abnormal",result=None,error="Stored scenario evidence is invalid.")
            records.append(record)
        return records

from backend import training_plans


def training_action(action, *args):
    """Preserve safe runtime failures and reject invalid plans before execution."""
    try:
        return action(*args)
    except ExperimentNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except ExperimentConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except PlannerFailure as exc:
        raise HTTPException(exc.http_status, exc.detail) from None
    except ModelUnavailable:
        raise HTTPException(503, "Model is unavailable; no recommendation was generated.") from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(422, "Invalid training plan or evidence.") from None


@app.post("/agent/training-plans")
def propose_training_plan(request: training_plans.TrainingRequest):
    return training_action(training_plans.create_plan, request)


@app.get("/training-plans/{plan_id}")
def read_training_plan(plan_id: str):
    return training_action(training_plans.get_plan, plan_id)


@app.get("/training-plans")
def training_plan_history(limit:int=Query(20,ge=1,le=50)):
    return training_action(training_plans.list_plans,limit)


@app.post("/training-plans/{plan_id}/accept")
def accept_training_plan(plan_id: str, request: training_plans.AcceptRequest):
    return training_action(training_plans.accept_plan, plan_id, request)


@app.post("/training-plans/{plan_id}/reject")
def reject_training_plan(plan_id: str):
    return training_action(training_plans.reject_plan, plan_id)


@app.post("/training-plans/{plan_id}/evaluate")
def evaluate_training_plan(plan_id: str):
    return training_action(training_plans.evaluate_plan, plan_id)


app.mount(
    "/",
    StaticFiles(
        directory=FRONTEND_DIR,
        html=True,
    ),
    name="frontend",
)
