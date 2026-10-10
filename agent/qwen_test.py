import os

from openai import OpenAI


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


client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),

    # 千问AI平台提供的 OpenAI-compatible endpoint
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)


def main():
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