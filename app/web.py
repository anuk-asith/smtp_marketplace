"""
Minimal FastAPI web server.
No payment gateway callbacks needed — payments are confirmed manually by admin.
"""
from fastapi import FastAPI
from .config import get_settings

cfg = get_settings()
web = FastAPI(title=f"{cfg.shop_name}")


@web.get("/health")
async def health() -> dict:
    return {"ok": True, "service": cfg.shop_name}
