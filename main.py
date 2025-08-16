import os
import math
import logging
from typing import Optional, List, Dict, Tuple, Set

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# ───────── Logging ─────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("cryptolertbot")

# ───────── Env ─────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
LIVECOINWATCH_API_KEY = os.getenv("LIVECOINWATCH_API_KEY") or os.getenv("API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")  # e.g. postgresql://B:C@A:E/D

LCW_SINGLE = "https://api.livecoinwatch.com/coins/single"
LCW_LIST = "https://api.livecoinwatch.com/coins/list"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=2&format=json"

USE_DB = bool(DATABASE_URL)

# ───────── DB (psycopg2, lazy init) ─────────
conn = None  # psycopg2 connection

def db_connect():
    """Connect once and ensure alerts table exists."""
    global conn
    if not USE_DB:
        return
    if conn is not None:
        return
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                chat_id BIGINT NOT NULL,
                symbol TEXT NOT NULL,
                target NUMERIC NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('above','below')),
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)
        conn.commit()

def db_add_alert(user_id: int, chat_id: int, symbol: str, target: float, direction: str):
    db_connect()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO alerts (user_id, chat_id, symbol, target, direction) VALUES (%s, %s, %s, %s, %s)",
            (user_id, chat_id, symbol.upper(), target, direction)
        )
        conn.commit()

def db_list_alerts(user_id: int) -> List[Tuple[int, str, float, str]]:
    db_connect()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, symbol, target, direction FROM alerts WHERE user_id = %s ORDER BY created_at DESC LIMIT 50",
            (user_id,)
        )
        rows = cur.fetchall()
    return rows

def db_delete_alert(alert_id: int, user_id: int) -> bool:
    db_connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM alerts WHERE id = %s AND user_id = %s", (alert_id, user_id))
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted

def db_fetch_all_alerts() -> List[Tuple[int, int, int, str, float, str]]:
    """Return list of (id, user_id, chat_id, symbol, target, direction)."""
    db_connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id, user_id, chat_id, symbol, target, direction FROM alerts")
        return cur.fetchall()

def db_delete_by_id(alert_id: int):
    db_connect()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM alerts WHERE id = %s", (alert_id,))
        conn.commit()

# ───────── Helpers ─────────
def _abbr(n: Optional[float]) -> str:
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

def _fmt_price(x: Optional[float]) -> str:
    try:
        return f"${float(x):,.2f}"
    except:
        return "$-"

def _delta_emoji(pct: Optional[float]) -> str:
    if pct is None:
        return ""
    return "🟢" if pct >= 0 else "🔴"

def _headers() -> Dict[str, str]:
    return {
        "content-type": "application/json",
        "x-api-key": LIVECOINWATCH_API_KEY or ""
    }

