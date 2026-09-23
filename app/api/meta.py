from fastapi import APIRouter

from app import engine
from app.contracts import Meta
from app.store import store

router = APIRouter()


@router.get("/meta", response_model=Meta)
def meta():
    return engine.meta(store.datasets["default"])
