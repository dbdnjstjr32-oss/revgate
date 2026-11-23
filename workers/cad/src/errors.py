"""Standard worker error codes (IMPLEMENTATION_BLUEPRINT §5.3).

* Crashes of the isolated job subprocess are mapped from its exit status via EXIT_CODE_MAP
  (negative = killed by signal N on POSIX, 128+N = the same signal reported by a shell/container).
* Domain errors are raised inside the job as CadDomainError and travel back through
  job_result.json with exit status EXIT_DOMAIN_ERROR.
* Warnings never fail a job; they are surfaced in analytical_meta.summary.warnings and in the
  job result.
"""

from __future__ import annotations

from typing import Any

# --- verbatim from the blueprint -------------------------------------------------------------
EXIT_CODE_MAP = {
    -9: "OOM_KILLED", 137: "OOM_KILLED",
    -11: "SEGFAULT_OCCT", 139: "SEGFAULT_OCCT",
    -6: "SEGFAULT_OCCT", 134: "SEGFAULT_OCCT",
}
# TimeoutExpired(60s) -> "TIMEOUT_WORKER_60S"
TIMEOUT_ERROR_CODE = "TIMEOUT_WORKER_60S"
DOMAIN_ERRORS = [
    "INVALID_STEP_SYNTAX", "EMPTY_GEOMETRY", "TOO_MANY_SOLIDS",
    "TOO_MANY_FACES", "RAW_FILE_NOT_FOUND", "SHA256_MISMATCH",
]
WARNINGS = [
    "OPEN_SHELL_ONLY", "HEALING_APPLIED", "INCH_SOURCE_CONVERTED",
    "HELICAL_THREADS_DETECTED", "BOOLEAN_FALLBACK_HEATMAP",
]

# --- additions (not in the blueprint list) ---------------------------------------------------
# Any other non-zero exit (uncaught Python exception = interpreter exit 1, malformed job spec, ...).
WORKER_INTERNAL_ERROR = "WORKER_INTERNAL_ERROR"
EXIT_DOMAIN_ERROR = 3  # child exit status for CadDomainError (result file carries the code)


class CadDomainError(Exception):
    """A deterministic, user-facing failure of a CAD job (bad input, limits exceeded, ...)."""

    def __init__(self, code: str, message: str = "", **details: Any) -> None:
        if code not in DOMAIN_ERRORS:
            raise ValueError(f"unknown domain error code {code!r}")
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message or code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.code, "message": self.message, "details": self.details}


def map_exit_code(returncode: int) -> str | None:
    """Error code for a crashed job subprocess, or None when the status is not a known crash."""
    return EXIT_CODE_MAP.get(returncode)
