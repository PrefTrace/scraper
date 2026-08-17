from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Diagnostic(BaseModel):
    source: str
    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.WARNING
    details: dict[str, Any] = Field(default_factory=dict)

