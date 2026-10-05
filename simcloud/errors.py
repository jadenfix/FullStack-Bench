"""The error catalogue. Every error the API returns has a stable code, an
HTTP status and a description; the skill docs are generated from this table so
agents can look up what an error means."""

from dataclasses import dataclass, field

CATALOGUE: dict[str, tuple[int, str]] = {
    "invalid_request": (400, "The request body or parameters failed validation."),
    "unauthenticated": (401, "No valid token was presented, or the token has expired."),
    "access_denied": (403, "The principal's policies do not allow this action on this resource."),
    "not_found": (404, "The resource does not exist in this project and environment."),
    "conflict": (409, "The resource already exists, or the request conflicts with its current state."),
    "precondition_failed": (412, "The supplied version does not match the resource's current version."),
    "unprocessable": (422, "The request is well formed but cannot be applied (for example an invalid spec)."),
    "quota_exceeded": (429, "A project quota would be exceeded."),
    "throttled": (429, "Too many requests. Retry after the number of seconds in Retry-After."),
    "unavailable": (503, "The region or service is temporarily unavailable. Retry with backoff."),
}


@dataclass
class SimCloudError(Exception):
    code: str
    message: str
    details: dict = field(default_factory=dict)
    retry_after: float | None = None

    def __post_init__(self):
        if self.code not in CATALOGUE:
            raise ValueError(f"unknown error code {self.code!r}")
        super().__init__(self.message)

    @property
    def status(self) -> int:
        return CATALOGUE[self.code][0]

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}
