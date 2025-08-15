import math
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Read API key (support both names, use whichever you set on Railway)
LIVECOINWATCH_API_KEY = os.getenv("LIVECOINWATCH_API_KEY") or os.getenv("API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_URL = "https://api.livecoinwatch.com/coins/single"

def _abbr(n: float) -> str:
    """Abbreviate large numbers: 1,234,567 -> 1.23M"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    if n == 0:
        return "0"
    units = ["", "K", "M", "B", "T", "Q"]
    k = 1000.0
    i = int(math.floor(math.log(abs(n), k)))
    i = max(0, min(i, len(units) - 1))
    val = n / (k ** i)
    return f"{val:.2f}{units[i]}"

def _delta_emoji(pct: float) -> str:
    if pct is None:
        return ""
    return "🟢" if pct >= 0 else "🔴"

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /price <symbol>\nExample: /price BTC")
        return

    symbol = context.args[0].upper()

    if not LIVECOINWATCH_API_KEY:
        await update.message.reply_text("API key missing on server. (Owner: set LIVECOINWATCH_API_KEY in Railway.)")
        return

    headers = {
        "content-type": "application/json",
        "x-api-key": LIVECOINWATCH_API_KEY
    }
    payload = {
        "currency": "USD",
        "code": symbol,
        "meta": True
    }

    try:
        r = requests.post(BASE_URL, headers=headers, json=payload, timeout=10)
        if r.status_code != 200:
            await update.message.reply_text("⚠️ API error. Try another symbol or later.")
            return

        data = r.json() or {}
        price_usd = data.get("rate")
        cap = data.get("cap")
        vol_24h = data.get("volume")
        delta_day = None
        if isinstance(data.get("delta"), dict):
            delta_day = data["delta"].get("day")

        if price_usd is None:
            await update.message.reply_text("❌ Coin not found. Try a common ticker like BTC / ETH / SOL.")
            return

        arrow = _delta_emoji(delta_day)
        delta_txt = f"{delta_day:+.2f}%" if isinstance(delta_day, (int, float)) else "N/A"

        msg = (
            f"💰 <b>{symbol}</b>\n"
            f"• Price: <b>${price_usd:,.2f}</b> {arrow} ({delta_txt} 24h)\n"
            f"• Market Cap: ${_abbr(cap)}\n"
            f"• 24h Volume: ${_abbr(vol_24h)}\n"
        )

        await update.message.reply_text(msg, parse_mode="HTML")

    except requests.Timeout:
        await update.message.reply_text("⏳ API timed out. Please try again.")
    except Exception:
        await update.message.reply_text("⚠️ Unexpected error. Try again in a moment.")

# --- Main app ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set on server.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register the /price handler
    app.add_handler(CommandHandler("price", price))

    print("✅ Bot is running...")
    app.run_polling()
