"""校园办事 AI 助手：指南查询、用户认证及个人会话历史。"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from redis.exceptions import RedisError

from app.auth import router as auth_router
from app.cache import get_guide_cache
from app.conversations import router as conversations_router
from app.chat import router as chat_router
from app.context_cache import get_context_cache
from app.database import engine
from app.guides import router
from app.config import PROJECT_ROOT
from app.references import router as references_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 构建客户端不会连接 Redis；Redis 离线也允许应用启动。
    cache = get_guide_cache()
    context_cache = get_context_cache()
    try:
        yield
    finally:
        cache.client.close()
        get_guide_cache.cache_clear()
        context_cache.client.close()
        get_context_cache.cache_clear()
        engine.dispose()


app = FastAPI(title="校园办事 AI 助手", version="0.5.0", lifespan=lifespan)
app.include_router(router)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(references_router)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "web"), name="static")


@app.get("/", include_in_schema=False)
def chat_page():
    return FileResponse(PROJECT_ROOT / "app" / "web" / "index.html", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
    })


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # 默认校验错误可能带入请求内容；去除 input/ctx，避免回显密码或密钥。
    return JSONResponse(status_code=422, content={
        "detail": [
            {key: error[key] for key in ("loc", "msg", "type")}
            for error in exc.errors()
        ]
    })


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    logging.getLogger(__name__).warning("数据库操作失败（%s）", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试"})


@app.get("/health", summary="检查应用是否正常运行")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", summary="数据库就绪检查；Redis 故障时降级")
def ready():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unavailable"})
    try:
        get_context_cache().client.ping()
        redis_status = "ok"
    except RedisError:
        redis_status = "unavailable"
    return {"status": "ready", "database": "ok", "redis": redis_status}
