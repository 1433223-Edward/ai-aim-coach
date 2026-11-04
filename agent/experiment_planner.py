import json
import os
import math
import time
import hashlib
import sqlite3
from dataclasses import dataclass
from uuid import uuid4
from urllib.parse import urlsplit

from analysis.analysis_runtime import AnalysisSnapshot, create_analysis_snapshot
from backend.session_store import ExperimentNotFound, ExperimentConflict

from analysis.tools import (
    compare_sensitivities,
    get_analysis_summary,
    get_sensitivity_profile,
)


# ============================================================
# AI Aim Coach
# Sensitivity Experiment Planner v0.2
#
# User Goal
#      ↓
# Qwen
#      ↓
# Analysis Tools
#      ↓
# Structured Evidence
#      ↓
# Agent Decision
#
# 当前已经开放：
# 1. get_analysis_summary()
# 2. get_sensitivity_profile()
# 3. compare_sensitivities()
#
# Agent 不允许读取 Raw Mouse Events。
# ============================================================


# Model configuration is checked only when the Agent is actually requested.
MODEL = "qwen3.8-flash"


class ModelUnavailable(RuntimeError):
    """The optional model capability is not configured or installed."""


def model_settings():
    # Resolve configuration per request; never accept embedded credentials or query secrets.
    model = os.getenv("AIMCOACH_MODEL", MODEL).strip()
    endpoint = os.getenv("AIMCOACH_MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    try:
        parsed = urlsplit(endpoint)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment or not model:
            raise ValueError("Invalid model settings")
    except ValueError:
        raise ModelUnavailable("Model name or endpoint configuration is invalid.") from None
    return model, endpoint


def get_model_client():
    # Never retain or log the key; a missing key must not stop non-Agent features.
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise ModelUnavailable("DASHSCOPE_API_KEY is not configured. Training and analysis remain available.")
    _, endpoint = model_settings()
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key,
                      base_url=endpoint,
                      timeout=15, max_retries=0)
    except Exception:
        raise ModelUnavailable("Model client could not be initialized.") from None


# ============================================================
# Agent Instructions
# ============================================================

SYSTEM_PROMPT = """
You are the Sensitivity Experiment Planner inside AI Aim Coach.

Your responsibility is to decide what sensitivity experiment should
be run next based on:

1. the user's stated goal
2. deterministic analysis evidence returned by tools

Rules:

- Use tools whenever experimental evidence is needed.
- Never invent experimental results.
- Never calculate statistics yourself.
- Never request or analyze raw mouse movement data.
- Clearly distinguish observed evidence from your recommendation.
- Organize the answer into Observations, Recommendation, and Unknowns.
- Cite the experiment ID and snapshot ID, and name the metric and tested
  sensitivity behind each observation. Use only the supplied Session scope.
- Pooled Attempt statistics do not by themselves establish a cross-Session
  finding. Label a cross-Session claim only when the tool finding supports it.
- Respect all analysis limitations.
- Never claim that an untested sensitivity is better.
- Untested sensitivities may only be proposed as future experiment conditions.

- Before recommending the next sensitivity experiment, use comparison
  or profile tools when the summary alone does not contain enough
  metric evidence.

- Do not propose new analysis metrics unless the existing structured
  evidence is genuinely insufficient for the user's decision task.

- Do not rename or reinterpret a metric as a broader concept.
  For example:
  lower path efficiency means less direct movement paths.
  It does not automatically prove lower overall stability.

- Prefer small, informative follow-up experiments rather than testing
  many conditions.

The deterministic analysis layer decides what the data shows.
You decide what should be tested next.
"""


# ============================================================
# Tool Schemas
#
# 这里只负责告诉模型：
# “系统有哪些能力，以及怎么调用。”
#
# 真正的 Python 实现仍然在 analysis/tools.py。
# ============================================================

TOOLS = [
    # --------------------------------------------------------
    # Tool 1
    # --------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "get_analysis_summary",

            "description": (
                "Get the overall sensitivity experiment scope, "
                "deterministic findings, and evidence limitations. "
                "Use this to understand what evidence currently exists."
            ),

            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },

    # --------------------------------------------------------
    # Tool 2
    # --------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "get_sensitivity_profile",

            "description": (
                "Get the full deterministic analysis profile for one "
                "already-tested trainer sensitivity, including metrics, "
                "miss behavior, and session-level consistency."
            ),

            "parameters": {
                "type": "object",

                "properties": {
                    "sensitivity": {
                        "type": "number",

                        "description": (
                            "A trainer sensitivity that already exists "
                            "in the experiment."
                        ),
                    }
                },

                "required": [
                    "sensitivity"
                ],

                "additionalProperties": False,
            },
        },
    },

    # --------------------------------------------------------
    # Tool 3
    # --------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "compare_sensitivities",

            "description": (
                "Get deterministic comparison evidence for two or more "
                "already-tested trainer sensitivities. "
                "Use this when deciding trade-offs between tested conditions."
            ),

            "parameters": {
                "type": "object",

                "properties": {
                    "sensitivities": {
                        "type": "array",

                        "items": {
                            "type": "number"
                        },

                        "minItems": 2,

                        "description": (
                            "Two or more tested sensitivities "
                            "that should be compared."
                        ),
                    }
                },

                "required": [
                    "sensitivities"
                ],

                "additionalProperties": False,
            },
        },
    },
]


