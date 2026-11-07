"""
Inventory / stock management helpers.
All credential payloads are stored Fernet-encrypted at rest.
"""
from __future__ import annotations

import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Inventory
from .security import encrypt, decrypt

log = logging.getLogger(__name__)


async def count_stock(db: AsyncSession, product_id: int) -> int:
    """Return the number of available units for a product."""
    return (
        await db.scalar(
            select(func.count(Inventory.id)).where(
                Inventory.product_id == product_id,
                Inventory.status == "available",
            )
        )
    ) or 0


async def add_stock(db: AsyncSession, product_id: int, payload: dict | str) -> Inventory:
    """
    Encrypt and store one credential set.
    ``payload`` can be a dict or a plain string (e.g. host:port:user:pass).
    """
    row = Inventory(
        product_id=product_id,
        encrypted_payload=encrypt(payload),
    )
    db.add(row)
    await db.flush()
    log.info("Stock item %d added for product %d", row.id, product_id)
    return row


async def add_stock_bulk(
    db: AsyncSession, product_id: int, payloads: list[dict | str]
) -> int:
    """Add multiple credential sets at once. Returns count added."""
    for payload in payloads:
        await add_stock(db, product_id, payload)
    return len(payloads)


async def reserve(
    db: AsyncSession, product_id: int, order_id: int, quantity: int = 1
) -> list[Inventory]:
    """
    Atomically reserve *quantity* available items for an order.
    Uses SELECT … FOR UPDATE SKIP LOCKED for concurrency safety.
    Returns the list of reserved rows (may be < quantity if stock ran out).
    """
    rows = list(
        (
            await db.scalars(
                select(Inventory)
                .where(
                    Inventory.product_id == product_id,
                    Inventory.status == "available",
                )
                .order_by(Inventory.id)
                .with_for_update(skip_locked=True)
                .limit(quantity)
            )
        ).all()
    )
    for row in rows:
        row.status = "delivered"
        row.order_id = order_id
    return rows


async def reveal(row: Inventory) -> dict:
    """Decrypt and return the credential payload for one inventory row."""
    return decrypt(row.encrypted_payload)


async def mark_refunded(db: AsyncSession, order_id: int) -> int:
    """Mark all inventory items tied to an order as refunded. Returns count."""
    rows = list(
        (
            await db.scalars(
                select(Inventory).where(Inventory.order_id == order_id)
            )
        ).all()
    )
    for row in rows:
        row.status = "refunded"
        row.order_id = None
    return len(rows)
