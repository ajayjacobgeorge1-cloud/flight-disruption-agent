"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_api_base_url: str
    llm_api_key: str
    llm_model: str
    eval_judge_model: str
    rebooking_autonomy_threshold_usd: float
    max_agent_loop_iterations: int = 8
    request_timeout_s: int = 60


def load_settings() -> Settings:
    return Settings(
        llm_api_base_url=os.getenv(
            "LLM_API_BASE_URL",
            "http://llm-platform-alb-1231835738.us-east-1.elb.amazonaws.com/v1/chat/completions",
        ),
        llm_api_key=os.getenv("LLM_API_KEY", "PLACEHOLDER_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gemini-2.0-flash"),
        eval_judge_model=os.getenv("EVAL_JUDGE_MODEL", "gemini-2.0-flash"),
        rebooking_autonomy_threshold_usd=float(
            os.getenv("REBOOKING_AUTONOMY_THRESHOLD_USD", "750")
        ),
    )


SETTINGS = load_settings()
