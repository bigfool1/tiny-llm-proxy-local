from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])
WEB_INDEX = Path(__file__).with_name("index.html")


@router.get("/", response_class=HTMLResponse)
async def web_chat() -> str:
    return WEB_INDEX.read_text(encoding="utf-8")
