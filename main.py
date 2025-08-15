import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Enable logging (helps us debug Railway crashes)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get tokens from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
LIVECOINWATCH_API_KEY = os.getenv("LIVECOINWATCH_API_KEY")

BASE_URL = "https://api.livecoinwatch.com/coins/single"

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hey! Send /price BTC to get Bitcoin price.")

# Price command
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a symbol, e.g., /price BTC")
        return

    symbol = context.args[0].upper()

    try:
        headers = {
            "content-type": "application/json",
            "x-api-key": LIVECOINWATCH_API_KEY
        }
        payload = {
            "currency": "USD",
            "code": symbol,
            "meta": False
        }
        response = requests.post(BASE_URL, json=payload, headers=headers)
        data = response.json()

        if "rate" in data:
            price_usd = round(data["rate"], 2)
            await update.message.reply_text(f"💰 {symbol} price: ${price_usd}")
        else:
            await update.message.reply_text("❌ Could not fetch price. Check symbol or API key.")

    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        await update.message.reply_text("⚠️ An error occurred.")

def main():
    if not BOT_TOKEN or not LIVECOINWATCH_API_KEY:
        logger.error("Missing BOT_TOKEN or LIVECOINWATCH_API_KEY environment variable")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))

    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
