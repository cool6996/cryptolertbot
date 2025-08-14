import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Load tokens from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")

BASE_URL = "https://api.livecoinwatch.com/coins/single"

# Command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "🚀 Welcome to Crypto Alerts Bot!\n\n"
        "Use /price <symbol> to get the latest price.\n"
        "Example: /price BTC\n\n"
        "You can also check:\n"
        "• BTC\n"
        "• ETH\n"
        "• SOL\n\n"
        "💡 Tip: Use only well-known coin tickers for best results."
    )
    await update.message.reply_text(welcome_message)

# Command: /price
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a coin symbol. Example: /price BTC")
        return

    coin_symbol = context.args[0].upper()

    headers = {
        "content-type": "application/json",
        "x-api-key": API_KEY
    }
    data = {
        "currency": "USD",
        "code": coin_symbol,
        "meta": False
    }

    try:
        response = requests.post(BASE_URL, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            price_usd = result.get("rate")
            if price_usd:
                await update.message.reply_text(f"💰 {coin_symbol} Price: ${price_usd:,.2f}")
            else:
                await update.message.reply_text("⚠ Could not find that coin. Try another symbol.")
        else:
            await update.message.reply_text("⚠ Error fetching data. Please try again later.")
    except Exception as e:
        await update.message.reply_text(f"⚠ An error occurred: {str(e)}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))

    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
  
