from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Business error with a stable code translated by the UI (ru/en)."""

    status_code = 400

    def __init__(self, code: str, message: str = "", status_code: int | None = None, **params: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.params = params
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "params": self.params}


class NotFound(AppError):
    status_code = 404

    def __init__(self, what: str = "object") -> None:
        super().__init__("not_found", f"{what} not found", what=what)


class Forbidden(AppError):
    status_code = 403

    def __init__(self, message: str = "Access denied") -> None:
        super().__init__("forbidden", message)


class Conflict(AppError):
    status_code = 409


class QuotaExceeded(AppError):
    status_code = 409

    def __init__(self, resource: str, limit: float, used: float, requested: float) -> None:
        super().__init__(
            "quota_exceeded",
            f"Quota exceeded for {resource}: limit {limit}, used {used}, requested {requested}",
            resource=resource,
            limit=limit,
            used=used,
            requested=requested,
            available=max(0, limit - used),
        )
