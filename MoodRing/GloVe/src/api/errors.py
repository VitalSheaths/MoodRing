"""One typed construction boundary for existing HTTP error envelopes."""
from fastapi.responses import JSONResponse

from .api_models import ErrorResponse


def error_response(
    status_code: int, detail: str, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    """Build the unchanged {ok: false, detail: string} response and retain HTTP headers."""
    envelope = ErrorResponse(detail=detail)
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )
