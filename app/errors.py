"""Structured tool-error contract shared by every tool.

Every tool returns either a plain result dict, or a ToolError.to_dict()
when something goes wrong. The agent loop feeds this back to the model
as the tool result so the model can decide whether to retry, explain,
or escalate -- it never sees a raw traceback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorCategory = Literal["transient", "validation", "permission"]


@dataclass
class ToolError(Exception):
    error_category: ErrorCategory
    message: str
    is_retryable: bool
    tool_name: str = ""
    details: dict | None = None

    def to_dict(self) -> dict:
        return {
            "error": True,
            "errorCategory": self.error_category,
            "isRetryable": self.is_retryable,
            "message": self.message,
            "details": self.details or {},
        }


def transient(message: str, **details) -> ToolError:
    return ToolError(error_category="transient", message=message, is_retryable=True, details=details)


def validation(message: str, **details) -> ToolError:
    return ToolError(error_category="validation", message=message, is_retryable=False, details=details)


def permission(message: str, **details) -> ToolError:
    return ToolError(error_category="permission", message=message, is_retryable=False, details=details)
