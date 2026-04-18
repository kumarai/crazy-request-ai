"""Base types for customer-support tools."""
from __future__ import annotations

from pydantic import BaseModel


class SupportToolResult(BaseModel):
    """Standard envelope for all support tool outputs."""
    tool_name: str
    success: bool = True
    data: dict = {}
    error: str | None = None
