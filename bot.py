import time
import logging
import requests
import base64
import hashlib
import hmac
import json
import uuid
import os
import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime, timedelta
import telegram
from telegram.error import TelegramError
from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import feedparser
import asyncio

# Logging configuration (Console logging for Heroku)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
GROK_API_KEY = os.getenv('GROK_API_KEY')
KUCOIN_API_KEY = os.getenv('KUCOIN_API_KEY')
KUCOIN_API_SECRET = os.getenv('KUCOIN_API_SECRET')
KUCOIN_API_PASSPHRASE = os.getenv('KUCOIN_API_PASSPHRASE')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Telegram bot
telegram_bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

# Constants
SYMBOL = "ETHUSDTM"
TAKE_PROFIT_PCT = 0.002  # 0.1%
DEEPSEARCH_INTERVAL = 4 * 3600  # 4 hours
DEEPSEARCH_PER_DAY = 6
MIN_BALANCE = 5  # Minimum 5 USDT
LEVERAGE_MAX = 10  # Maximum 10x
LEVERAGE_FALLBACK = 5  # Fallback to 5x if insufficient balance

# Global variables
last_deepsearch_result = None
last_deepsearch_time = 0
current_price_cache = {'price': None, 'timestamp': 0}
last_position = None  # Track last position

class KcSigner:
    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = self.sign(api_passphrase.encode('utf-8'), api_secret.encode('utf-8'))

    def sign(self, plain: bytes, key: bytes) -> str:
        hm = hmac.new(key, plain, hashlib.sha256)
        return base64.b64encode(hm.digest()).decode()

    def headers(self, plain: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        signature = self.sign((timestamp + plain).encode('utf-8'), self.api_secret.encode('utf-8'))
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-PASSPHRASE": self.api_passphrase,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-SIGN": signature,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json"
        }

def safe_headers(headers):
    safe = headers.copy()
    safe["KC-API-SIGN"] = "****"
    safe["KC-API-PASSPHRASE"] = "****"
    return safe

def get_klines(granularity=60, limit=200):
    try:
        url = f"https://api-futures.kucoin.com/api/v1/kline/query?symbol={SYMBOL}&granularity={granularity}&limit={limit}"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"K-line response: {data}")
        if data.get('code') == '200000':
            klines = data.get('data', [])
            if not klines:
                logger.warning(f"No data for {granularity}")
                return None
            df = pd.DataFrame(klines, columns=["time", "open", "high", "low", "close", "volume"])
            df["close"] = df["close"].astype(float)
            return df
        logger.error(f"Failed to get K-line: {data.get('msg', 'Unknown error')}")
        return None
    except Exception as e:
        logger.error(f"K-line error: {str(e)}")
        return None

def calculate_indicators():
    try:
        indicators = {}
        timeframes = {60: "1h", 240: "4h", 1440: "1d", 10080: "1w"}
        for granularity, tf_name in timeframes.items():
            df = get_klines(granularity, 200)
            if df is None or len(df) < 200:
                logger.warning(f"Insufficient data for {tf_name}")
                continue
            df["RSI"] = ta.rsi(df["close"], length=14)
            df["MA200"] = ta.sma(df["close"], length=200)
            df["EMA50"] = ta.ema(df["close"], length=50)
            indicators[tf_name] = {
                "RSI": df["RSI"].iloc[-1],
                "MA200": df["MA200"].iloc[-1],
                "EMA50": df["EMA50"].iloc[-1],
                "PRICE": df["close"].iloc[-1]
            }
        logger.info(f"Indicators: {indicators}")
        return indicators
    except Exception as e:
        logger.error(f"Indicator calculation error: {str(e)}")
        return None

def get_grok_signal(indicators, deepsearch_result):
    try:
        if not indicators or not deepsearch_result:
            logger.warning(f"Grok signal: Missing data, indicators: {indicators}, deepsearch_result: {deepsearch_result}")
            return "wait"
        
        score = 0
        for tf, ind in indicators.items():
            if ind["RSI"] < 30:
                score += 0.2
            elif ind["RSI"] > 70:
                score -= 0.2
            if ind["EMA50"] > ind["MA200"]:
                score += 0.1
        
        if deepsearch_result["sentiment"] == "Bullish":
            score += 0.3
        elif deepsearch_result["sentiment"] == "Bearish":
            score -= 0.3
        
        logger.info(f"Grok signal score: {score}")
        if score >= 0.3:
            return "buy"
        elif score <= -0.3:
            return "sell"
        return "wait"
    except Exception as e:
        logger.error(f"Grok signal error: {str(e)}")
        return "wait"