def lcw_single(symbol: str) -> Optional[Dict]:
    """Fetch single coin data with meta."""
    try:
        r = requests.post(LCW_SINGLE, headers=_headers(), json={
            "currency": "USD",
            "code": symbol.upper().strip(),
            "meta": True
        }, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        log.error(f"LCW single error: {e}")
        return None

def lcw_list(limit: int = 200) -> List[Dict]:
    """Fetch top coins by rank with meta; we'll sort locally for gainers/losers/trending."""
    try:
        r = requests.post(LCW_LIST, headers=_headers(), json={
            "currency": "USD",
            "sort": "rank",
            "order": "ascending",
            "offset": 0,
            "limit": max(10, min(limit, 300)),
            "meta": True
        }, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"LCW list error: {e}")
        return []

# ───────── Commands ─────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 <b>Welcome to Crypto Alerts (@cryptolertbot)</b>\n"
        "Commands:\n"
        "• /price <symbol> – price, 24h change, cap & volume\n"
        "• /gainers – top 10 gainers (24h)\n"
        "• /losers – top 10 losers (24h)\n"
        "• /trending – hot coins by 24h volume\n"
        "• /convert <amt> <from> <to> – convert coins or to USD\n"
        "• /feargreed – market sentiment (Fear & Greed)\n"
        "• /alert <symbol> <price> – set a price alert (auto-deletes)\n"
        "• /myalerts – list your alerts\n"
        "• /delalert <id> – delete an alert by id\n"
        f"\nAlerts: {'✅ enabled' if USE_DB else '❌ disabled (owner must add DATABASE_URL)'}"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /price <symbol>\nExample: /price BTC")
        return
    if not LIVECOINWATCH_API_KEY:
        await update.message.reply_text("API key missing on server.")
        return

    sym = context.args[0].upper()
    data = lcw_single(sym)
    if not data or data.get("rate") is None:
        await update.message.reply_text("❌ Coin not found. Try BTC / ETH / SOL.")
        return

    price_usd = data.get("rate")
    cap = data.get("cap")
    vol_24h = data.get("volume")
    d = data.get("delta") or {}
    delta_day = d.get("day")

    arrow = _delta_emoji(delta_day)
    delta_txt = f"{delta_day:+.2f}%" if isinstance(delta_day, (int, float)) else "N/A"

    msg = (
        f"💰 <b>{sym}</b>\n"
        f"• Price: <b>{_fmt_price(price_usd)}</b> {arrow} ({delta_txt} 24h)\n"
        f"• Market Cap: ${_abbr(cap)}\n"
        f"• 24h Volume: ${_abbr(vol_24h)}\n"
        f"\nTip: Try /price ETH or /price SOL"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def gainers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LIVECOINWATCH_API_KEY:
        await update.message.reply_text("API key missing on server.")
        return
    coins = lcw_list(200)
    coins = [c for c in coins if isinstance(c.get("delta"), dict) and c["delta"].get("day") is not None]
    coins.sort(key=lambda c: c["delta"]["day"], reverse=True)
    top = coins[:10]
    if not top:
        await update.message.reply_text("No data right now. Try later.")
        return

    lines = ["📈 <b>Top Gainers (24h)</b>"]
    for i, c in enumerate(top, 1):
        sym = c.get("code", "?")
        pct = c["delta"]["day"]
        rate = c.get("rate")
        lines.append(f"{i}. <b>{sym}</b>  {pct:+.2f}%  —  {_fmt_price(rate)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def losers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LIVECOINWATCH_API_KEY:
        await update.message.reply_text("API key missing on server.")
        return
    coins = lcw_list(200)
    coins = [c for c in coins if isinstance(c.get("delta"), dict) and c["delta"].get("day") is not None]
    coins.sort(key=lambda c: c["delta"]["day"])  # biggest drop first
    top = coins[:10]
    if not top:
        await update.message.reply_text("No data right now. Try later.")
        return

    lines = ["📉 <b>Top Losers (24h)</b>"]
    for i, c in enumerate(top, 1):
        sym = c.get("code", "?")
        pct = c["delta"]["day"]
        rate = c.get("rate")
        lines.append(f"{i}. <b>{sym}</b>  {pct:+.2f}%  —  {_fmt_price(rate)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trending proxy: highest 24h volume among top caps."""
    if not LIVECOINWATCH_API_KEY:
        await update.message.reply_text("API key missing on server.")
        return
    coins = lcw_list(200)
    coins = [c for c in coins if c.get("volume") and c.get("rate")]
    coins.sort(key=lambda c: c["volume"], reverse=True)
    top = coins[:10]
    if not top:
        await update.message.reply_text("No data right now. Try later.")
        return

    lines = ["🔥 <b>Trending by 24h Volume</b>"]
    for i, c in enumerate(top, 1):
        sym = c.get("code", "?")
        rate = c.get("rate")
        vol = c.get("volume")
        d = c.get("delta") or {}
        pct = d.get("day")
        arrow = _delta_emoji(pct)
        pct_txt = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"
        lines.append(f"{i}. <b>{sym}</b> — {_fmt_price(rate)} | Vol: ${_abbr(vol)} | {arrow} {pct_txt}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def convert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /convert 3 btc usd   or   /convert 0.5 eth sol """
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /convert <amount> <from> <to>\nEx: /convert 3 btc usd\nEx: /convert 0.5 eth sol")
        return
    if not LIVECOINWATCH_API_KEY:
        await update.message.reply_text("API key missing on server.")
        return

    try:
        amount = float(context.args[0])
    except:
        await update.message.reply_text("First argument must be a number (amount).")
        return

    from_sym = context.args[1].upper()
    to_sym = context.args[2].upper()

    from_data = lcw_single(from_sym)
    if not from_data or from_data.get("rate") is None:
        await update.message.reply_text("Invalid source coin.")
        return
    from_rate = float(from_data["rate"])

    if to_sym in ("USD", "USDT", "USDC"):
        result = amount * from_rate
        await update.message.reply_text(f"💱 {amount:g} {from_sym} ≈ {_fmt_price(result)}")
        return

    to_data = lcw_single(to_sym)
    if not to_data or to_data.get("rate") is None:
        await update.message.reply_text("Invalid target coin.")
        return
    to_rate = float(to_data["rate"])

    result = amount * (from_rate / to_rate)
    decimals = 8 if result < 1 else 4
    await update.message.reply_text(f"💱 {amount:g} {from_sym} ≈ {result:.{decimals}f} {to_sym}")

async def feargreed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        r = requests.get(FEAR_GREED_URL, timeout=10)
        if r.status_code != 200:
            await update.message.reply_text("Could not fetch Fear & Greed right now.")
            return
        data = r.json().get("data", [])
        if not data:
            await update.message.reply_text("No sentiment data available.")
            return
        cur = data[0]
        cur_val = int(cur.get("value", 0))
        cur_cls = cur.get("value_classification", "Unknown")
        prev_val = int(data[1].get("value", 0)) if len(data) > 1 else None
        trend = "↗️" if (prev_val is not None and cur_val > prev_val) else ("↘️" if (prev_val is not None and cur_val < prev_val) else "→")
        await update.message.reply_text(
            f"😶‍🌫️ <b>Fear & Greed Index</b>\n• Now: <b>{cur_val}</b> ({cur_cls}) {trend}",
            parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text("Error fetching sentiment.")

# ───────── Alerts (DB-backed) ─────────
async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not USE_DB:
        await update.message.reply_text("Alerts are disabled (owner must add a free Postgres + DATABASE_URL).")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /alert <symbol> <price>\nEx: /alert btc 125000")
        return
    sym = context.args[0].upper()
    try:
        target = float(context.args[1])
    except:
        await update.message.reply_text("Price must be a number.")
        return

    data = lcw_single(sym)
    if not data or data.get("rate") is None:
        await update.message.reply_text("Unknown coin symbol.")
        return
    price_now = float(data["rate"])
    direction = "above" if target >= price_now else "below"

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        db_add_alert(user_id, chat_id, sym, target, direction)
        await update.message.reply_text(
            f"⏰ Alert set for <b>{sym}</b> {direction} <b>{_fmt_price(target)}</b>\n(Current: {_fmt_price(price_now)})",
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"Add alert error: {e}")
        await update.message.reply_text("Failed to save alert. Try again later.")

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not USE_DB:
        await update.message.reply_text("Alerts are disabled.")
        return
    rows = db_list_alerts(update.effective_user.id)
    if not rows:
        await update.message.reply_text("You have no alerts. Set one with /alert <symbol> <price>.")
        return
    lines = ["📝 <b>Your Alerts</b>"]
    for (aid, sym, tgt, direction) in rows:
        lines.append(f"• ID {aid}: <b>{sym}</b> {direction} { _fmt_price(float(tgt)) }")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def delalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not USE_DB:
        await update.message.reply_text("Alerts are disabled.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /delalert <id>")
        return
    try:
        aid = int(context.args[0])
    except:
        await update.message.reply_text("Alert id must be a number.")
        return
    ok = db_delete_alert(aid, update.effective_user.id)
    await update.message.reply_text("✅ Deleted." if ok else "Alert not found.")

# Job: check alerts every 2 minutes
async def alert_check_job(context: ContextTypes.DEFAULT_TYPE):
    if not USE_DB:
        return
    try:
        rows = db_fetch_all_alerts()
        if not rows:
            return

        # unique symbols -> fewer API calls
        symbols: Set[str] = set(r[3].upper() for r in rows)
        prices: Dict[str, Optional[float]] = {}

        for sym in symbols:
            data = lcw_single(sym)
            prices[sym] = float(data["rate"]) if data and data.get("rate") is not None else None

        # evaluate
        for (aid, user_id, chat_id, sym, target, direction) in rows:
            sym = sym.upper()
            price_now = prices.get(sym)
            if price_now is None:
                continue
            hit = (direction == "above" and price_now >= float(target)) or (direction == "below" and price_now <= float(target))
            if hit:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🔔 <b>{sym}</b> alert triggered!\nPrice is {_fmt_price(price_now)} (target {direction} {_fmt_price(float(target))})",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    log.error(f"Send alert msg error: {e}")
                try:
                    db_delete_by_id(aid)
                except Exception as e:
                    log.error(f"Delete alert error: {e}")

    except Exception as e:
        log.error(f"alert_check_job error: {e}")

# ───────── Main ─────────
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set")
    if not LIVECOINWATCH_API_KEY:
        log.warning("LIVECOINWATCH_API_KEY not set — price/gainers/losers/trending/convert will fail.")

    if USE_DB:
        try:
            db_connect()
            log.info("DB connected. Alerts enabled.")
        except Exception as e:
            log.error(f"DB connection failed; alerts disabled. Error: {e}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("gainers", gainers))
    app.add_handler(CommandHandler("losers", losers))
    app.add_handler(CommandHandler("trending", trending))
    app.add_handler(CommandHandler("convert", convert_cmd))
    app.add_handler(CommandHandler("feargreed", feargreed))
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("myalerts", myalerts))
    app.add_handler(CommandHandler("delalert", delalert))

    # Jobs
    app.job_queue.run_repeating(alert_check_job, interval=120, first=15)

    log.info("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
