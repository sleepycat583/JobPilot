from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "retryable": retryable},
    )
