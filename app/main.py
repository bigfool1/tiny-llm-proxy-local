from fastapi import FastAPI

from app.chat.router import router as chat_router
from app.skills.admin import router as skills_admin_router
from app.web.router import router as web_router


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Proxy Skill Runtime")
    app.include_router(web_router)
    app.include_router(chat_router)
    app.include_router(skills_admin_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