def run_deepsearch():
    global last_deepsearch_result, last_deepsearch_time
    try:
        if time.time() - last_deepsearch_time < DEEPSEARCH_INTERVAL:
            logger.info("DeepSearch: Using last result")
            return last_deepsearch_result
        
        feeds = [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss"
        ]
        
        crypto_news = []
        for feed_url in feeds:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "").lower()
                summary = entry.get("summary", "").lower()
                text = title + " " + summary
                if any(keyword in text for keyword in ["bitcoin", "ethereum", "crypto", "blockchain"]):
                    if not any(keyword in text for keyword in ["celebrity", "gossip", "entertainment"]):
                        crypto_news.append({
                            "title": entry.get("title", ""),
                            "summary": entry.get("summary", ""),
                            "link": entry.get("link", ""),
                            "text": text
                        })
        
        if not crypto_news:
            logger.info("DeepSearch: No crypto news found, returning Neutral")
            last_deepsearch_result = {"sentiment": "Neutral", "timestamp": time.time()}
            last_deepsearch_time = time.time()
            return last_deepsearch_result
        
        analyzer = SentimentIntensityAnalyzer()
        sentiment_scores = []
        reg_spec_contexts = []
        for news in crypto_news:
            text = f"{news['title']}: {news['summary']}"
            score = analyzer.polarity_scores(text)["compound"]
            reg_keywords = ["regulation", "sec", "law", "policy", "compliance"]
            spec_keywords = ["speculation", "rally", "crash", "bubble", "surge", "dip"]
            is_regulation = any(keyword in news["text"] for keyword in reg_keywords)
            is_speculation = any(keyword in text for keyword in spec_keywords)
            if is_regulation:
                score *= 1.1
                reg_spec_contexts.append(f"Regulation: {news['title']}")
            if is_speculation:
                score *= 1.05
                reg_spec_contexts.append(f"Speculation: {news['title']}")
            sentiment_scores.append(score)
        
        avg_score = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
        sentiment = "Bullish" if avg_score > 0.1 else "Bearish" if avg_score < -0.1 else "Neutral"
        
        if reg_spec_contexts:
            logger.info(f"DeepSearch: Regulation/Speculation contexts: {reg_spec_contexts}")
        
        last_deepsearch_result = {"sentiment": sentiment, "timestamp": time.time()}
        last_deepsearch_time = time.time()
        return last_deepsearch_result
    
    except Exception as e:
        logger.error(f"DeepSearch error: {str(e)}")
        return last_deepsearch_result if last_deepsearch_result else {"sentiment": "Neutral", "timestamp": time.time()}

def check_usdm_balance():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/account-overview?currency=USDT"
        payload = "GET/api/v1/account-overview?currency=USDT"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Balance response: {data}")
        if data.get('code') == '200000':
            usdt_balance = float(data.get('data', {}).get('availableBalance', 0))
            position_margin = float(data.get('data', {}).get('positionMargin', 0))
            return usdt_balance, position_margin
        logger.error(f"USD-M balance check failed: {data.get('msg', 'Unknown error')}")
        return 0, 0
    except Exception as e:
        logger.error(f"Balance error: {str(e)}")
        return 0, 0

def get_contract_details():
    try:
        url = "https://api-futures.kucoin.com/api/v1/contracts/active"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"Contract response: {data}")
        if data.get('code') == '200000':
            for contract in data.get('data', []):
                if contract.get('symbol') == SYMBOL:
                    return {
                        "multiplier": float(contract.get('multiplier', 0.001)),
                        "min_order_size": int(contract.get('minOrderQty', 1)),
                        "max_leverage": int(contract.get('maxLeverage', 20)),
                        "tick_size": float(contract.get('tickSize', 0.01))
                    }
            logger.warning(f"{SYMBOL} contract not found")
            return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20, "tick_size": 0.01}
        logger.error(f"Failed to get contract details: {data.get('msg', 'Unknown error')}")
        return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20, "tick_size": 0.01}
    except Exception as e:
        logger.error(f"Contract details error: {str(e)}")
        return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20, "tick_size": 0.01}

