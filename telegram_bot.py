"""Telegram bot (§10) — the whole interface. Run: python telegram_bot.py

Commands: /scan /top /why <id> /status /kinds. Inline buttons 🔥 🗑 👀 write
feedback / mute / pin. Muted clusters are excluded from /top; every response
carries the run-status footer. /scan runs the full pipeline and streams
progress so the operator sees it working (§10.2).

Only the configured chat is served.
"""

from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import actions
import digest
import scan
from config import load_config
from db import DB
from embed import Embedder
from judge import PreflightError
from llm import LLM

CONFIG = load_config()
DB_ = DB.from_config(CONFIG)
LLM_ = LLM(CONFIG)
_EMBEDDER: Embedder | None = None
_SCANNING = False


def _embedder() -> Embedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder.from_config(CONFIG)
    return _EMBEDDER


def _authorized(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and str(chat.id) == str(CONFIG.telegram_chat_id)


def _card_keyboard(card: digest.Card) -> InlineKeyboardMarkup:
    rows = []
    if card.url:
        rows.append([InlineKeyboardButton("🔗 source", url=card.url)])
    rows.append([
        InlineKeyboardButton("🔥", callback_data=f"fire:{card.cluster_id}"),
        InlineKeyboardButton("🗑", callback_data=f"mute:{card.cluster_id}"),
        InlineKeyboardButton("👀", callback_data=f"pin:{card.cluster_id}"),
    ])
    return InlineKeyboardMarkup(rows)


async def _send_digest(bot, chat_id: int) -> None:
    recurring, single = digest.top_clusters(DB_)
    if not recurring and not single:
        await bot.send_message(chat_id, "Nothing in the feed yet. Run /scan.")
        await bot.send_message(chat_id, digest.run_footer(DB_))
        return
    for card in recurring:
        await bot.send_message(chat_id, digest.format_card(card),
                               reply_markup=_card_keyboard(card),
                               disable_web_page_preview=True)
    if single:
        await bot.send_message(chat_id, "📄 Worth reading")
        for card in single:
            await bot.send_message(chat_id, digest.format_card(card),
                                   reply_markup=_card_keyboard(card),
                                   disable_web_page_preview=True)
    await bot.send_message(chat_id, digest.run_footer(DB_))


# --- commands --------------------------------------------------------------

async def start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "painminer\n/scan — run now\n/top — leaderboard\n/why <id> — evidence\n"
        "/status — last run, queue, sources\n/kinds — breakdown by kind"
    )


async def top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await _send_digest(ctx.bot, update.effective_chat.id)


async def status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(digest.status_text(DB_), disable_web_page_preview=True)


async def kinds(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(digest.kinds_text(DB_) + "\n\n" + digest.run_footer(DB_))


async def why(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    if not ctx.args or not ctx.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Usage: /why <cluster id>")
        return
    cid = int(ctx.args[0].lstrip("#"))
    text = digest.why_text(DB_, cid) + "\n\n" + digest.run_footer(DB_)
    await update.message.reply_text(text[:4000], disable_web_page_preview=True)


async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    global _SCANNING
    if _SCANNING:
        await update.message.reply_text("A scan is already running.")
        return
    _SCANNING = True
    status_msg = await update.message.reply_text("Starting scan…")
    holder = {"text": "Starting scan…"}

    def progress(msg: str) -> None:        # called from the worker thread
        holder["text"] = msg

    task = asyncio.create_task(
        asyncio.to_thread(scan.run_scan, DB_, LLM_, CONFIG,
                          embedder=_embedder(), progress=progress)
    )
    last = None
    try:
        while not task.done():
            if holder["text"] != last:
                last = holder["text"]
                try:
                    await status_msg.edit_text(last)
                except Exception:
                    pass
            await asyncio.sleep(2)
        summary = await task
    except PreflightError as exc:
        await status_msg.edit_text(f"Scan aborted: {exc}")
        return
    except Exception as exc:
        await status_msg.edit_text(f"Scan failed: {exc}")
        return
    finally:
        _SCANNING = False

    await status_msg.edit_text(
        f"Scan complete: fetched {summary.items_fetched}, judged {summary.judged} "
        f"({summary.empty} empty), {summary.findings_created} findings, "
        f"{summary.new_clusters} new clusters, {summary.seconds_per_item:.1f}s/item"
        + (" [stopped on time budget]" if summary.stopped_on_budget else "")
    )
    await _send_digest(ctx.bot, update.effective_chat.id)


async def on_button(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return
    try:
        verb, cid = query.data.split(":", 1)
        msg = actions.DISPATCH[verb](DB_, int(cid))
    except Exception as exc:
        await query.answer(f"error: {exc}", show_alert=False)
        return
    await query.answer(msg, show_alert=False)


def build_app() -> Application:
    app = Application.builder().token(CONFIG.telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("why", why))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("kinds", kinds))
    app.add_handler(CallbackQueryHandler(on_button))
    return app


def main() -> None:
    print("painminer bot starting (polling)…")
    build_app().run_polling()


if __name__ == "__main__":
    main()
