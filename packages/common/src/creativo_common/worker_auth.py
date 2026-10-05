import hashlib
import hmac
import time

MAX_SKEW_SECONDS = 120


class WorkerAuthError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def sign(secret: str, timestamp: int, job_id: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}.{job_id}.{digest}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signature_headers(
    secret: str,
    job_id: str,
    body: bytes,
    *,
    now: int | None = None,
) -> dict[str, str]:
    timestamp = int(time.time()) if now is None else now
    return {
        "X-Creativo-Timestamp": str(timestamp),
        "X-Creativo-Job-Id": job_id,
        "X-Creativo-Signature": sign(secret, timestamp, job_id, body),
    }


def verify(
    secret: str,
    *,
    timestamp: str | None,
    job_id: str | None,
    signature: str | None,
    body: bytes,
    now: int | None = None,
) -> None:
    if not timestamp or not job_id or not signature:
        raise WorkerAuthError("missing_signature")
    try:
        issued = int(timestamp)
    except ValueError as exc:
        raise WorkerAuthError("invalid_timestamp") from exc
    current = int(time.time()) if now is None else now
    if abs(current - issued) > MAX_SKEW_SECONDS:
        raise WorkerAuthError("timestamp_expired")
    expected = sign(secret, issued, job_id, body)
    if not hmac.compare_digest(expected, signature):
        raise WorkerAuthError("bad_signature")