# ============================================================
# Tool Dispatcher
#
# Agent 只负责：
# “我要调用哪个工具、传什么参数。”
#
# 真正允许执行哪些 Python 函数，
# 仍然由我们自己的代码决定。
# ============================================================

@dataclass(frozen=True)
class RuntimeLimits:
    # These are operational budgets, not thresholds for statistical significance.
    total_seconds: float = 60
    call_seconds: float = 15
    model_calls: int = 6
    tool_calls: int = 12
    completion_tokens: int = 1000
    total_tokens: int = 128000
    provider_retries: int = 1


class PlannerFailure(RuntimeError):
    """A safe, inspectable failure; no unverified answer is returned."""
    def __init__(self, code, message, *, request_id, trace, evidence=None, http_status=409):
        super().__init__(message)
        self.detail = {"code": code, "message": message, "request_id": request_id,
                       "trace": trace, "evidence": evidence}
        self.http_status = http_status


def validate_tool_arguments(tool_name: str, arguments: object) -> dict:
    expected = {"get_analysis_summary": set(), "get_sensitivity_profile": {"sensitivity"},
                "compare_sensitivities": {"sensitivities"}}
    if tool_name not in expected:
        raise ValueError("Unknown tool.")
    if not isinstance(arguments, dict) or set(arguments) != expected[tool_name]:
        raise ValueError("Tool arguments have missing or extra fields.")
    values = []
    if tool_name == "get_sensitivity_profile":
        values = [arguments["sensitivity"]]
    elif tool_name == "compare_sensitivities":
        values = arguments["sensitivities"]
        if not isinstance(values, list) or len(values) < 2:
            raise ValueError("Comparison requires at least two distinct tested sensitivities.")
    # Reject bools, strings and nonfinite values before lookup or statistics access.
    try:
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise ValueError("Sensitivities must be finite numbers.")
    except OverflowError:
        raise ValueError("Sensitivities must be finite numbers.") from None
    if len(set(values)) != len(values):
        raise ValueError("Sensitivities must be unique.")
    return arguments


def execute_tool(tool_name: str, arguments: dict, *, snapshot: AnalysisSnapshot) -> dict:
    arguments = validate_tool_arguments(tool_name, arguments)
    # The model selects a projection, but cannot select a different evidence source.
    if tool_name == "get_analysis_summary":
        return get_analysis_summary(snapshot=snapshot)
    if tool_name == "get_sensitivity_profile":
        return get_sensitivity_profile(arguments["sensitivity"], snapshot=snapshot)
    return compare_sensitivities(arguments["sensitivities"], snapshot=snapshot)


def verify_tool_result(name, arguments, result, snapshot):
    """Check the returned evidence against the immutable source, not its label alone."""
    source = snapshot.result()
    # JSON equality preserves type differences such as true versus 1 in metric fields.
    same = lambda left, right: json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)
    if not isinstance(result, dict) or not same(result.get("evidence"), snapshot.metadata()):
        raise ValueError("Tool evidence identity is invalid.")
    profiles = {p["sensitivity"]: p for p in source["sensitivity_profiles"]}
    if name == "get_analysis_summary":
        expected = {key: source[key] for key in (
            "analysis_type", "schema_version", "experiment", "findings", "limitations")}
        # Reject extra assertions as well as changed metrics before forwarding to the model.
        valid = same(result, {**expected, "evidence": snapshot.metadata()})
    elif name == "get_sensitivity_profile":
        valid = same({k: v for k, v in result.items() if k != "evidence"}, profiles[arguments["sensitivity"]])
    else:
        requested = arguments["sensitivities"]
        related = [f for f in source["findings"]
                   if f.get("condition", {}).get("sensitivity") in requested
                   and set(f.get("comparison", {}).get("sensitivities", [])).intersection(requested)]
        valid = (set(result) == {"evidence", "sensitivities", "profiles", "relevant_findings", "limitations"}
                 and same(result.get("sensitivities"), requested)
                 and same(result.get("profiles"), [profiles[value] for value in requested])
                 and same(result.get("relevant_findings"), related)
                 and same(result.get("limitations"), source["limitations"]))
    if not valid:
        raise ValueError("Tool payload does not match the fixed evidence.")