def check_positions():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/positions?symbol={SYMBOL}"
        payload = f"GET/api/v1/positions?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Position response: {data}")
        if data.get('code') == '200000':
            positions = data.get('data', [])
            result = []
            for pos in positions:
                result.append({
                    "side": "long" if pos.get('currentQty', 0) > 0 else "short",
                    "entry_price": float(pos.get('avgEntryPrice', 0)),
                    "margin": float(pos.get('posMargin', 0)),
                    "pnl": float(pos.get('unrealisedPnl', 0)),
                    "currentQty": pos.get('currentQty', 0)
                })
            return result
        logger.error(f"Position check failed: {data.get('msg', 'Unknown error')}")
        return []
    except Exception as e:
        logger.error(f"Position check error: {str(e)}")
        return []

def get_eth_price():
    try:
        url = f"https://api-futures.kucoin.com/api/v1/ticker?symbol={SYMBOL}"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"Price response: {data}")
        if data.get('code') == '200000':
            price = float(data.get('data', {}).get('price', 0))
            return price
        logger.error(f"Failed to get price: {data.get('msg', 'Unknown error')}")
        return None
    except Exception as e:
        logger.error(f"Price fetch error: {str(e)}")
        return None

async def send_telegram_message(message):
    try:
        await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.info("Telegram notification sent")
    except TelegramError as e:
        logger.error(f"Telegram error: {str(e)}")

def get_funding_rate():
    try:
        url = f"https://api-futures.kucoin.com/api/v1/funding-rate/{SYMBOL}"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"Funding rate response: {data}")
        if data.get('code') == '200000':
            return float(data.get('data', {}).get('fundingRate', 0))
        logger.error(f"Failed to get funding rate: {data.get('msg', 'Unknown error')}")
        return None
    except Exception as e:
        logger.error(f"Funding rate error: {str(e)}")
        return None

def check_fills():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/fills?symbol={SYMBOL}"
        payload = f"GET/api/v1/fills?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Fills response: {data}")
        if data.get('code') == '200000':
            fills = data.get('data', {}).get('items', [])
            result = []
            for fill in fills[:3]:
                result.append({
                    "price": float(fill.get('price', 0)),
                    "reason": "TP" if fill.get('stop', '') == 'TP' else "Market" if fill.get('type', '') == 'market' else "Unknown"
                })
            return result
        logger.error(f"Failed to get fills: {data.get('msg', 'Unknown error')}")
        return []
    except Exception as e:
        logger.error(f"Fills check error: {str(e)}")
        return []

def get_cached_price():
    now = time.time()
    if now - current_price_cache['timestamp'] < 5:
        return current_price_cache['price']
    
    price = get_eth_price()
    if price:
        current_price_cache.update({'price': price, 'timestamp': now})
    return price

def round_to_tick_size(price: float, tick_size: float) -> float:
    return round(price / tick_size) * tick_size

def check_order_status(order_id: str) -> bool:
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/orders/{order_id}"
        payload = f"GET/api/v1/orders/{order_id}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Order status response: {data}")
        if data.get('code') == '200000':
            status = data.get('data', {}).get('status')
            if status == 'done':
                logger.info(f"Order {order_id} completed (filled).")
                return True
            elif status == 'canceled':
                logger.error(f"Order {order_id} canceled.")
                return False
            else:
                logger.info(f"Order {order_id} not yet completed, status: {status}")
                return False
        logger.error(f"Failed to get order status: {data.get('msg', 'Unknown error')}")
        return False
    except Exception as e:
        logger.error(f"Order status check error: {str(e)}")
        return False

