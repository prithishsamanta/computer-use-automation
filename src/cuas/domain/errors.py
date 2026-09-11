"""Hard-failure error codes from .CLAUDE/06_ERRORS_AND_OBSERVABILITY.md.

These are distinct from business outcomes (which are successful, structured
results, not exceptions) and from capability routing outcomes (NO_CAPABILITY_MATCH
-> DISCOVERY_REQUIRED, which is normal control flow handled by CapabilityService).
AutomationError and its subclasses model only the "system failed" category.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    CHECKPOINT_FAILED = "CHECKPOINT_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    OUTPUT_EXTRACTION_FAILED = "OUTPUT_EXTRACTION_FAILED"


class AutomationError(Exception):
    """Base for the hard-failure taxonomy. Every subclass sets `code`."""

    code: ErrorCode

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TargetNotFoundError(AutomationError):
    code = ErrorCode.TARGET_NOT_FOUND


class ArtifactInvalidError(AutomationError):
    code = ErrorCode.ARTIFACT_INVALID


class CheckpointFailedError(AutomationError):
    code = ErrorCode.CHECKPOINT_FAILED


class OutputExtractionFailedError(AutomationError):
    code = ErrorCode.OUTPUT_EXTRACTION_FAILED
