"""telco routes (split from app/api.py)."""
import logging

from fastapi import (
    APIRouter,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.facts import InvalidPhone, UnknownPhone, check_sim_swap, retrieve_sim_swap_date

log = logging.getLogger("payshield.api")
router = APIRouter()


class CheckIn(BaseModel):
    phoneNumber: str
    maxAge: int | None = None


class PhoneIn(BaseModel):
    phoneNumber: str


def _camara_error(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"status": status, "code": code, "message": message})


@router.post("/sim-swap/v1/check")
def camara_check(body: CheckIn):
    try:
        result = check_sim_swap(body.phoneNumber, body.maxAge)
    except InvalidPhone:
        return _camara_error(400, "INVALID_ARGUMENT", "phoneNumber must be E.164")
    except UnknownPhone:
        return _camara_error(404, "NOT_FOUND", "SIM swap data is not available for this phone number.")
    except ValueError as exc:
        return _camara_error(400, "INVALID_ARGUMENT", str(exc))
    return {"swapped": result["swapped"]}


@router.post("/sim-swap/v1/retrieve-date")
def camara_date(body: PhoneIn):
    try:
        result = retrieve_sim_swap_date(body.phoneNumber)
    except InvalidPhone:
        return _camara_error(400, "INVALID_ARGUMENT", "phoneNumber must be E.164")
    except UnknownPhone:
        return _camara_error(404, "NOT_FOUND", "SIM swap data is not available for this phone number.")
    return {"latestSimChange": result["latestSimChange"]}
