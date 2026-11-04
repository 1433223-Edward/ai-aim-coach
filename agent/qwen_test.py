import os

from agent.experiment_planner import get_model_client


# ============================================================
# AI Aim Coach
# Qwen API Smoke Test
#
# 当前只验证：
#
# Python
#   ↓
# 千问AI API
#   ↓
# qwen3.8-flash
#   ↓
# Text Response
#
# 暂时不接 Analysis Tool。
# ============================================================


def main():
    # Real provider smoke is explicit and never runs during test discovery.
    if os.getenv("AIMCOACH_LIVE_SMOKE") != "1":
        raise SystemExit("Set AIMCOACH_LIVE_SMOKE=1 to opt into a real model request.")
    client = get_model_client()
    response = client.chat.completions.create(
        # 当前只追求低成本 MVP
        model="qwen3.8-flash",

        messages=[
            {
                "role": "system",
                "content": (
                    "You are a minimal AI component "
                    "inside AI Aim Coach."
                ),
            },
            {
                "role": "user",
                "content": (
                    "只回复：AI Aim Coach API connected."
                ),
            },
        ],
    )

    print(
        response.choices[0].message.content
    )


if __name__ == "__main__":
    main()