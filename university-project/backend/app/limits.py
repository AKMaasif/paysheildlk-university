"""Size limits for what a browser may send. A camera frame is a JPEG of about 50 to 300 KB; these limits sit well above
that and well below what could exhaust memory."""
from typing import Annotated

from fastapi import HTTPException
from pydantic import StringConstraints

MAX_FRAMES = 20
FRAME_MAX_CHARS = 600_000  # one base64 frame, about 450 KB of image
TOTAL_FRAME_CHARS = 6_000_000  # all frames of one request, about 4.5 MB of image
MAX_BODY_BYTES = 8 * 1024 * 1024  # any request

Frame = Annotated[str, StringConstraints(max_length=FRAME_MAX_CHARS)]


def check_frames_total(frames: list[str]) -> None:
    if sum(len(item) for item in frames) > TOTAL_FRAME_CHARS:
        raise HTTPException(status_code=413, detail="The camera images are too large. Please scan again.")


class BodyLimit:
    """Refuses a request whose body is larger than MAX_BODY_BYTES, by its declared length and by what actually arrives."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            return await self._refuse(send)
        seen = 0

        async def counted():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    return {"type": "http.disconnect"}  # stop reading; the handler never sees the rest
            return message

        return await self.app(scope, counted, send)

    @staticmethod
    async def _refuse(send):
        body = b'{"detail":"That request is too large."}'
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
