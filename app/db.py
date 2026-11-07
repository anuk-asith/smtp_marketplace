"""
Database engine & session factory.
Supports both PostgreSQL (asyncpg) and SQLite (aiosqlite) via DATABASE_URL.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    cfg = get_settings()
    kwargs: dict = {"pool_pre_ping": True}
    if cfg.database_url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
    return create_async_engine(cfg.database_url, **kwargs)


engine = _make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _migrate_sync(sync_conn) -> None:
    try:
        res = sync_conn.exec_driver_sql("PRAGMA table_info(products)").fetchall()
        col_names = {row[1] for row in res}
        if col_names:
            if "is_archived" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE products ADD COLUMN is_archived BOOLEAN DEFAULT 0")
            if "allow_backorder" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE products ADD COLUMN allow_backorder BOOLEAN DEFAULT 1")
            if "backorder_eta" not in col_names:
                sync_conn.exec_driver_sql("ALTER TABLE products ADD COLUMN backorder_eta VARCHAR(100) DEFAULT '24-48 hours'")

        res_orders = sync_conn.exec_driver_sql("PRAGMA table_info(orders)").fetchall()
        order_cols = {row[1] for row in res_orders}
        if order_cols:
            if "is_backorder" not in order_cols:
                sync_conn.exec_driver_sql("ALTER TABLE orders ADD COLUMN is_backorder BOOLEAN DEFAULT 0")
    except Exception:
        pass


async def init_db() -> None:
    from . import models  # noqa: F401 – registers all ORM models
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sync)
