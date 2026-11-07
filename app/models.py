"""
Database models for the SMTP marketplace bot.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, BigInteger, Boolean, DateTime,
    ForeignKey, Text, Index
)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    # "smtp" | "ses"
    category: Mapped[str] = mapped_column(String(20))
    region: Mapped[str] = mapped_column(String(80), default="RANDOM")
    limit_label: Mapped[str] = mapped_column(String(100), default="UNKNOWN")
    price_usd_cents: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Maximum units a single buyer can purchase at once (0 = unlimited)
    max_per_order: Mapped[int] = mapped_column(Integer, default=0)
    emoji: Mapped[str] = mapped_column(String(10), default="📦")

    # Backorder / pre-order support
    allow_backorder: Mapped[bool] = mapped_column(Boolean, default=True)
    backorder_eta: Mapped[str] = mapped_column(String(100), default="24-48 hours")


# ---------------------------------------------------------------------------
# Inventory  (one row = one credential set)
# ---------------------------------------------------------------------------

class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        Index("ix_inv_product_status", "product_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    # AES-128-CBC Fernet-encrypted JSON payload
    encrypted_payload: Mapped[str] = mapped_column(Text)
    # "available" | "reserved" | "delivered" | "refunded"
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    amount_usd_cents: Mapped[int] = mapped_column(Integer)
    pay_currency: Mapped[str] = mapped_column(String(20))
    payment_id: Mapped[str | None] = mapped_column(
        String(120), unique=True, nullable=True
    )
    pay_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pay_amount: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pay_amount_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # payment_status: "waiting" | "claimed" | "confirmed" | "rejected" | "refunded"
    payment_status: Mapped[str] = mapped_column(String(30), default="waiting")
    # fulfillment_status: "pending" | "delivered" | "backordered" | "out_of_stock" | "refunded"
    fulfillment_status: Mapped[str] = mapped_column(String(30), default="pending")
    is_backorder: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ---------------------------------------------------------------------------
# Genuine Customer Reviews (Verified Buyers Only)
# ---------------------------------------------------------------------------

class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    # 1 to 5 stars
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )


# ---------------------------------------------------------------------------
# Payment events
# ---------------------------------------------------------------------------

class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(220), unique=True)
    payment_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_spent_cents: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc
    )
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