async def verify_tp_order(order_id: str) -> bool:
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/st-orders?orderId={order_id}"
        payload = f"GET/api/v1/st-orders?orderId={order_id}"
        headers = signer.headers(payload)
        
        max_retries = 3
        for attempt in range(max_retries):
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            logger.info(f"TP verification response (attempt {attempt + 1}): {data}")
            
            if data.get('code') == '200000':
                items = data.get('data', {}).get('items', [])
                if not items:
                    logger.error(f"TP order not found: {order_id}")
                    return False
                order_data = items[0]
                if order_data.get('status') in ['new', 'active']:
                    logger.info(f"TP order verified: {order_id}, status: {order_data.get('status')}")
                    return True
                else:
                    logger.error(f"TP order invalid status: {order_id}, status: {order_data.get('status')}")
                    return False
            else:
                logger.error(f"TP verification error: {data.get('msg', 'Unknown error')}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
        
        logger.error(f"TP verification failed after {max_retries} attempts: {order_id}")
        return False
    except Exception as e:
        logger.error(f"TP verification general error: {str(e)}")
        return False

async def close_position_with_retry(position):
    try:
        side = position['side']
        size = abs(position.get('currentQty', 0))
        current_price = get_cached_price()
        if not current_price:
            logger.warning("Price not available, won't attempt to close.")
            return False

        # Close position
        close_order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": "sell" if side == "long" else "buy",
            "symbol": SYMBOL,
            "type": "market",
            "size": size,
            "reduceOnly": True,
            "marginMode": "ISOLATED"
        }
        
        max_retries = 3
        retry_delay = 2
        for attempt in range(max_retries):
            try:
                signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
                url = "https://api-futures.kucoin.com/api/v1/orders"
                payload = f"POST/api/v1/orders{json.dumps(close_order_data)}"
                headers = signer.headers(payload)
                response = requests.post(url, headers=headers, json=close_order_data, timeout=10)
                data = response.json()

                if data.get('code') == '200000':
                    close_order_id = data.get('data', {}).get('orderId')
                    logger.info(f"Position closed with 2% loss, Order ID: {close_order_id}")
                    
                    # Cancel open orders (v3/orders)
                    cancel_url = f"https://api-futures.kucoin.com/api/v3/orders?symbol={SYMBOL}"
                    cancel_payload = f"DELETE/api/v3/orders?symbol={SYMBOL}"
                    cancel_headers = signer.headers(cancel_payload)
                    cancel_response = requests.delete(cancel_url, headers=cancel_headers, timeout=10)
                    cancel_data = cancel_response.json()
                    if cancel_data.get('code') == '200000':
                        cancelled_ids = cancel_data.get('data', {}).get('cancelledOrderIds', [])
                        logger.info(f"Open orders canceled: {cancelled_ids}")
                    else:
                        logger.error(f"Failed to cancel open orders: {cancel_data.get('msg', 'Unknown error')}")
                    
                    await send_telegram_message(
                        f"🛑 Position Closed with 2% Loss!\n"
                        f"Symbol: {SYMBOL}\n"
                        f"Direction: {side.upper()}\n"
                        f"Entry: {position['entry_price']:.2f} USDT\n"
                        f"Exit: {current_price:.2f} USDT\n"
                        f"Size: {size} contracts\n"
                        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    )
                    return True
                else:
                    logger.error(f"Failed to close position (attempt {attempt + 1}): {data.get('msg', 'Unknown error')}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
            except Exception as e:
                logger.error(f"Position close error (attempt {attempt + 1}): {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
        
        logger.error(f"Failed to close position after {max_retries} attempts.")
        await send_telegram_message(f"❌ Failed to close position: Error after {max_retries} attempts.")
        return False
    except Exception as e:
        logger.error(f"Position close general error: {str(e)}")
        return False

async def open_position(signal, usdt_balance):
    try:
        # Funding rate (optional)
        funding_rate = get_funding_rate()
        if funding_rate is None:
            logger.warning("Failed to get funding rate, continuing.")
        
        # Balance check
        if usdt_balance is None or usdt_balance < MIN_BALANCE:
            logger.error(f"Insufficient USDT balance: {usdt_balance:.2f} USDT")
            return {"success": False, "error": "Insufficient balance"}
        
        # Contract details
        contract = get_contract_details()
        multiplier = contract.get('multiplier', 0.001)
        min_order_size = contract.get('min_order_size', 1)
        max_leverage = contract.get('max_leverage', 20)
        tick_size = contract.get('tick_size', 0.01)
        logger.info(f"Contract details: tick_size={tick_size}, multiplier={multiplier}, min_order_size={min_order_size}, max_leverage={max_leverage}")
        
        # Get price
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("Failed to get price, cannot open position.")
            return {"success": False, "error": "Failed to get price"}
        logger.info(f"Current Price: {eth_price:.2f} USDT, Symbol: {SYMBOL}")
        
        # Leverage calculation
        usdt_amount = usdt_balance
        leverage = str(LEVERAGE_MAX) if max_leverage >= LEVERAGE_MAX else str(max_leverage)
        total_value = usdt_amount * int(leverage)
        size = max(min_order_size, int(total_value / (eth_price * multiplier)))
        position_value = size * eth_price * multiplier
        required_margin = position_value / int(leverage)
        logger.info(f"{leverage}x Leverage: {size} contracts, Total Value: {position_value:.2f} USDT, Required Margin: {required_margin:.2f} USDT")
        
        if required_margin > usdt_balance:
            logger.warning(f"Insufficient balance for {leverage}x: Required {required_margin:.2f} USDT, available {usdt_balance:.2f} USDT")
            leverage = str(LEVERAGE_FALLBACK) if max_leverage >= LEVERAGE_FALLBACK else str(max_leverage)
            total_value = usdt_amount * int(leverage)
            size = max(min_order_size, int(total_value / (eth_price * multiplier) / 2))
            position_value = size * eth_price * multiplier
            required_margin = position_value / int(leverage)
            logger.info(f"{leverage}x Leverage: {size} contracts, Total Value: {position_value:.2f} USDT, Required Margin: {required_margin:.2f} USDT")
        
        if required_margin > usdt_balance:
            logger.error(f"Insufficient balance: Required {required_margin:.2f} USDT, available {usdt_balance:.2f} USDT")
            return {"success": False, "error": f"Insufficient balance: {required_margin:.2f} USDT required"}
        
        # Take-profit price
        take_profit_price = eth_price * (1 + TAKE_PROFIT_PCT) if signal == "buy" else eth_price * (1 - TAKE_PROFIT_PCT)
        take_profit_price = round_to_tick_size(take_profit_price, tick_size)
        logger.info(f"Take Profit Price: {take_profit_price:.2f} (tick_size={tick_size})")
        
        if take_profit_price <= 0:
            logger.error("Invalid take-profit price")
            return {"success": False, "error": "Invalid take-profit price"}
        
        # Open position order
        order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": signal,
            "symbol": SYMBOL,
            "leverage": leverage,
            "type": "market",
            "price": str(round(eth_price, 2)),
            "size": size,
            "marginMode": "ISOLATED"
        }
        
        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = f"POST/api/v1/orders{json.dumps(order_data)}"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers(payload)
        logger.info(f"Headers: {safe_headers(headers)}")
        logger.info(f"Order data: {order_data}")
        response = requests.post(url, headers=headers, json=order_data, timeout=10)
        data = response.json()
        logger.info(f"Open position response: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Failed to open position: {data.get('msg', 'Unknown error')}")
            return {"success": False, "error": data.get('msg', 'Unknown error')}
        
        order_id = data.get('data', {}).get('orderId')
        logger.info(f"Position open order sent! Order ID: {order_id}")

        # Wait for order to fill
        max_wait_time = 30
        check_interval = 2
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            if check_order_status(order_id):
                logger.info(f"Position opened, sending TP order.")
                break
            logger.info(f"Order {order_id} not yet filled, waiting...")
            time.sleep(check_interval)
        else:
            logger.error(f"Order {order_id} not filled within {max_wait_time}s.")
            await send_telegram_message(f"⚠️ Error: Position order {order_id} not filled within {max_wait_time}s.")
            return {"success": False, "error": f"Order not filled within {max_wait_time}s"}

        # Verify position
        try:
            positions = check_positions()
            if not positions:
                logger.error("Position not opened, cannot send TP order.")
                await send_telegram_message(f"⚠️ Error: Position not opened, TP order not sent.")
                return {"success": False, "error": "Position not opened"}
        except Exception as e:
            logger.error(f"Position check error: {str(e)}")
            await send_telegram_message(f"⚠️ Error: Position check error: {str(e)}")
            return {"success": False, "error": f"Position check error: {str(e)}"}

        # Take-profit order
        tp_order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": "sell" if signal == "buy" else "buy",
            "symbol": SYMBOL,
            "type": "limit",
            "size": size,
            "price": str(take_profit_price),
            "stopPrice": str(take_profit_price),
            "stopPriceType": "TP",
            "reduceOnly": True,
            "workingType": "Mark",
            "marginMode": "ISOLATED"
        }

        try:
            st_url = "https://api-futures.kucoin.com/api/v1/st-orders"
            st_payload = f"POST/api/v1/st-orders{json.dumps(tp_order_data)}"
            headers = signer.headers(st_payload)
            logger.info(f"TP request: {tp_order_data}")
            st_response = requests.post(st_url, headers=headers, json=tp_order_data, timeout=10)
            st_data = st_response.json()
            logger.info(f"TP order response: {st_data}")

            if st_data.get('code') == '200000':
                st_order_id = st_data.get('data', {}).get('orderId')
                await send_telegram_message(f"✅ TP successfully set: {take_profit_price:.2f}")
                logger.info(f"TP order successfully set, Order ID: {st_order_id}")
                # Telegram notification (position opened)
                await send_telegram_message(
                    f"📈 New Position Opened ({SYMBOL})\n"
                    f"Direction: {'Long' if signal == 'buy' else 'Short'}\n"
                    f"Entry Price: {eth_price:.2f} USDT\n"
                    f"Contracts: {size}\n"
                    f"Leverage: {leverage}x\n"
                    f"Position Value: {position_value:.2f} USDT\n"
                    f"Stop Loss: 2% loss check (in loop)\n"
                    f"Take Profit: {take_profit_price:.2f} USDT\n"
                    f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
                return {"success": True, "orderId": order_id, "size": size}
            else:
                logger.error(f"Failed to set TP: {st_data.get('msg', 'Unknown error')}")
                await send_telegram_message(f"⚠️ TP order failed: {st_data.get('msg', 'Unknown error')}")
                return {"success": False, "error": f"TP order failed: {st_data.get('msg', 'Unknown error')}"}
        except Exception as e:
            logger.error(f"TP send error: {str(e)}")
            await send_telegram_message(f"⚠️ TP order failed: {str(e)}")
            return {"success": False, "error": f"TP send error: {str(e)}"}
    
    except Exception as e:
        logger.error(f"Open position error: {str(e)}")
        await send_telegram_message(f"⚠️ Open position error: {str(e)}")
        return {"success": False, "error": str(e)}

async def manage_existing_position(position):
    try:
        current_price = get_cached_price()
        if not current_price:
            logger.warning("Price not available, skipping position management.")
            return

        entry_price = position['entry_price']
        side = position['side']
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if side == 'long' else ((entry_price - current_price) / entry_price * 100)
        
        if pnl_pct <= -2:
            logger.warning(f"2% loss detected! Closing {SYMBOL} {side} position.")
            await close_position_with_retry(position)

    except Exception as e:
        logger.error(f"Position management error: {str(e)}")
        await send_telegram_message(f"⚠️ Position management error: {str(e)}")

async def main():
    global last_position
    notification_cooldown = {
        'balance_warning': 0,
        'position_active': False
    }
    
    while True:
        try:
            # 1. Balance and Position Check
            usdt_balance, position_margin = check_usdm_balance()
            positions = check_positions()
            current_price = get_cached_price()
            
            # 2. Critical Condition Checks
            if usdt_balance < MIN_BALANCE:
                if not positions:
                    if time.time() - notification_cooldown['balance_warning'] > 3600:
                        await send_telegram_message(
                            f"⚠️ Insufficient Balance: {usdt_balance:.2f} USDT (Min: {MIN_BALANCE} USDT)\n"
                            f"⏳ Next check: 5 minutes later"
                        )
                        notification_cooldown['balance_warning'] = time.time()
                    await asyncio.sleep(300)
                    continue
                else:
                    logger.warning(f"Position open but low balance: {usdt_balance:.2f} USDT")

            # 2.2 Active Position Check
            if positions:
                if not notification_cooldown['position_active']:
                    pos = positions[0]
                    await send_telegram_message(
                        f"♻️ Open Position Detected:\n"
                        f"Direction: {pos['side'].upper()}\n"
                        f"Entry: {pos['entry_price']:.2f}\n"
                        f"Size: {abs(pos['currentQty'])} contracts\n"
                        f"Current Price: {current_price:.2f if current_price is not None else 'Unknown'}"
                    )
                    notification_cooldown['position_active'] = True
                
                await manage_existing_position(positions[0])
                
                # Position closure check
                if last_position and not positions:
                    fills = check_fills()
                    if fills:
                        logger.info(f"Close details: {fills}")
                        await send_telegram_message(
                            f"📉 Position Closed!\n"
                            f"Symbol: {SYMBOL}\n"
                            f"Direction: {last_position['side'].upper()}\n"
                            f"Entry: {last_position['entry_price']:.2f} USDT\n"
                            f"Exit: {fills[0]['price']:.2f} USDT\n"
                            f"Reason: {fills[0]['reason']}\n"
                            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        )
                    last_position = None
                
                await asyncio.sleep(60)
                continue
            else:
                if notification_cooldown['position_active']:
                    await send_telegram_message("✅ All positions closed")
                    notification_cooldown['position_active'] = False
                last_position = None

            # 3. Normal Trading Flow
            indicators = calculate_indicators()
            if not indicators:
                await asyncio.sleep(60)
                continue

            deepsearch_result = run_deepsearch()
            signal = get_grok_signal(indicators, deepsearch_result)
            
            if signal != "wait":
                logger.info(f"New signal received: {signal.upper()}")
                await open_position(signal, usdt_balance)
            
            await asyncio.sleep(60)

        except requests.exceptions.RequestException as e:
            logger.error(f"API connection error: {str(e)}")
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