def run_experiment_planner(user_goal: str, experiment_id: str, *, limits=None, clock=None) -> dict:
    limits = limits or RuntimeLimits()
    clock = clock or time.monotonic
    # All traces and errors refer to this request; never put provider exception text in them.
    request_id = str(uuid4())
    trace = []
    started = clock()
    evidence = None

    def fail(code, message, status=409):
        raise PlannerFailure(code, message, request_id=request_id, trace=trace,
                             evidence=evidence, http_status=status)

    # P0-2 owns this immutable snapshot until the request returns or fails.
    try:
        analysis_snapshot = create_analysis_snapshot(experiment_id)
    except ExperimentNotFound:
        fail("experiment_not_found", "Experiment not found.", 404)
    except ExperimentConflict:
        fail("invalid_evidence", "Experiment evidence is empty, invalid, or has no verified protocol.")
    except sqlite3.Error:
        fail("storage_unavailable", "Storage is unavailable. Retry later.", 503)
    evidence = analysis_snapshot.metadata()
    source = analysis_snapshot.result()
    tested = {p["sensitivity"] for p in source["sensitivity_profiles"]}
    if not tested or any(p["attempt_count"] <= 0 for p in source["sensitivity_profiles"]):
        fail("insufficient_evidence", "No valid metric profiles are available.")
    try:
        model, _ = model_settings()
        client = get_model_client()
    except ModelUnavailable:
        fail("model_unavailable", "Model configuration is unavailable; training and analysis remain usable.", 503)

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "Evidence scope: " + json.dumps(evidence)},
                    {"role": "user", "content": user_goal}]
        # A summary gives scope; metric tools must cover every tested condition before a plan is accepted.
        summary_seen = False
        covered = set()
        tools_used = []
        tool_count = 0
        tool_errors = 0
        evidence_corrections = 0
        retries = 0
        tokens_used = 0
        call_index = 0
        call_ids = set()
        # Keep provider timing and accounting separate from tool evidence trace entries.
        model_runs = []
        while call_index < limits.model_calls:
            remaining = limits.total_seconds - (clock() - started)
            if remaining <= 0:
                fail("deadline_exceeded", "Agent request exceeded its time budget.", 504)
            # Use UTF-8 bytes as a conservative fallback when the provider omits token usage.
            input_bound = len(json.dumps({"messages": messages, "tools": TOOLS}, ensure_ascii=False).encode())
            output_budget = min(limits.completion_tokens, limits.total_tokens - tokens_used - input_bound)
            if output_budget <= 0:
                fail("token_budget_exceeded", "Agent token budget exhausted.")
            final_round = call_index == limits.model_calls - 1
            call_index += 1
            model_started = clock()
            try:
                response = client.chat.completions.create(
                    model=model, messages=messages, tools=TOOLS,
                    tool_choice="none" if final_round else "auto",
                    timeout=min(limits.call_seconds, remaining), max_tokens=output_budget,
                )
            except Exception as exc:
                transient = (isinstance(exc, (TimeoutError, ConnectionError)) or
                             type(exc).__name__ in ("APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"))
                trace.append({"kind": "model", "round": call_index, "success": False,
                              "elapsed_ms": round((clock() - model_started) * 1000, 3),
                              "error": "transient_provider_error" if transient else "provider_error"})
                # Retry only transient provider failures, within the same call and deadline budgets.
                if transient and retries < limits.provider_retries and not final_round:
                    retries += 1
                    continue
                fail("provider_timeout" if isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError" else "provider_error",
                     "Model provider did not complete the request.", 504 if isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError" else 502)
            if clock() - started > limits.total_seconds:
                fail("deadline_exceeded", "Agent request exceeded its time budget.", 504)
            usage = getattr(response, "usage", None)
            reported_tokens = getattr(usage, "total_tokens", None)
            tokens_used += reported_tokens if type(reported_tokens) is int and reported_tokens > 0 else input_bound + output_budget
            model_runs.append({"round": call_index, "elapsed_ms": round((clock() - model_started) * 1000, 3),
                               "reported_tokens": reported_tokens if type(reported_tokens) is int and reported_tokens > 0 else None,
                               "accounting": "provider" if type(reported_tokens) is int and reported_tokens > 0 else "conservative_bytes"})
            if tokens_used > limits.total_tokens:
                fail("token_budget_exceeded", "Agent token budget exhausted.")
            try:
                choice = response.choices[0]
                message = choice.message
                calls = message.tool_calls if message.tool_calls is not None else []
                content = message.content
            except (AttributeError, IndexError, TypeError):
                fail("invalid_model_response", "Model returned an invalid message.", 502)
            finish = getattr(choice, "finish_reason", None)
            if finish in ("length", "content_filter") or getattr(message, "refusal", None):
                fail("invalid_model_response", "Model response was truncated or refused.", 502)
            if finish not in ("stop", "tool_calls") or not isinstance(calls, list) or (content is not None and not isinstance(content, str)):
                fail("invalid_model_response", "Model message fields are invalid.", 502)
            if (finish == "tool_calls" and not calls) or (finish == "stop" and calls):
                fail("invalid_model_response", "Model finish state disagrees with its message.", 502)
            if not calls:
                if not isinstance(content, str) or not content.strip():
                    fail("empty_answer", "Model returned no usable answer.", 502)
                if summary_seen and covered >= tested:
                    return {"answer": content, "tools_used": tools_used, "evidence": evidence,
                            "request_id": request_id, "status": "ok", "trace": trace,
                            "usage": {"model_calls": call_index, "tokens_accounted": tokens_used, "model": model,
                                  "model_runs": model_runs,
                                  "prompt_version": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                                  "tools_version": hashlib.sha256(json.dumps(TOOLS, sort_keys=True).encode()).hexdigest()}}
                # One bounded correction allows normal tool-calling models to obtain missing evidence.
                if evidence_corrections == 0 and not final_round:
                    evidence_corrections += 1
                    messages.extend([{"role": "assistant", "content": content},
                                     {"role": "system", "content": "Do not recommend yet. Use summary and metric tools to cover all tested conditions first. Partial experiments must be described as incomplete."}])
                    continue
                fail("insufficient_evidence", "Recommendation requires successful summary and metric evidence for all tested conditions.")
            if final_round:
                fail("round_budget_exceeded", "Model requested tools after the final answer round.")
            if not isinstance(calls, list):
                fail("invalid_tool_call", "Tool call list is invalid.", 502)
            serialized = []
            for call in calls:
                try:
                    call_id, name, raw = call.id, call.function.name, call.function.arguments
                    if not all(isinstance(v, str) and v for v in (call_id, name)) or not isinstance(raw, str) or call_id in call_ids:
                        raise ValueError()
                    call_ids.add(call_id)
                    serialized.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}})
                except (AttributeError, TypeError, ValueError):
                    fail("invalid_tool_call", "Tool message is malformed.", 502)
            messages.append({"role": "assistant", "content": content, "tool_calls": serialized})
            for call in serialized:
                tool_count += 1
                if tool_count > limits.tool_calls:
                    fail("tool_budget_exceeded", "Agent tool budget exhausted.")
                name = call["function"]["name"]
                arguments = None
                safe_arguments = None
                tool_started = clock()
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                    validate_tool_arguments(name, arguments)
                    safe_arguments = arguments
                    tool_result = execute_tool(name, arguments, snapshot=analysis_snapshot)
                    verify_tool_result(name, arguments, tool_result, analysis_snapshot)
                    success = True
                except Exception:
                    # Failed attempts are sent as explicit errors, never as analysis evidence.
                    success = False
                    tool_errors += 1
                    tool_result = {"error": {"code": "tool_error", "message": "Invalid tool arguments or result. Use a supported tool with tested sensitivities."}}
                trace.append({"kind": "tool", "tool": name, "arguments": safe_arguments,
                              "success": success, "snapshot_id": evidence["snapshot_id"],
                              "elapsed_ms": round((clock() - tool_started) * 1000, 3),
                              "error": None if success else "tool_error"})
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(tool_result, ensure_ascii=False, allow_nan=False)})
                if not success:
                    if tool_errors > 1:
                        fail("tool_error", "Tool recovery failed; no recommendation was accepted.")
                    continue
                tools_used.append(name)
                if name == "get_analysis_summary":
                    summary_seen = True
                elif name == "get_sensitivity_profile":
                    covered.add(arguments["sensitivity"])
                else:
                    covered.update(arguments["sensitivities"])
        fail("round_budget_exceeded", "Agent exceeded its model-call budget.")

    finally:
        # Each request owns its HTTP pool, including failed and cancelled runs.
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup must not replace the accepted result or the original safe failure.
                pass


# ============================================================
# First Multi-tool Runtime
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id", help="Managed experiment to analyze; never defaults to latest.")
    args = parser.parse_args()

    goal = (
        "我想提高 Flick 速度，"
        "但不希望明显牺牲稳定性。"
        "请根据已有实验结果，"
        "判断下一轮 sensitivity 实验最值得测试什么。"
    )

    result = run_experiment_planner(
        goal, args.experiment_id,
    )

    print()
    print("=== Tools Used ===")
    print(result["tools_used"])

    print()
    print("=== Agent Decision ===")
    print(result["answer"])
