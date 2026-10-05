from fastapi import APIRouter, HTTPException, Request

from app.db.database import database_ready

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    if not await database_ready(request.app.state.db_engine):
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok"}
