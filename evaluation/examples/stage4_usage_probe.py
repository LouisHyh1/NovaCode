"""有限用量核验：同一公开文本两次 Anthropic、一次原生用量对照。"""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import anthropic
import openai

from novacode.config import load


async def main():
    output = Path(sys.argv[1])
    output.mkdir(exist_ok=False)
    budget = {
        "provider_calls": 3,
        "max_output_tokens": 128,
        "seconds_per_request": 30,
        "input": "固定公开重复文本，不超过 12000 UTF-8 字节",
        "estimated_total_token_ceiling": 20_000,
    }
    (output / "budget.json").write_text(json.dumps(budget, ensure_ascii=False, indent=2) + "\n")
    cfg = next(
        p
        for p in load(".novacode/config.yaml").providers
        if p.model == "deepseek-v4-flash" and p.protocol == "anthropic"
    )
    cfg = replace(cfg, max_retries=0, timeout=30, max_output_tokens=128)
    content = "This is a public token accounting probe. " * 200 + "Reply only READY."
    rows = []
    async with anthropic.AsyncAnthropic(
        api_key=cfg.api_key, base_url=cfg.base_url, max_retries=0, timeout=30
    ) as client:
        for index in range(2):
            response = await client.messages.create(
                model=cfg.model,
                max_tokens=128,
                messages=[{"role": "user", "content": content}],
                thinking={"type": "disabled"},
            )
            rows.append(
                {
                    "protocol": "anthropic",
                    "index": index,
                    "actual_model": response.model,
                    "raw_usage": response.usage.model_dump(),
                    "text": [b.text for b in response.content if b.type == "text"],
                }
            )
    async with openai.AsyncOpenAI(
        api_key=cfg.api_key, base_url="https://api.deepseek.com", max_retries=0, timeout=30
    ) as client:
        response = await client.chat.completions.create(
            model=cfg.model,
            max_tokens=128,
            messages=[{"role": "user", "content": content}],
            extra_body={"thinking": {"type": "disabled"}},
        )
        rows.append(
            {
                "protocol": "openai",
                "actual_model": response.model,
                "raw_usage": response.usage.model_dump(),
            }
        )
    (output / "usage.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
