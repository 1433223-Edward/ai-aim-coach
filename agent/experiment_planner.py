import json
import os

from openai import OpenAI

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


API_KEY = os.getenv("DASHSCOPE_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "DASHSCOPE_API_KEY is not set."
    )


client = OpenAI(
    api_key=API_KEY,

    # 保留你 qwen_test 已经验证成功的地址。
    base_url=(
        "https://dashscope.aliyuncs.com/"
        "compatible-mode/v1"
    ),
)


# 改成你刚才已经成功调用的真实模型 ID。
MODEL = "qwen3.8-flash"


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

def execute_tool(
    tool_name: str,
    arguments: dict,
) -> dict:

    if tool_name == "get_analysis_summary":
        return get_analysis_summary()

    if tool_name == "get_sensitivity_profile":
        return get_sensitivity_profile(
            sensitivity=arguments["sensitivity"],
        )

    if tool_name == "compare_sensitivities":
        return compare_sensitivities(
            sensitivities=arguments["sensitivities"],
        )

    raise ValueError(
        f"Unknown tool: {tool_name}"
    )


# ============================================================
# Agent Runtime
# ============================================================

def run_experiment_planner(
    user_goal: str,
) -> str:

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_goal,
        },
    ]
    # 保存本次 Agent Runtime 实际调用过的 Tool。
    #
    # 它不是分析结果本身，而是 Agent 执行轨迹的一部分。
    # 后续 API / Demo 可以展示：
    # “Agent 为了这个决策实际使用了哪些能力。”
    tools_used: list[str] = []

    # 防止模型因为异常行为无限 Tool Loop。
    max_tool_rounds = 6


    for round_index in range(max_tool_rounds):

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        assistant_message = (
            response.choices[0].message
        )


        # ====================================================
        # Case 1:
        # 没有新的 Tool Call
        #
        # 说明模型认为证据已经足够，
        # 当前 content 就是最终决策。
        # ====================================================

        if not assistant_message.tool_calls:
            return {
                "answer": (
                    assistant_message.content
                    or ""
                ),
                "tools_used": tools_used,
            }


        # ====================================================
        # Case 2:
        # 模型请求 Tool
        #
        # 必须先把 assistant 的 tool_calls
        # 放回 conversation history。
        # ====================================================

        messages.append({
            "role": "assistant",

            "content":
                assistant_message.content,

            "tool_calls": [
                {
                    "id": tool_call.id,

                    "type": "function",

                    "function": {
                        "name":
                            tool_call.function.name,

                        "arguments":
                            tool_call.function.arguments,
                    },
                }
                for tool_call
                in assistant_message.tool_calls
            ],
        })


        # ====================================================
        # 执行当前这一轮的所有 Tool Call
        # ====================================================

        for tool_call in assistant_message.tool_calls:

            tool_name = (
                tool_call.function.name
            )

            arguments = json.loads(
                tool_call.function.arguments
                or "{}"
            )

            # 记录 Agent 实际选择过的 Tool。
            tools_used.append(tool_name)

            # 当前保留最小 Observability（可观察性）。
            # 以后能直接看到 Agent 到底调用了什么。
            # print(
            #     f"[Tool Call] "
            #     f"{tool_name}"
            # )

            # if arguments:
            #     print(
            #         f"[Arguments] "
            #         f"{arguments}"
            #     )


            tool_result = execute_tool(
                tool_name=tool_name,
                arguments=arguments,
            )


            print(
                "[Tool Result] "
                "Structured evidence returned."
            )


            # ------------------------------------------------
            # Tool Result 回传模型
            # ------------------------------------------------

            messages.append({
                "role": "tool",

                "tool_call_id":
                    tool_call.id,

                "content": json.dumps(
                    tool_result,
                    ensure_ascii=False,
                ),
            })


        # ----------------------------------------------------
        # 不在这里 return。
        #
        # 下一轮循环重新调用模型，
        # 让它根据新 Evidence 判断：
        #
        # - 是否继续调用另一个 Tool
        # - 还是直接给最终答案
        # ----------------------------------------------------


    raise RuntimeError(
        "Agent exceeded maximum tool-call rounds."
    )


# ============================================================
# First Multi-tool Runtime
# ============================================================

if __name__ == "__main__":

    goal = (
        "我想提高 Flick 速度，"
        "但不希望明显牺牲稳定性。"
        "请根据已有实验结果，"
        "判断下一轮 sensitivity 实验最值得测试什么。"
    )

    result = run_experiment_planner(
        goal
    )

    print()
    print("=== Tools Used ===")
    print(result["tools_used"])

    print()
    print("=== Agent Decision ===")
    print(result["answer"])