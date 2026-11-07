"""
Telegram bot handlers — User Storefront & Admin Control Panel.

Features:
  - Transparent Product Catalog with Backorder/Pre-Order Support
  - Verified Customer Review System (Only confirmed buyers can leave reviews)
  - Full Interactive Visual Admin Control Panel (/admin):
      • Visual Product Creation Wizard (1-Tap Presets or Custom)
      • Visual Product Editor (Adjust Price by +$10/-$50 buttons, Toggle Visibility, Set ETA)
      • Visual Account Stock Adder (Just paste accounts in chat)
      • Visual Product Removal (Delete individual products or Wipe All Products with confirmation)
      • 1-Click Order Confirmation & Delivery
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select, desc

from .config import get_settings
from .db import SessionLocal
from .models import Inventory, Order, Product, User, Review
from .payments import PAYMENT_METHODS
from .store import (
    add_stock,
    add_stock_bulk,
    count_stock,
    mark_refunded,
    reserve,
    reveal,
)

cfg = get_settings()
bot = Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# FSM States
# ════════════════════════════════════════════════════════════════════════════

class ReviewForm(StatesGroup):
    waiting_for_comment = State()


class AdminWizard(StatesGroup):
    waiting_title = State()
    waiting_price = State()
    waiting_stock = State()


class AdminEdit(StatesGroup):
    waiting_price_input = State()
    waiting_stock_accounts = State()


# ════════════════════════════════════════════════════════════════════════════
# Helpers & Presets
# ════════════════════════════════════════════════════════════════════════════

def is_admin(uid: int) -> bool:
    return uid in cfg.admins


def fmt_usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def back_btn(dest: str = "home") -> InlineKeyboardButton:
    return InlineKeyboardButton(text="◀️ Back", callback_data=dest)


def make_slug(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")
    return s[:40] or "item"


PRODUCT_PRESETS = {
    "smtp_rnd": {
        "title": "AWS SES SMTP RANDOM (unknown limit)",
        "category": "smtp",
        "emoji": "📧",
        "price_cents": 45000,
        "region": "RANDOM",
        "limit_label": "Unknown (50K–1M+)",
        "description": "High delivery performance SMTP account with unknown/random limit.",
    },
    "smtp_100k": {
        "title": "SMTP 100K High Volume Server",
        "category": "smtp",
        "emoji": "📧",
        "price_cents": 60000,
        "region": "GLOBAL",
        "limit_label": "100,000 / day",
        "description": "Dedicated SMTP server with verified DNS, DKIM, and SPF.",
    },
    "ses_1m_ca": {
        "title": "AWS SES 1M - CA-CENTRAL-1 - CLI",
        "category": "ses",
        "emoji": "☁️",
        "price_cents": 150000,
        "region": "CA-CENTRAL-1",
        "limit_label": "1,000,000",
        "description": "AWS SES with 1M daily sending limit and CLI access.",
    },
    "ses_1m_eu": {
        "title": "AWS SES 1M - EU-NORTH-1 - CLI",
        "category": "ses",
        "emoji": "☁️",
        "price_cents": 150000,
        "region": "EU-NORTH-1",
        "limit_label": "1,000,000",
        "description": "AWS SES with 1M daily sending limit and CLI access.",
    },
    "ses_1m_ap": {
        "title": "AWS SES 1M - AP-NORTHEAST - CLI",
        "category": "ses",
        "emoji": "☁️",
        "price_cents": 120000,
        "region": "AP-NORTHEAST",
        "limit_label": "1,000,000",
        "description": "AWS SES with 1M daily sending limit and CLI access.",
    },
    "ses_50k_eu": {
        "title": "AWS SES 50K - EU-WEST-2 - CLI",
        "category": "ses",
        "emoji": "☁️",
        "price_cents": 45000,
        "region": "EU-WEST-2",
        "limit_label": "50,000",
        "description": "AWS SES 50K limit with CLI access.",
    },
    "ses_50k_ap": {
        "title": "AWS SES 50K - AP-SOUTH-1 - CLI",
        "category": "ses",
        "emoji": "☁️",
        "price_cents": 40000,
        "region": "AP-SOUTH-1",
        "limit_label": "50,000",
        "description": "AWS SES 50K limit with CLI access.",
    },
}


async def upsert_user(
    uid: int,
    username: str | None,
    first_name: str | None,
    referred_by: int | None = None,
) -> None:
    async with SessionLocal() as db:
        u = await db.get(User, uid)
        if u is None:
            db.add(
                User(
                    telegram_id=uid,
                    username=username,
                    first_name=first_name,
                    referred_by=referred_by,
                )
            )
        else:
            u.username = username
            u.first_name = first_name
        await db.commit()


async def is_banned(uid: int) -> bool:
    async with SessionLocal() as db:
        u = await db.get(User, uid)
        return bool(u and u.banned)


def format_credential_display(creds: dict | str) -> str:
    if isinstance(creds, str):
        return f"<code>{creds}</code>"
    if isinstance(creds, dict):
        if "raw" in creds and len(creds) == 1:
            return f"<code>{creds['raw']}</code>"
        lines = []
        for k, v in creds.items():
            label = k.replace("_", " ").title()
            lines.append(f"<b>{label}:</b> <code>{v}</code>")
        return "\n".join(lines)
    return f"<code>{creds}</code>"


# ════════════════════════════════════════════════════════════════════════════
# Main Menu
# ════════════════════════════════════════════════════════════════════════════

def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🛍️  Browse Catalog", callback_data="catalog:0")],
        [InlineKeyboardButton(text="⭐  Customer Reviews", callback_data="reviews:0")],
        [InlineKeyboardButton(text="📦  My Orders", callback_data="orders:0")],
    ]
    if cfg.shop_support_username:
        rows.append([
            InlineKeyboardButton(
                text="🆘  Customer Support",
                url=f"https://t.me/{cfg.shop_support_username}",
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="🆘  Customer Support", callback_data="support")
        ])
    rows.append([
        InlineKeyboardButton(text="🔗  Refer a Friend", callback_data="referral")
    ])
    if is_admin(uid):
        rows.append([
            InlineKeyboardButton(text="👑  Admin Control Panel", callback_data="ap:main")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


WELCOME_TEXT = (
    "{banner} <b>{shop}</b>\n\n"
    "Welcome! Browse our inventory of verified SMTP accounts.\n\n"
    "• <b>BTC</b> & <b>USDT TRC-20</b> accepted\n"
    "• Transparent availability & automated delivery\n"
    "• High reputation & high delivery performance"
)


@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    args = (msg.text or "").split(maxsplit=1)
    referred_by: int | None = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referred_by = int(args[1][4:])
            if referred_by == msg.from_user.id:
                referred_by = None
        except ValueError:
            pass

    await upsert_user(
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
        referred_by,
    )

    if await is_banned(msg.from_user.id):
        await msg.answer("❌ Your account is disabled.")
        return

    text = WELCOME_TEXT.format(banner=cfg.shop_banner, shop=cfg.shop_name)
    await msg.answer(text, reply_markup=main_menu_kb(msg.from_user.id))


@dp.callback_query(F.data == "home")
async def cb_home(c: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await is_banned(c.from_user.id):
        await c.answer("Account disabled", show_alert=True)
        return
    text = WELCOME_TEXT.format(banner=cfg.shop_banner, shop=cfg.shop_name)
    await c.message.edit_text(text, reply_markup=main_menu_kb(c.from_user.id))
    await c.answer()


# ════════════════════════════════════════════════════════════════════════════
# Customer Catalogue
# ════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("catalog:"))
async def cb_catalog(c: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if await is_banned(c.from_user.id):
        await c.answer("Account disabled", show_alert=True)
        return

    page = int(c.data.split(":")[1])
    page_size = cfg.catalog_page_size

    async with SessionLocal() as db:
        all_products = list(
            (
                await db.scalars(
                    select(Product)
                    .where(Product.active.is_(True), Product.is_archived.is_(False))
                    .order_by(Product.price_usd_cents)
                )
            ).all()
        )
        displayable: list[tuple[Product, int]] = []
        for p in all_products:
            n = await count_stock(db, p.id)
            if n > 0 or p.allow_backorder:
                displayable.append((p, n))

    total = len(displayable)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(0, min(page, total_pages - 1))
    slice_ = displayable[page * page_size: page * page_size + page_size]

    rows: list[list[InlineKeyboardButton]] = []
    for p, n in slice_:
        if n > 0:
            badge = f"[{n} in stock]"
            emoji_tag = p.emoji
        else:
            badge = "[Pre-Order]"
            emoji_tag = "🟡"
        label = f"{emoji_tag} {p.title} · {fmt_usd(p.price_usd_cents)} {badge}"[:64]
        rows.append([InlineKeyboardButton(text=label, callback_data=f"p:{p.id}")])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"catalog:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"catalog:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([back_btn("home")])

    header = (
        f"<b>📋 Product Catalog</b> ({total} items)\n\n"
        "Select an item below for details:"
    )
    if total == 0:
        header = "<b>📋 Product Catalog</b>\n\n<i>No items currently listed. Please check back soon!</i>"

    await c.message.edit_text(header, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data == "noop")
async def cb_noop(c: CallbackQuery) -> None:
    await c.answer()


# ════════════════════════════════════════════════════════════════════════════
# Product Details with Transparent Backorder ETA
# ════════════════════════════════════════════════════════════════════════════

def _category_badge(cat: str) -> str:
    return "📧 SMTP Server" if cat == "smtp" else "☁️ SES Access"


async def _product_detail_text(p: Product, stock: int) -> str:
    region_line = "" if p.region in ("RANDOM", "") else f"\n🌍 <b>Region:</b> <code>{p.region}</code>"
    limit_line = (
        f"\n📊 <b>Sending Limit:</b> {p.limit_label}"
        if p.limit_label not in ("UNKNOWN", "")
        else "\n📊 <b>Sending Limit:</b> Standard / Multi-Limit"
    )

    if stock > 0:
        avail_line = f"\n📦 <b>Availability:</b> <b>In Stock ({stock} units)</b> — Instant Automated Delivery"
    else:
        avail_line = (
            f"\n🟡 <b>Availability:</b> <b>Pre-Order / Backorder</b>\n"
            f"⏱️ <b>Estimated Delivery Window:</b> <b>{p.backorder_eta}</b>\n"
            f"ℹ️ <i>Notice: Immediate stock is 0. Placing an order reserves your units in the current provisioning batch.</i>"
        )

    return (
        f"{p.emoji} <b>{p.title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>Type:</b> {_category_badge(p.category)}"
        f"{region_line}"
        f"{limit_line}\n"
        f"💰 <b>Price:</b> <b>{fmt_usd(p.price_usd_cents)}</b>"
        f"{avail_line}"
        + (f"\n\n📝 {p.description}" if p.description else "")
    )


def _qty_kb(pid: int, stock: int, max_per_order: int, is_backorder: bool) -> InlineKeyboardMarkup:
    cap = 5 if is_backorder else min(stock, max_per_order if max_per_order > 0 else stock, 10)
    qty_buttons = [
        InlineKeyboardButton(text=f"Buy {q}", callback_data=f"qty:{pid}:{q}")
        for q in range(1, cap + 1)
    ]
    rows: list[list[InlineKeyboardButton]] = [
        qty_buttons[i: i + 5] for i in range(0, len(qty_buttons), 5)
    ]
    rows.append([back_btn("catalog:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data.startswith("p:"))
async def cb_product_detail(c: CallbackQuery) -> None:
    if await is_banned(c.from_user.id):
        await c.answer("Account disabled", show_alert=True)
        return

    pid = int(c.data.split(":")[1])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        stock = await count_stock(db, pid) if p else 0

    if not p or not p.active or p.is_archived:
        await c.answer("❌ This product is currently unavailable.", show_alert=True)
        return
    if stock == 0 and not p.allow_backorder:
        await c.answer("❌ Out of stock.", show_alert=True)
        return

    is_backorder = (stock == 0 and p.allow_backorder)
    text = await _product_detail_text(p, stock)
    text += "\n\n<i>Choose quantity to purchase:</i>"
    await c.message.edit_text(text, reply_markup=_qty_kb(pid, stock, p.max_per_order, is_backorder))
    await c.answer()


@dp.callback_query(F.data.startswith("qty:"))
async def cb_qty(c: CallbackQuery) -> None:
    if await is_banned(c.from_user.id):
        await c.answer("Account disabled", show_alert=True)
        return

    _, pid_s, qty_s = c.data.split(":")
    pid, qty = int(pid_s), int(qty_s)

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        stock = await count_stock(db, pid) if p else 0

    if not p or not p.active or p.is_archived:
        await c.answer("Product unavailable.", show_alert=True)
        return
    if stock > 0 and qty > stock:
        await c.answer(f"Only {stock} items available in stock.", show_alert=True)
        return

    is_backorder = (stock == 0 and p.allow_backorder)
    total_cents = p.price_usd_cents * qty
    backorder_notice = (
        f"\n\n🟡 <b>Order Type: Pre-Order / Backorder</b>\n"
        f"Delivery window: <b>{p.backorder_eta}</b> after payment verification."
        if is_backorder
        else "\n\n⚡ <b>Order Type: Instant Stock Delivery</b>"
    )

    text = (
        f"{p.emoji} <b>Checkout Summary</b>\n\n"
        f"Product: <b>{p.title}</b>\n"
        f"Quantity: <b>{qty} units</b>\n"
        f"Total Price: <b>{fmt_usd(total_cents)}</b>"
        f"{backorder_notice}\n\n"
        f"Select your cryptocurrency payment method:"
    )
    rows = [
        [
            InlineKeyboardButton(
                text=info["label"],
                callback_data=f"buy:{pid}:{qty}:{method}",
            )
        ]
        for method, info in PAYMENT_METHODS.items()
    ]
    rows.append([back_btn(f"p:{pid}")])
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(c: CallbackQuery) -> None:
    if await is_banned(c.from_user.id):
        await c.answer("Account disabled", show_alert=True)
        return

    parts = c.data.split(":")
    pid, qty, method = int(parts[1]), int(parts[2]), parts[3]

    if method not in PAYMENT_METHODS:
        await c.answer("Unsupported payment method.", show_alert=True)
        return

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        stock = await count_stock(db, pid) if p else 0

        if not p or not p.active or p.is_archived:
            await c.answer("Product is currently unavailable.", show_alert=True)
            return
        if stock == 0 and not p.allow_backorder:
            await c.answer("Out of stock.", show_alert=True)
            return

        is_backorder = (stock < qty and p.allow_backorder)
        user = await db.get(User, c.from_user.id)
        referred_by = user.referred_by if user else None

        total_cents = p.price_usd_cents * qty
        order = Order(
            telegram_user_id=c.from_user.id,
            product_id=pid,
            quantity=qty,
            amount_usd_cents=total_cents,
            pay_currency=method,
            payment_status="waiting",
            is_backorder=is_backorder,
            referred_by=referred_by,
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)

    info = PAYMENT_METHODS[method]
    address = info["address"]
    amount_usd = fmt_usd(total_cents)

    delivery_schedule = (
        f"🟡 <b>Pre-Order Window:</b> {p.backorder_eta}\n"
        if is_backorder
        else "⚡ <b>Fulfillment:</b> Instant on network confirmation\n"
    )

    await c.message.answer(
        f"🧾 <b>Order #{order.id} Created</b>\n\n"
        f"Product: <b>{p.title}</b> × {qty}\n"
        f"Total Amount: <b>{amount_usd}</b>\n"
        f"{delivery_schedule}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Method:</b> {info['label']}\n"
        f"🌐 <b>Network:</b> <code>{info['network']}</code>\n\n"
        f"<b>Send exactly:</b>\n"
        f"<code>{amount_usd}</code> worth of {method.split('-')[0]}\n\n"
        f"<b>Deposit Address:</b> <i>(tap to copy)</i>\n"
        f"<code>{address}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"After sending, tap <b>'✅ I've Sent the Payment'</b> below.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ I've Sent the Payment",
                        callback_data=f"paid:{order.id}",
                    )
                ],
                [back_btn("home")],
            ]
        ),
    )
    await c.answer("Order initiated ✅")


@dp.callback_query(F.data.startswith("paid:"))
async def cb_paid(c: CallbackQuery) -> None:
    order_id = int(c.data.split(":")[1])

    async with SessionLocal() as db:
        order = await db.get(Order, order_id)
        if not order:
            await c.answer("Order not found.", show_alert=True)
            return
        if order.telegram_user_id != c.from_user.id:
            await c.answer("Unauthorized", show_alert=True)
            return
        if order.fulfillment_status == "delivered":
            await c.answer("Already delivered!", show_alert=True)
            return
        if order.payment_status == "confirmed":
            await c.answer("Payment already verified.", show_alert=True)
            return

        order.payment_status = "claimed"
        await db.commit()
        p = await db.get(Product, order.product_id)

    info = PAYMENT_METHODS.get(order.pay_currency, {})
    order_type_badge = "🟡 BACKORDER" if order.is_backorder else "⚡ INSTANT STOCK"
    user_handle = f"@{c.from_user.username}" if c.from_user.username else f"ID: {c.from_user.id}"

    admin_alert = (
        f"🔔 <b>New Payment Received — Order #{order.id} [{order_type_badge}]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Customer: <a href='tg://user?id={c.from_user.id}'>{c.from_user.full_name}</a> ({user_handle})\n"
        f"📦 Item: <b>{p.title if p else '?'}</b> × {order.quantity}\n"
        f"💰 Amount: <b>{fmt_usd(order.amount_usd_cents)}</b>\n"
        f"💳 Method: <b>{info.get('label', order.pay_currency)}</b>\n"
        f"📥 Deposit Wallet: <code>{info.get('address', '?')}</code>\n"
        + (f"⏱️ Backorder ETA: <b>{p.backorder_eta}</b>\n" if order.is_backorder and p else "")
        + "━━━━━━━━━━━━━━━━━━━━"
    )
    admin_buttons = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm & Deliver",
                    callback_data=f"adm_confirm:{order.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"adm_reject:{order.id}",
                ),
            ]
        ]
    )

    for admin_id in cfg.admins:
        try:
            await bot.send_message(admin_id, admin_alert, reply_markup=admin_buttons)
        except Exception:
            pass

    await c.message.edit_reply_markup(reply_markup=None)
    eta_line = f"\nEstimated fulfillment window: <b>{p.backorder_eta}</b>." if order.is_backorder and p else ""
    await c.message.answer(
        f"✅ <b>Payment Submitted — Order #{order.id}</b>\n\n"
        f"Status: <b>Verifying network confirmations…</b> 🔄\n"
        f"{eta_line}\n"
        f"Once the deposit confirmation completes, your order will be fulfilled automatically."
    )
    await c.answer("Payment submitted for verification ✅")


# ════════════════════════════════════════════════════════════════════════════
# 1-Click Admin Order Actions
# ════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("adm_confirm:"))
async def cb_admin_confirm_action(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        await c.answer("Admin only.", show_alert=True)
        return

    order_id = int(c.data.split(":")[1])
    async with SessionLocal() as db:
        order = await db.get(Order, order_id)
        if not order:
            await c.answer("Order not found.", show_alert=True)
            return
        if order.fulfillment_status == "delivered":
            await c.answer("Already delivered.", show_alert=True)
            return

        p = await db.get(Product, order.product_id)
        items = await reserve(db, order.product_id, order.id, order.quantity)

        if len(items) == order.quantity:
            order.payment_status = "confirmed"
            order.fulfillment_status = "delivered"

            cred_blocks: list[str] = []
            for idx, item in enumerate(items, 1):
                creds = await reveal(item)
                header = f"<b>── Account {idx}/{len(items)} ──</b>\n" if len(items) > 1 else ""
                cred_blocks.append(header + format_credential_display(creds))

            await db.commit()

            delivery_msg = (
                f"🎉 <b>Payment Confirmed — Order #{order_id}</b>\n\n"
                f"Thank you for your purchase! Here are your credentials:\n\n"
                + "\n\n".join(cred_blocks)
                + "\n\n<i>Keep these credentials secure. Please leave a review below!</i>"
            )
            review_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⭐ Leave a Review",
                            callback_data=f"rev_start:{order.id}",
                        )
                    ]
                ]
            )
            try:
                await bot.send_message(order.telegram_user_id, delivery_msg, reply_markup=review_kb)
            except Exception as e:
                log.error("Failed to send delivery to user %s: %s", order.telegram_user_id, e)

            await c.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="✅ Confirmed & Delivered", callback_data="noop")]]
                )
            )
            await c.answer(f"✅ Order #{order_id} fulfilled and delivered!", show_alert=True)
        else:
            for it in items:
                it.status = "available"
                it.order_id = None
            order.payment_status = "confirmed"
            order.fulfillment_status = "backordered"
            order.is_backorder = True
            await db.commit()

            eta = p.backorder_eta if p else "24-48 hours"
            backorder_msg = (
                f"✅ <b>Payment Verified — Pre-Order Confirmed (Order #{order_id})</b>\n\n"
                f"Thank you! Your payment is confirmed and your order is queued.\n"
                f"⏱️ Estimated fulfillment window: <b>{eta}</b>.\n\n"
                f"Your credentials will be delivered right here as soon as provisioned."
            )
            try:
                await bot.send_message(order.telegram_user_id, backorder_msg)
            except Exception:
                pass

            await c.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🟡 Queued in Backorders", callback_data="noop")],
                        [InlineKeyboardButton(text="📥 Deliver When Restocked", callback_data=f"adm_confirm:{order_id}")],
                    ]
                )
            )
            await c.answer(f"Order #{order_id} queued in backorders (ETA: {eta}).", show_alert=True)


@dp.callback_query(F.data.startswith("adm_reject:"))
async def cb_admin_reject_action(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        await c.answer("Admin only.", show_alert=True)
        return

    order_id = int(c.data.split(":")[1])
    async with SessionLocal() as db:
        order = await db.get(Order, order_id)
        if not order:
            await c.answer("Order not found.", show_alert=True)
            return
        if order.fulfillment_status == "delivered":
            await c.answer("Already delivered.", show_alert=True)
            return
        order.payment_status = "rejected"
        await db.commit()

    try:
        await bot.send_message(
            order.telegram_user_id,
            f"❌ <b>Order #{order_id} Notice</b>\n\n"
            f"We could not verify the deposit for this order.\n"
            f"If you completed the transfer, please contact Support with your transaction hash.",
        )
    except Exception:
        pass

    await c.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Payment Rejected", callback_data="noop")]]
        )
    )
    await c.answer(f"Order #{order_id} rejected.", show_alert=True)


# ════════════════════════════════════════════════════════════════════════════
# ⭐ Genuine Customer Reviews (Verified Buyers Only)
# ════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("reviews:"))
async def cb_reviews_list(c: CallbackQuery) -> None:
    page = int(c.data.split(":")[1])
    per_page = 4

    async with SessionLocal() as db:
        total = (await db.scalar(select(func.count(Review.id)))) or 0
        avg_rating = (await db.scalar(select(func.avg(Review.rating)))) or 0.0
        reviews = list(
            (
                await db.scalars(
                    select(Review).order_by(desc(Review.id)).offset(page * per_page).limit(per_page)
                )
            ).all()
        )
        products_map = {}
        users_map = {}
        for r in reviews:
            products_map[r.product_id] = await db.get(Product, r.product_id)
            users_map[r.telegram_user_id] = await db.get(User, r.telegram_user_id)

    total_pages = max(1, math.ceil(total / per_page))

    if total == 0:
        await c.message.edit_text(
            "⭐ <b>Verified Customer Reviews</b>\n\n"
            "<i>No reviews have been submitted yet.</i>\n\n"
            "🛡️ <b>Verification Policy:</b> Reviews can only be submitted by verified customers who have completed an order.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("home")]]),
        )
        await c.answer()
        return

    stars_bar = "⭐" * int(round(avg_rating))
    header = (
        f"⭐ <b>Customer Reviews</b>\n"
        f"Average Rating: <b>{avg_rating:.1f}/5.0</b> {stars_bar} ({total} verified reviews)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    blocks = []
    for r in reviews:
        p = products_map.get(r.product_id)
        u = users_map.get(r.telegram_user_id)
        p_name = p.title if p else "Verified Product"
        reviewer_name = f"@{u.username}" if (u and u.username) else f"Buyer #{str(r.telegram_user_id)[-4:]}"
        date_str = r.created_at.strftime("%Y-%m-%d")
        stars = "⭐" * r.rating
        comment_str = f"\n💬 <i>\"{r.comment}\"</i>" if r.comment else ""
        blocks.append(
            f"<b>{stars} ({r.rating}/5)</b> by <b>{reviewer_name}</b>\n"
            f"Item: {p_name} · <i>{date_str}</i>"
            f"{comment_str}"
        )

    text = header + "\n\n──────────\n\n".join(blocks)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"reviews:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"reviews:{page+1}"))

    rows: list[list[InlineKeyboardButton]] = []
    if nav:
        rows.append(nav)
    rows.append([back_btn("home")])

    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data.startswith("rev_start:"))
async def cb_review_start(c: CallbackQuery) -> None:
    order_id = int(c.data.split(":")[1])

    async with SessionLocal() as db:
        order = await db.get(Order, order_id)
        if not order:
            await c.answer("Order not found.", show_alert=True)
            return
        if order.telegram_user_id != c.from_user.id:
            await c.answer("You can only review your own orders.", show_alert=True)
            return
        if order.fulfillment_status != "delivered":
            await c.answer("You can only review delivered orders.", show_alert=True)
            return

        existing = await db.scalar(select(Review).where(Review.order_id == order_id))
        if existing:
            await c.answer("You have already reviewed this order.", show_alert=True)
            return

        p = await db.get(Product, order.product_id)

    title = p.title if p else "your order"
    text = (
        f"⭐ <b>Review for Order #{order_id}</b>\n"
        f"Product: <b>{title}</b>\n\n"
        "How would you rate your experience?"
    )
    star_buttons = [
        [
            InlineKeyboardButton(text="⭐⭐⭐⭐⭐ (5)", callback_data=f"rev_rate:{order_id}:5"),
            InlineKeyboardButton(text="⭐⭐⭐⭐ (4)", callback_data=f"rev_rate:{order_id}:4"),
        ],
        [
            InlineKeyboardButton(text="⭐⭐⭐ (3)", callback_data=f"rev_rate:{order_id}:3"),
            InlineKeyboardButton(text="⭐⭐ (2)", callback_data=f"rev_rate:{order_id}:2"),
            InlineKeyboardButton(text="⭐ (1)", callback_data=f"rev_rate:{order_id}:1"),
        ],
        [back_btn("orders:0")],
    ]
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=star_buttons))
    await c.answer()


@dp.callback_query(F.data.startswith("rev_rate:"))
async def cb_review_rate(c: CallbackQuery, state: FSMContext) -> None:
    parts = c.data.split(":")
    order_id = int(parts[1])
    rating = int(parts[2])

    await state.set_state(ReviewForm.waiting_for_comment)
    await state.update_data(order_id=order_id, rating=rating)

    text = (
        f"⭐ Rating selected: <b>{rating} / 5 stars</b>\n\n"
        "Please reply with a short comment about the product or delivery speed (or tap <b>Skip Comment</b> below):"
    )
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏩ Skip Comment & Post", callback_data="rev_skip")],
            [back_btn("orders:0")],
        ]
    )
    await c.message.edit_text(text, reply_markup=skip_kb)
    await c.answer()


@dp.callback_query(F.data == "rev_skip")
async def cb_review_skip(c: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    order_id = data.get("order_id")
    rating = data.get("rating", 5)

    if not order_id:
        await c.answer("Session expired.", show_alert=True)
        return

    await _save_review(c.from_user.id, order_id, rating, "")
    await c.message.edit_text(
        "✅ <b>Thank you!</b> Your verified review has been posted successfully.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("home")]]),
    )
    await c.answer("Review saved ✅")


@dp.message(ReviewForm.waiting_for_comment)
async def msg_review_comment(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    order_id = data.get("order_id")
    rating = data.get("rating", 5)

    if not order_id:
        await m.answer("Review session expired.")
        return

    comment = m.text.strip()[:400]
    await _save_review(m.from_user.id, order_id, rating, comment)
    await m.answer(
        "✅ <b>Thank you!</b> Your verified review and comment have been posted.",
        reply_markup=main_menu_kb(m.from_user.id),
    )


async def _save_review(uid: int, order_id: int, rating: int, comment: str) -> None:
    async with SessionLocal() as db:
        order = await db.get(Order, order_id)
        if not order:
            return
        existing = await db.scalar(select(Review).where(Review.order_id == order_id))
        if existing:
            return
        rev = Review(
            order_id=order_id,
            telegram_user_id=uid,
            product_id=order.product_id,
            rating=rating,
            comment=comment,
        )
        db.add(rev)
        await db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Customer Orders
# ════════════════════════════════════════════════════════════════════════════

STATUS_USER_DISPLAY = {
    "waiting": "⏳ Awaiting Payment",
    "claimed": "🔄 Verifying Network",
    "confirmed": "✅ Verified",
    "rejected": "❌ Unverified",
    "refunded": "↩️ Refunded",
}


@dp.callback_query(F.data.startswith("orders:"))
async def cb_my_orders(c: CallbackQuery) -> None:
    if await is_banned(c.from_user.id):
        await c.answer("Account disabled", show_alert=True)
        return

    page = int(c.data.split(":")[1])
    per_page = 5
    uid = c.from_user.id

    async with SessionLocal() as db:
        total = (
            await db.scalar(select(func.count(Order.id)).where(Order.telegram_user_id == uid))
        ) or 0
        orders = list(
            (
                await db.scalars(
                    select(Order)
                    .where(Order.telegram_user_id == uid)
                    .order_by(desc(Order.id))
                    .offset(page * per_page)
                    .limit(per_page)
                )
            ).all()
        )
        reviewed_ids = {
            r.order_id
            for r in (
                await db.scalars(
                    select(Review).where(Review.telegram_user_id == uid)
                )
            ).all()
        }

    if not orders:
        await c.message.edit_text(
            "📦 <b>My Orders</b>\n\nYou have no past orders yet.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("home")]]),
        )
        await c.answer()
        return

    lines = []
    review_buttons: list[list[InlineKeyboardButton]] = []
    for o in orders:
        if o.fulfillment_status == "delivered":
            status_text = "📬 Delivered"
        elif o.fulfillment_status == "backordered":
            status_text = "🟡 Queued in Backorder"
        else:
            status_text = STATUS_USER_DISPLAY.get(o.payment_status, o.payment_status)

        lines.append(
            f"<b>Order #{o.id}</b> · {fmt_usd(o.amount_usd_cents)} ({o.pay_currency})\n"
            f"Status: <b>{status_text}</b>\n"
            f"<i>{o.created_at.strftime('%Y-%m-%d %H:%M')} UTC</i>"
        )
        if o.fulfillment_status == "delivered" and o.id not in reviewed_ids:
            review_buttons.append([
                InlineKeyboardButton(text=f"⭐ Review Order #{o.id}", callback_data=f"rev_start:{o.id}")
            ])

    total_pages = max(1, math.ceil(total / per_page))
    text = f"<b>📦 Order History</b> (Page {page+1}/{total_pages})\n\n" + "\n\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"orders:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"orders:{page+1}"))

    rows: list[list[InlineKeyboardButton]] = []
    rows.extend(review_buttons)
    if nav:
        rows.append(nav)
    rows.append([back_btn("home")])
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data == "support")
async def cb_support(c: CallbackQuery) -> None:
    await c.message.answer(
        "🆘 <b>Customer Support</b>\n\n"
        "For assistance or custom inquiries, please reach out with your <b>Order ID</b>."
    )
    await c.answer()


@dp.callback_query(F.data == "referral")
async def cb_referral(c: CallbackQuery) -> None:
    uid = c.from_user.id
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{uid}"

    async with SessionLocal() as db:
        count = (
            await db.scalar(select(func.count(User.telegram_id)).where(User.referred_by == uid))
        ) or 0

    await c.message.answer(
        f"🔗 <b>Referral Program</b>\n\n"
        f"Invite friends and partners with your unique link:\n"
        f"<code>{link}</code>\n\n"
        f"Total users referred: <b>{count}</b>"
    )
    await c.answer()


# ════════════════════════════════════════════════════════════════════════════
# 👑 INTERACTIVE ADMIN CONTROL PANEL (Visual & Touch-Driven)
# ════════════════════════════════════════════════════════════════════════════

async def get_admin_dashboard_data() -> dict:
    async with SessionLocal() as db:
        active_products = (
            await db.scalar(
                select(func.count(Product.id)).where(Product.active.is_(True), Product.is_archived.is_(False))
            )
        ) or 0
        total_products = (await db.scalar(select(func.count(Product.id)))) or 0
        total_stock = (await db.scalar(select(func.count(Inventory.id)).where(Inventory.status == "available"))) or 0
        pending_orders = (
            await db.scalar(
                select(func.count(Order.id)).where(
                    Order.payment_status.in_(["waiting", "claimed"]),
                    Order.fulfillment_status == "pending",
                )
            )
        ) or 0
        backordered_orders = (
            await db.scalar(
                select(func.count(Order.id)).where(Order.fulfillment_status == "backordered")
            )
        ) or 0
        delivered_orders = (
            await db.scalar(select(func.count(Order.id)).where(Order.fulfillment_status == "delivered"))
        ) or 0
        total_revenue = (
            await db.scalar(
                select(func.sum(Order.amount_usd_cents)).where(
                    Order.fulfillment_status.in_(["delivered", "backordered"])
                )
            )
        ) or 0
        total_reviews = (await db.scalar(select(func.count(Review.id)))) or 0
        total_users = (await db.scalar(select(func.count(User.telegram_id)))) or 0

    return {
        "active_products": active_products,
        "total_products": total_products,
        "total_stock": total_stock,
        "pending_orders": pending_orders,
        "backordered_orders": backordered_orders,
        "delivered_orders": delivered_orders,
        "total_revenue": total_revenue,
        "total_reviews": total_reviews,
        "total_users": total_users,
    }


def admin_panel_kb(pending_count: int, backorder_count: int) -> InlineKeyboardMarkup:
    pending_badge = f" ({pending_count} 🔴)" if pending_count > 0 else ""
    backorder_badge = f" ({backorder_count} 🟡)" if backorder_count > 0 else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Products & Pricing", callback_data="ap:products:0"),
                InlineKeyboardButton(text="➕ Add Product (Wizard)", callback_data="ap:wizard:start"),
            ],
            [
                InlineKeyboardButton(text=f"⏳ Pending Orders{pending_badge}", callback_data="ap:pending"),
                InlineKeyboardButton(text=f"🟡 Backorders{backorder_badge}", callback_data="ap:backorders"),
            ],
            [
                InlineKeyboardButton(text="📊 Revenue & Stats", callback_data="ap:stats"),
                InlineKeyboardButton(text="💳 Payment Wallets", callback_data="ap:wallets"),
            ],
            [
                InlineKeyboardButton(text="📢 Broadcast Message", callback_data="ap:broadcasthelp"),
                InlineKeyboardButton(text="🔄 Refresh", callback_data="ap:main"),
            ],
            [
                InlineKeyboardButton(text="◀️ Return to Store Menu", callback_data="home"),
            ],
        ]
    )


@dp.message(Command("admin"))
async def cmd_admin_panel(m: Message, state: FSMContext) -> None:
    await state.clear()
    if not is_admin(m.from_user.id):
        return
    data = await get_admin_dashboard_data()
    text = (
        "👑 <b>Admin Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Active Products:</b> {data['active_products']} (Total: {data['total_products']})\n"
        f"📥 <b>Available Stock:</b> <b>{data['total_stock']} units</b>\n"
        f"⏳ <b>Pending Verification:</b> <b>{data['pending_orders']}</b>\n"
        f"🟡 <b>Queued Backorders:</b> <b>{data['backordered_orders']}</b>\n"
        f"✅ <b>Fulfilled Orders:</b> {data['delivered_orders']}\n"
        f"💰 <b>Total Revenue:</b> <b>{fmt_usd(data['total_revenue'])}</b>\n"
        f"⭐ <b>Verified Reviews:</b> {data['total_reviews']}\n"
        f"👥 <b>Total Users:</b> {data['total_users']}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await m.answer(text, reply_markup=admin_panel_kb(data["pending_orders"], data["backordered_orders"]))


@dp.callback_query(F.data == "ap:main")
async def cb_admin_main(c: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not is_admin(c.from_user.id):
        await c.answer("Admin only.", show_alert=True)
        return
    data = await get_admin_dashboard_data()
    text = (
        "👑 <b>Admin Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Active Products:</b> {data['active_products']} (Total: {data['total_products']})\n"
        f"📥 <b>Available Stock:</b> <b>{data['total_stock']} units</b>\n"
        f"⏳ <b>Pending Verification:</b> <b>{data['pending_orders']}</b>\n"
        f"🟡 <b>Queued Backorders:</b> <b>{data['backordered_orders']}</b>\n"
        f"✅ <b>Fulfilled Orders:</b> {data['delivered_orders']}\n"
        f"💰 <b>Total Revenue:</b> <b>{fmt_usd(data['total_revenue'])}</b>\n"
        f"⭐ <b>Verified Reviews:</b> {data['total_reviews']}\n"
        f"👥 <b>Total Users:</b> {data['total_users']}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await c.message.edit_text(text, reply_markup=admin_panel_kb(data["pending_orders"], data["backordered_orders"]))
    await c.answer()


# ════════════════════════════════════════════════════════════════════════════
# ➕ Visual Product Creation Wizard (No Slash Commands Required!)
# ════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data == "ap:wizard:start")
async def cb_wizard_start(c: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(c.from_user.id):
        return
    await state.clear()
    text = (
        "➕ <b>Product Creation Wizard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose an option to add a product:\n\n"
        "• <b>1-Tap Instant Presets</b> (pre-filled title, region & price)\n"
        "• <b>Custom Product</b> (create any title and price visually)"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ 1-Tap Presets", callback_data="ap:wizard:presets"),
                InlineKeyboardButton(text="✏️ Custom Product", callback_data="ap:wizard:custom"),
            ],
            [
                InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main"),
            ],
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data == "ap:wizard:presets")
async def cb_wizard_presets(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    text = (
        "⚡ <b>1-Tap Product Presets</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any item to add it to your catalog immediately:"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="📧 SMTP Random Limit ($450)", callback_data="ap:add_preset:smtp_rnd"),
        ],
        [
            InlineKeyboardButton(text="📧 SMTP 100K Server ($600)", callback_data="ap:add_preset:smtp_100k"),
        ],
        [
            InlineKeyboardButton(text="☁️ SES 1M - CA-CENTRAL-1 ($1,500)", callback_data="ap:add_preset:ses_1m_ca"),
        ],
        [
            InlineKeyboardButton(text="☁️ SES 1M - EU-NORTH-1 ($1,500)", callback_data="ap:add_preset:ses_1m_eu"),
        ],
        [
            InlineKeyboardButton(text="☁️ SES 1M - AP-NORTHEAST ($1,200)", callback_data="ap:add_preset:ses_1m_ap"),
        ],
        [
            InlineKeyboardButton(text="☁️ SES 50K - EU-WEST-2 ($450)", callback_data="ap:add_preset:ses_50k_eu"),
        ],
        [
            InlineKeyboardButton(text="☁️ SES 50K - AP-SOUTH-1 ($400)", callback_data="ap:add_preset:ses_50k_ap"),
        ],
        [
            InlineKeyboardButton(text="◀️ Back to Wizard", callback_data="ap:wizard:start"),
        ],
    ]
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data.startswith("ap:add_preset:"))
async def cb_execute_add_preset(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    preset_key = c.data.split(":")[2]
    info = PRODUCT_PRESETS.get(preset_key)
    if not info:
        await c.answer("Preset not found", show_alert=True)
        return

    base_slug = make_slug(info["title"])
    sku = base_slug

    async with SessionLocal() as db:
        idx = 1
        while await db.scalar(select(Product).where(Product.sku == sku)):
            sku = f"{base_slug}-{idx}"
            idx += 1

        p = Product(
            sku=sku,
            title=info["title"],
            category=info["category"],
            region=info["region"],
            limit_label=info["limit_label"],
            price_usd_cents=info["price_cents"],
            description=info["description"],
            emoji=info["emoji"],
            active=True,
            is_archived=False,
            allow_backorder=True,
            backorder_eta="24-48 hours",
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
        pid = p.id

    await c.answer("✅ Product created from preset!", show_alert=True)
    # Redirect directly to the interactive product detail screen
    c.data = f"ap:pdetail:{pid}"
    await cb_admin_product_detail(c)


# ── Custom Product Creation (Visual Prompts) ─────────────────────────────────

@dp.callback_query(F.data == "ap:wizard:custom")
async def cb_wizard_custom(c: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(c.from_user.id):
        return
    await state.set_state(AdminWizard.waiting_title)
    text = (
        "✏️ <b>Custom Product — Step 1/2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Please send the <b>Product Title</b> in chat.\n\n"
        "<i>Example:</i> <code>Custom SMTP 50K - US East</code>"
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="ap:wizard:start")]]
    )
    await c.message.edit_text(text, reply_markup=cancel_kb)
    await c.answer()


@dp.message(AdminWizard.waiting_title)
async def msg_wizard_title(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    title = m.text.strip()[:180]
    if not title:
        await m.answer("Please send a valid title.")
        return

    await state.update_data(title=title)
    await state.set_state(AdminWizard.waiting_price)

    await m.answer(
        f"✏️ <b>Custom Product: {title} — Step 2/2</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Please send the <b>Price in USD</b> (numbers only).\n\n"
        "<i>Example:</i> <code>450</code> for $450.00",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="ap:wizard:start")]]
        ),
    )


@dp.message(AdminWizard.waiting_price)
async def msg_wizard_price(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    raw = m.text.strip().replace("$", "").replace(",", "")
    try:
        dollars = float(raw)
        price_cents = int(dollars * 100)
        if price_cents <= 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Invalid price. Please send a positive number (e.g. <code>450</code>).")
        return

    data = await state.get_data()
    await state.clear()
    title = data.get("title", "Custom Product")

    base_slug = make_slug(title)
    sku = base_slug
    cat = "ses" if "ses" in title.lower() else "smtp"
    emoji = "☁️" if cat == "ses" else "📧"

    async with SessionLocal() as db:
        idx = 1
        while await db.scalar(select(Product).where(Product.sku == sku)):
            sku = f"{base_slug}-{idx}"
            idx += 1

        p = Product(
            sku=sku,
            title=title,
            category=cat,
            region="GLOBAL",
            limit_label="Standard",
            price_usd_cents=price_cents,
            emoji=emoji,
            active=True,
            is_archived=False,
            allow_backorder=True,
            backorder_eta="24-48 hours",
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
        pid = p.id

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Add Stock Accounts Now", callback_data=f"ap:addstock:{pid}"),
            ],
            [
                InlineKeyboardButton(text="📦 View in Product Editor", callback_data=f"ap:pdetail:{pid}"),
                InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main"),
            ],
        ]
    )
    await m.answer(
        f"✅ <b>Product Created!</b>\n\n"
        f"• <b>Title:</b> {p.title}\n"
        f"• <b>Price:</b> {fmt_usd(p.price_usd_cents)}\n"
        f"• <b>Status:</b> 🟢 Active (Pre-Order enabled)\n\n"
        "Choose an option below:",
        reply_markup=kb,
    )


# ════════════════════════════════════════════════════════════════════════════
# 📦 Visual Products Manager & Wipe Everything Option
# ════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("ap:products:"))
async def cb_admin_products(c: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not is_admin(c.from_user.id):
        return

    page = int(c.data.split(":")[2])
    per_page = 6

    async with SessionLocal() as db:
        all_products = list((await db.scalars(select(Product).order_by(Product.id.desc()))).all())
        stock_map = {}
        for p in all_products:
            stock_map[p.id] = await count_stock(db, p.id)

    total = len(all_products)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(0, min(page, total_pages - 1))
    slice_ = all_products[page * per_page: page * per_page + per_page]

    rows: list[list[InlineKeyboardButton]] = []
    for p in slice_:
        if p.is_archived:
            st = "📦"
        elif p.active:
            st = "🟢"
        else:
            st = "🔴"
        n = stock_map[p.id]
        label = f"{st} {p.title} · {fmt_usd(p.price_usd_cents)} [{n} stock]"[:64]
        rows.append([InlineKeyboardButton(text=label, callback_data=f"ap:pdetail:{p.id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"ap:products:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"ap:products:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(text="➕ Add Product (Wizard)", callback_data="ap:wizard:start"),
    ])
    if total > 0:
        rows.append([
            InlineKeyboardButton(text="⚠️ Clear All Products", callback_data="ap:wipe_confirm"),
        ])
    rows.append([
        InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main"),
    ])

    await c.message.edit_text(
        f"📦 <b>Manage Products ({total} items)</b>\n\nTap any product to edit its price, toggle visibility, adjust delivery ETA, or add stock:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await c.answer()


# ── Wipe All Products Visual Confirmation ────────────────────────────────────

@dp.callback_query(F.data == "ap:wipe_confirm")
async def cb_wipe_all_confirm(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    text = (
        "⚠️ <b>WARNING: REMOVE EVERY PRODUCT?</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "This will permanently remove <b>all products</b> and all available inventory accounts from the store.\n\n"
        "• Past customer orders and delivery logs will remain safe.\n"
        "• This action cannot be undone.\n\n"
        "Are you sure you want to proceed?"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔴 Yes, Wipe All Products", callback_data="ap:wipe_execute"),
            ],
            [
                InlineKeyboardButton(text="❌ Cancel", callback_data="ap:products:0"),
            ],
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data == "ap:wipe_execute")
async def cb_wipe_all_execute(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    async with SessionLocal() as db:
        # Delete unallocated inventory
        avail_items = list((await db.scalars(select(Inventory).where(Inventory.status == "available"))).all())
        for it in avail_items:
            await db.delete(it)
        # Delete all products
        all_ps = list((await db.scalars(select(Product))).all())
        for p in all_ps:
            await db.delete(p)
        await db.commit()

    await c.answer("✅ All products and stock removed!", show_alert=True)
    c.data = "ap:products:0"
    await cb_admin_products(c, FSMContext)  # type: ignore


# ════════════════════════════════════════════════════════════════════════════
# 🛠️ Interactive Product Editor (Visual Buttons)
# ════════════════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("ap:pdetail:"))
async def cb_admin_product_detail(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await c.answer("Product not found.", show_alert=True)
            return
        n = await count_stock(db, p.id)

    if p.is_archived:
        status_str = "📦 Archived (Hidden from Store)"
    elif p.active:
        status_str = "🟢 Active (Visible to Customers)"
    else:
        status_str = "🔴 Inactive (Hidden)"

    bo_str = f"🟢 Allowed (ETA: {p.backorder_eta})" if p.allow_backorder else "🔴 Disabled"

    text = (
        f"<b>{p.emoji} {p.title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Price:</b> <b>{fmt_usd(p.price_usd_cents)}</b>\n"
        f"📦 <b>Available Stock:</b> <b>{n} units</b>\n"
        f"👁 <b>Visibility:</b> {status_str}\n"
        f"🟡 <b>Pre-Order / Backorder:</b> {bo_str}\n"
        f"🔑 <b>SKU:</b> <code>{p.sku}</code>"
    )

    toggle_vis_text = "🔴 Hide from Store" if p.active else "🟢 Show in Store"
    toggle_bo_text = "🟡 Disable Pre-Orders" if p.allow_backorder else "🟡 Enable Pre-Orders"
    archive_text = "🔄 Unarchive" if p.is_archived else "📦 Archive"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💲 Adjust Price", callback_data=f"ap:price_menu:{p.id}"),
                InlineKeyboardButton(text="📥 Add Accounts", callback_data=f"ap:addstock:{p.id}"),
            ],
            [
                InlineKeyboardButton(text=toggle_vis_text, callback_data=f"ap:ptoggle:{p.id}"),
                InlineKeyboardButton(text=toggle_bo_text, callback_data=f"ap:botoggle:{p.id}"),
            ],
            [
                InlineKeyboardButton(text="⏱️ Delivery ETA", callback_data=f"ap:etamenu:{p.id}"),
                InlineKeyboardButton(text=archive_text, callback_data=f"ap:parchive:{p.id}"),
            ],
            [
                InlineKeyboardButton(text="🗑 Delete This Product", callback_data=f"ap:pdel_confirm:{p.id}"),
            ],
            [
                InlineKeyboardButton(text="◀️ Back to Products", callback_data="ap:products:0"),
            ],
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


# ── Visual Price Adjuster (+/- Buttons) ──────────────────────────────────────

@dp.callback_query(F.data.startswith("ap:price_menu:"))
async def cb_price_menu(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await c.answer("Product not found.")
            return

    text = (
        f"💲 <b>Adjust Price for: {p.title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Current Price: <b>{fmt_usd(p.price_usd_cents)}</b>\n\n"
        "Tap the buttons below to raise, lower, or enter a custom price:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="-$50", callback_data=f"ap:adj_price:{p.id}:-50"),
                InlineKeyboardButton(text="-$10", callback_data=f"ap:adj_price:{p.id}:-10"),
                InlineKeyboardButton(text="+$10", callback_data=f"ap:adj_price:{p.id}:10"),
                InlineKeyboardButton(text="+$50", callback_data=f"ap:adj_price:{p.id}:50"),
            ],
            [
                InlineKeyboardButton(text="-$100", callback_data=f"ap:adj_price:{p.id}:-100"),
                InlineKeyboardButton(text="+$100", callback_data=f"ap:adj_price:{p.id}:100"),
            ],
            [
                InlineKeyboardButton(text="✏️ Type Exact Price", callback_data=f"ap:custom_price:{p.id}"),
            ],
            [
                InlineKeyboardButton(text="✅ Done / Back", callback_data=f"ap:pdetail:{p.id}"),
            ],
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("ap:adj_price:"))
async def cb_adjust_price_step(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":")
    pid = int(parts[2])
    diff_dollars = int(parts[3])

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            new_cents = max(100, p.price_usd_cents + (diff_dollars * 100))
            p.price_usd_cents = new_cents
            await db.commit()

    await c.answer(f"Price updated to {fmt_usd(new_cents)}")
    await cb_price_menu(c)


@dp.callback_query(F.data.startswith("ap:custom_price:"))
async def cb_custom_price_prompt(c: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    await state.set_state(AdminEdit.waiting_price_input)
    await state.update_data(product_id=pid)

    await c.message.edit_text(
        "✏️ <b>Enter New Price</b>\n\n"
        "Please type the new price in USD (e.g. <code>350</code>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data=f"ap:price_menu:{pid}")]]
        ),
    )
    await c.answer()


@dp.message(AdminEdit.waiting_price_input)
async def msg_receive_custom_price(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    pid = data.get("product_id")
    await state.clear()

    if not pid:
        await m.answer("Session expired.")
        return

    raw = m.text.strip().replace("$", "").replace(",", "")
    try:
        dollars = float(raw)
        cents = int(dollars * 100)
        if cents <= 0:
            raise ValueError
    except ValueError:
        await m.answer("❌ Invalid price.")
        return

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            p.price_usd_cents = cents
            await db.commit()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📦 Return to Product", callback_data=f"ap:pdetail:{pid}")]]
    )
    await m.answer(f"✅ Price updated to <b>{fmt_usd(cents)}</b>!", reply_markup=kb)


# ── Visual Stock Adder (Paste Accounts directly in Chat) ─────────────────────

@dp.callback_query(F.data.startswith("ap:addstock:"))
async def cb_add_stock_prompt(c: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    await state.set_state(AdminEdit.waiting_stock_accounts)
    await state.update_data(product_id=pid)

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        title = p.title if p else "Product"

    text = (
        f"📥 <b>Add Accounts for: {title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Simply reply to this message with your credentials.\n\n"
        "• Paste <b>1 account</b> or <b>100 accounts</b> (1 per line).\n"
        "• Each line will automatically add <b>+1 unit</b> to available stock.\n\n"
        "<i>Example format:</i>\n"
        "<code>smtp.domain.com:587:user1:pass1\n"
        "smtp.domain.com:587:user2:pass2</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data=f"ap:pdetail:{pid}")]]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.message(AdminEdit.waiting_stock_accounts)
async def msg_receive_stock_accounts(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    data = await state.get_data()
    pid = data.get("product_id")
    await state.clear()

    if not pid:
        await m.answer("Session expired.")
        return

    lines = [l.strip() for l in m.text.splitlines() if l.strip()]
    if not lines:
        await m.answer("No accounts provided.")
        return

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await m.answer("Product not found.")
            return

        for line in lines:
            await add_stock(db, p.id, line)
        await db.commit()
        total_n = await count_stock(db, p.id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📦 Return to Product", callback_data=f"ap:pdetail:{pid}")]]
    )
    await m.answer(
        f"✅ <b>Added {len(lines)} accounts to stock!</b>\n\n"
        f"Product: <b>{p.title}</b>\n"
        f"Available Stock is now: <b>{total_n} units</b>.",
        reply_markup=kb,
    )


# ── Visual Delivery ETA Selector ─────────────────────────────────────────────

@dp.callback_query(F.data.startswith("ap:etamenu:"))
async def cb_eta_menu(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await c.answer("Product not found.")
            return

    text = (
        f"⏱️ <b>Select Delivery Window for: {p.title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Current Window: <b>{p.backorder_eta}</b>\n\n"
        "Tap an option to update the transparent delivery timeframe shown to buyers:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ 1-3 Hours", callback_data=f"ap:set_eta:{pid}:1-3 hours"),
                InlineKeyboardButton(text="🕒 6-12 Hours", callback_data=f"ap:set_eta:{pid}:6-12 hours"),
            ],
            [
                InlineKeyboardButton(text="📅 24 Hours", callback_data=f"ap:set_eta:{pid}:24 hours"),
                InlineKeyboardButton(text="⏳ 24-48 Hours", callback_data=f"ap:set_eta:{pid}:24-48 hours"),
            ],
            [
                InlineKeyboardButton(text="📆 3-5 Days", callback_data=f"ap:set_eta:{pid}:3-5 days"),
            ],
            [
                InlineKeyboardButton(text="◀️ Back to Product", callback_data=f"ap:pdetail:{pid}"),
            ],
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("ap:set_eta:"))
async def cb_set_eta(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    parts = c.data.split(":")
    pid = int(parts[2])
    new_eta = parts[3]

    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            p.backorder_eta = new_eta
            await db.commit()

    await c.answer(f"Delivery window updated to {new_eta} ✅")
    await cb_admin_product_detail(c)


# ── Visual Toggle & Delete ───────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("ap:ptoggle:"))
async def cb_admin_product_toggle(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            p.active = not p.active
            await db.commit()
    await cb_admin_product_detail(c)


@dp.callback_query(F.data.startswith("ap:botoggle:"))
async def cb_admin_product_botoggle(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            p.allow_backorder = not p.allow_backorder
            await db.commit()
    await cb_admin_product_detail(c)


@dp.callback_query(F.data.startswith("ap:parchive:"))
async def cb_admin_product_archive(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            p.is_archived = not p.is_archived
            await db.commit()
    await cb_admin_product_detail(c)


@dp.callback_query(F.data.startswith("ap:pdel_confirm:"))
async def cb_admin_product_del_confirm(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if not p:
            await c.answer("Product not found.")
            return

    text = (
        f"🗑 <b>Confirm Deletion of: {p.title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "This will permanently delete this product and its unallocated stock accounts.\n\n"
        "Are you sure?"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔴 Yes, Delete", callback_data=f"ap:pdel_exec:{pid}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"ap:pdetail:{pid}"),
            ]
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("ap:pdel_exec:"))
async def cb_admin_product_del_exec(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    pid = int(c.data.split(":")[2])
    async with SessionLocal() as db:
        p = await db.get(Product, pid)
        if p:
            avail = list((await db.scalars(select(Inventory).where(Inventory.product_id == p.id, Inventory.status == "available"))).all())
            for item in avail:
                await db.delete(item)
            await db.delete(p)
            await db.commit()
    await c.answer("Product deleted ✅", show_alert=True)
    c.data = "ap:products:0"
    await cb_admin_products(c, FSMContext)  # type: ignore


# ── Admin: Pending Orders & Backorders ───────────────────────────────────────

@dp.callback_query(F.data == "ap:pending")
async def cb_admin_pending_orders(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as db:
        orders = list(
            (
                await db.scalars(
                    select(Order)
                    .where(
                        Order.payment_status.in_(["waiting", "claimed"]),
                        Order.fulfillment_status == "pending",
                    )
                    .order_by(desc(Order.id))
                    .limit(10)
                )
            ).all()
        )
        products_map = {}
        for o in orders:
            products_map[o.product_id] = await db.get(Product, o.product_id)

    if not orders:
        await c.message.edit_text(
            "⏳ <b>Pending Orders</b>\n\n✅ <i>No orders currently waiting for verification!</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main")]]),
        )
        await c.answer()
        return

    text = f"⏳ <b>Pending Orders ({len(orders)})</b>\n\n"
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        p = products_map.get(o.product_id)
        p_title = p.title if p else "Product"
        type_tag = "[Pre-Order]" if o.is_backorder else "[Instant Stock]"
        text += (
            f"<b>Order #{o.id}</b> {type_tag} · {fmt_usd(o.amount_usd_cents)} ({o.pay_currency})\n"
            f"Item: {p_title} × {o.quantity}\n"
            f"User ID: <code>{o.telegram_user_id}</code>\n\n"
        )
        rows.append([
            InlineKeyboardButton(text=f"✅ Confirm #{o.id}", callback_data=f"adm_confirm:{o.id}"),
            InlineKeyboardButton(text=f"❌ Reject #{o.id}", callback_data=f"adm_reject:{o.id}"),
        ])

    rows.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data="ap:pending"),
        InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main"),
    ])
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data == "ap:backorders")
async def cb_admin_backorders(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return

    async with SessionLocal() as db:
        orders = list(
            (
                await db.scalars(
                    select(Order)
                    .where(Order.fulfillment_status == "backordered")
                    .order_by(desc(Order.id))
                    .limit(10)
                )
            ).all()
        )
        products_map = {}
        for o in orders:
            products_map[o.product_id] = await db.get(Product, o.product_id)

    if not orders:
        await c.message.edit_text(
            "🟡 <b>Queued Backorders</b>\n\n✅ <i>No unfulfilled backorders in queue.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main")]]),
        )
        await c.answer()
        return

    text = f"🟡 <b>Queued Backorders ({len(orders)})</b>\n\nFulfill an order once you've restocked inventory:\n\n"
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        p = products_map.get(o.product_id)
        p_title = p.title if p else "Product"
        text += (
            f"<b>Order #{o.id}</b> · {p_title} × {o.quantity}\n"
            f"Customer: <code>{o.telegram_user_id}</code> · {fmt_usd(o.amount_usd_cents)}\n\n"
        )
        rows.append([
            InlineKeyboardButton(text=f"📥 Deliver Backorder #{o.id}", callback_data=f"adm_confirm:{o.id}")
        ])

    rows.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data="ap:backorders"),
        InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main"),
    ])
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


# ── Admin: Wallets & Stats ───────────────────────────────────────────────────

@dp.callback_query(F.data == "ap:wallets")
async def cb_admin_wallets(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    lines = []
    for method, info in PAYMENT_METHODS.items():
        lines.append(f"<b>{info['label']}</b>\nNetwork: {info['network']}\n<code>{info['address']}</code>")
    text = "💳 <b>Active Payment Wallets</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + "\n\n".join(lines)
    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main")]]
        ),
    )
    await c.answer()


@dp.callback_query(F.data == "ap:stats")
async def cb_admin_stats_view(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    data = await get_admin_dashboard_data()
    text = (
        "📊 <b>Store Analytics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Total Revenue:</b> {fmt_usd(data['total_revenue'])}\n"
        f"✅ <b>Delivered Orders:</b> {data['delivered_orders']}\n"
        f"🟡 <b>Queued Backorders:</b> {data['backordered_orders']}\n"
        f"⏳ <b>Awaiting Verification:</b> {data['pending_orders']}\n"
        f"📦 <b>Available Stock Units:</b> {data['total_stock']}\n"
        f"🏷 <b>Active Products:</b> {data['active_products']}\n"
        f"⭐ <b>Verified Reviews:</b> {data['total_reviews']}\n"
        f"👥 <b>Total Customers:</b> {data['total_users']}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Refresh", callback_data="ap:stats")],
                [InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main")],
            ]
        ),
    )
    await c.answer()


@dp.callback_query(F.data == "ap:broadcasthelp")
async def cb_admin_broadcasthelp(c: CallbackQuery) -> None:
    if not is_admin(c.from_user.id):
        return
    text = (
        "📢 <b>Broadcast Announcement</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send an announcement message to all registered users:\n\n"
        "<b>Command:</b>\n"
        "<code>/broadcast Your message here</code>\n\n"
        "Supports standard HTML formatting."
    )
    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Admin Menu", callback_data="ap:main")]]
        ),
    )
    await c.answer()


@dp.message(Command("broadcast"))
async def cmd_broadcast_msg(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    text = m.text.partition(" ")[2].strip()
    if not text:
        await m.answer("Usage: <code>/broadcast Your message here</code>")
        return

    async with SessionLocal() as db:
        users = list((await db.scalars(select(User).where(User.banned.is_(False)))).all())

    sent = failed = 0
    for u in users:
        try:
            await bot.send_message(u.telegram_id, f"📢 <b>Announcement</b>\n\n{text}")
            sent += 1
        except Exception:
            failed += 1

    await m.answer(f"📢 Broadcast finished: <b>{sent} delivered</b>, {failed} failed.")
