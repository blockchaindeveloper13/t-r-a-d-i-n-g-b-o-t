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

# Loglama ayarları (Heroku için konsol loglaması)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Config vars
load_dotenv()
GROK_API_KEY = os.getenv('GROK_API_KEY')
KUCOIN_API_KEY = os.getenv('KUCOIN_API_KEY')
KUCOIN_API_SECRET = os.getenv('KUCOIN_API_SECRET')
KUCOIN_API_PASSPHRASE = os.getenv('KUCOIN_API_PASSPHRASE')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Telegram bot
telegram_bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

# Sabit ayarlar
SYMBOL = "ETHUSDTM"
TAKE_PROFIT_PCT = 0.001  # %0.1
DEEPSEARCH_INTERVAL = 4 * 3600  # 4 saat
DEEPSEARCH_PER_DAY = 6
MIN_BALANCE = 5  # Minimum 5 USDT
LEVERAGE_MAX = 10  # Maksimum 10x
LEVERAGE_FALLBACK = 5  # Yetersiz bakiye için 5x

# Global değişkenler
last_deepsearch_result = None
last_deepsearch_time = 0
current_price_cache = {'price': None, 'timestamp': 0}
last_position = None  # Son pozisyonu takip et

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
        headers = {
            "KC-API-KEY": self.api_key,
            "KC-API-PASSPHRASE": self.api_passphrase,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-SIGN": signature,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json"
        }
        return headers

# Güvenli loglama için headers maskeleme
def safe_headers(headers):
    safe = headers.copy()
    safe["KC-API-SIGN"] = "****"
    safe["KC-API-PASSPHRASE"] = "****"
    return safe

# K-line verileri
def get_klines(granularity=60, limit=200):
    try:
        url = f"https://api-futures.kucoin.com/api/v1/kline/query?symbol={SYMBOL}&granularity={granularity}&limit={limit}"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"K-line yanıtı: {data}")
        if data.get('code') == '200000':
            klines = data.get('data', [])
            if not klines:
                logger.warning(f"{granularity} için veri yok")
                return None
            df = pd.DataFrame(klines, columns=["time", "open", "high", "low", "close", "volume"])
            df["close"] = df["close"].astype(float)
            return df
        logger.error(f"K-line alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return None
    except Exception as e:
        logger.error(f"K-line hatası: {str(e)}")
        return None

# Teknik indikatörler
def calculate_indicators():
    try:
        indicators = {}
        timeframes = {60: "1h", 240: "4h", 1440: "1d", 10080: "1w"}
        for granularity, tf_name in timeframes.items():
            df = get_klines(granularity, 200)
            if df is None or len(df) < 200:
                logger.warning(f"{tf_name} için yeterli veri yok")
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
        logger.info(f"İndikatörler: {indicators}")
        return indicators
    except Exception as e:
        logger.error(f"İndikatör hesaplama hatası: {str(e)}")
        return None

# Grok sinyal
def get_grok_signal(indicators, deepsearch_result):
    try:
        if not indicators or not deepsearch_result:
            logger.warning(f"Grok sinyal: Veri eksik, indicators: {indicators}, deepsearch_result: {deepsearch_result}")
            return "bekle"
        
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
        
        logger.info(f"Grok sinyal puanı: {score}")
        if score >= 0.3:
            return "buy"
        elif score <= -0.3:
            return "sell"
        return "bekle"
    except Exception as e:
        logger.error(f"Grok sinyal hatası: {str(e)}")
        return "bekle"

# DeepSearch simülasyon
def run_deepsearch():
    global last_deepsearch_result, last_deepsearch_time
    try:
        if time.time() - last_deepsearch_time < DEEPSEARCH_INTERVAL:
            logger.info("DeepSearch: Son sonucu kullanıyor")
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
            logger.info("DeepSearch: Kripto haberi bulunamadı, Neutral dönüyor")
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
            logger.info(f"DeepSearch: Regülasyon/Spekülasyon bağlamları: {reg_spec_contexts}")
        
        last_deepsearch_result = {"sentiment": sentiment, "timestamp": time.time()}
        last_deepsearch_time = time.time()
        return last_deepsearch_result
    
    except Exception as e:
        logger.error(f"DeepSearch hatası: {str(e)}")
        return last_deepsearch_result if last_deepsearch_result else {"sentiment": "Neutral", "timestamp": time.time()}

# Bakiye kontrol
def check_usdm_balance():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/account-overview?currency=USDT"
        payload = "GET/api/v1/account-overview?currency=USDT"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Bakiye yanıtı: {data}")
        if data.get('code') == '200000':
            usdt_balance = float(data.get('data', {}).get('availableBalance', 0))
            position_margin = float(data.get('data', {}).get('positionMargin', 0))
            return usdt_balance, position_margin
        logger.error(f"USD-M bakiye kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
        return 0, 0
    except Exception as e:
        logger.error(f"Bakiye hatası: {str(e)}")
        return 0, 0

# Kontrat detayları
def get_contract_details():
    try:
        url = "https://api-futures.kucoin.com/api/v1/contracts/active"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"Kontrat yanıtı: {data}")
        if data.get('code') == '200000':
            for contract in data.get('data', []):
                if contract.get('symbol') == SYMBOL:
                    return {
                        "multiplier": float(contract.get('multiplier', 0.001)),
                        "min_order_size": int(contract.get('minOrderQty', 1)),
                        "max_leverage": int(contract.get('maxLeverage', 20)),
                        "tick_size": float(contract.get('tickSize', 0.01))
                    }
            logger.warning(f"{SYMBOL} kontratı bulunamadı")
            return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20, "tick_size": 0.01}
        logger.error(f"Kontrat detayları alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20, "tick_size": 0.01}
    except Exception as e:
        logger.error(f"Kontrat detayları hatası: {str(e)}")
        return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20, "tick_size": 0.01}

# Pozisyon kontrol
def check_positions():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/positions?symbol={SYMBOL}"
        payload = f"GET/api/v1/positions?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Pozisyon yanıtı: {data}")
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
        logger.error(f"Pozisyon kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
        return []
    except Exception as e:
        logger.error(f"Pozisyon kontrol hatası: {str(e)}")
        return []

# ETH fiyatı
def get_eth_price():
    try:
        url = f"https://api-futures.kucoin.com/api/v1/ticker?symbol={SYMBOL}"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"Fiyat yanıtı: {data}")
        if data.get('code') == '200000':
            price = float(data.get('data', {}).get('price', 0))
            return price
        logger.error(f"Fiyat alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return None
    except Exception as e:
        logger.error(f"Fiyat alma hatası: {str(e)}")
        return None

# Telegram bildirimi
async def send_telegram_message(message):
    try:
        await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.info("Telegram bildirimi gönderildi")
    except TelegramError as e:
        logger.error(f"Telegram hatası: {str(e)}")

# Fonlama oranı
def get_funding_rate():
    try:
        url = f"https://api-futures.kucoin.com/api/v1/funding-rate/{SYMBOL}"
        response = requests.get(url, timeout=10)
        data = response.json()
        logger.info(f"Fonlama oranı yanıtı: {data}")
        if data.get('code') == '200000':
            return float(data.get('data', {}).get('fundingRate', 0))
        logger.error(f"Fonlama oranı alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return None
    except Exception as e:
        logger.error(f"Fonlama oranı hatası: {str(e)}")
        return None

# Kapanış detayları
def check_fills():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/fills?symbol={SYMBOL}"
        payload = f"GET/api/v1/fills?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Fills yanıtı: {data}")
        if data.get('code') == '200000':
            fills = data.get('data', {}).get('items', [])
            result = []
            for fill in fills[:3]:
                result.append({
                    "price": float(fill.get('price', 0)),
                    "reason": "TP" if fill.get('stop', '') == 'TP' else "Market" if fill.get('type', '') == 'market' else "Bilinmiyor"
                })
            return result
        logger.error(f"Fills alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return []
    except Exception as e:
        logger.error(f"Fills kontrol hatası: {str(e)}")
        return []

# Fiyat önbellekleme
def get_cached_price():
    now = time.time()
    if now - current_price_cache['timestamp'] < 5:
        return current_price_cache['price']
    
    price = get_eth_price()
    if price:
        current_price_cache.update({'price': price, 'timestamp': now})
    return price

# Fiyat yuvarlama
def round_to_tick_size(price: float, tick_size: float) -> float:
    return round(price / tick_size) * tick_size

# Emir durumu kontrol
def check_order_status(order_id: str) -> bool:
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/orders/{order_id}"
        payload = f"GET/api/v1/orders/{order_id}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        logger.info(f"Emir durumu yanıtı: {data}")
        if data.get('code') == '200000':
            status = data.get('data', {}).get('status')
            if status == 'done':
                logger.info(f"Emir {order_id} tamamlandı (filled).")
                return True
            elif status == 'canceled':
                logger.error(f"Emir {order_id} iptal edildi.")
                return False
            else:
                logger.info(f"Emir {order_id} henüz tamamlanmadı, durum: {status}")
                return False
        logger.error(f"Emir durumu alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return False
    except Exception as e:
        logger.error(f"Emir durumu kontrol hatası: {str(e)}")
        return False

# TP doğrulama
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
            logger.info(f"TP doğrulama yanıtı (deneme {attempt + 1}): {data}")
            
            if data.get('code') == '200000':
                items = data.get('data', {}).get('items', [])
                if not items:
                    logger.error(f"TP emri bulunamadı: {order_id}")
                    return False
                order_data = items[0]
                if order_data.get('status') in ['new', 'active']:
                    logger.info(f"TP emri doğrulandı: {order_id}, durum: {order_data.get('status')}")
                    return True
                else:
                    logger.error(f"TP emri geçersiz durum: {order_id}, durum: {order_data.get('status')}")
                    return False
            else:
                logger.error(f"TP doğrulama hatası: {data.get('msg', 'Bilinmeyen hata')}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
        
        logger.error(f"TP doğrulama {max_retries} denemede başarısız: {order_id}")
        return False
    except Exception as e:
        logger.error(f"TP doğrulama genel hatası: {str(e)}")
        return False

# Pozisyon kapatma (yeniden deneme ile)
async def close_position_with_retry(position):
    try:
        side = position['side']
        size = abs(position.get('currentQty', 0))
        current_price = get_cached_price()
        if not current_price:
            logger.warning("Fiyat alınamadı, kapatma denenmeyecek.")
            return False

        # Pozisyon kapatma
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
                    logger.info(f"Pozisyon %2 zararla kapatıldı, Order ID: {close_order_id}")
                    
                    # Açık emirleri iptal et (v3/orders)
                    cancel_url = f"https://api-futures.kucoin.com/api/v3/orders?symbol={SYMBOL}"
                    cancel_payload = f"DELETE/api/v3/orders?symbol={SYMBOL}"
                    cancel_headers = signer.headers(cancel_payload)
                    cancel_response = requests.delete(cancel_url, headers=cancel_headers, timeout=10)
                    cancel_data = cancel_response.json()
                    if cancel_data.get('code') == '200000':
                        cancelled_ids = cancel_data.get('data', {}).get('cancelledOrderIds', [])
                        logger.info(f"Açık emir iptali: {cancelled_ids}")
                    else:
                        logger.error(f"Açık emir iptali başarısız: {cancel_data.get('msg', 'Bilinmeyen hata')}")
                    
                    await send_telegram_message(
                        f"🛑 Pozisyon %2 Zararla Kapatıldı!\n"
                        f"Sembol: {SYMBOL}\n"
                        f"Yön: {side.upper()}\n"
                        f"Giriş: {position['entry_price']:.2f} USDT\n"
                        f"Kapanış: {current_price:.2f} USDT\n"
                        f"Büyüklük: {size} kontrat\n"
                        f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    )
                    return True
                else:
                    logger.error(f"Pozisyon kapatma başarısız (deneme {attempt + 1}): {data.get('msg', 'Bilinmeyen hata')}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
            except Exception as e:
                logger.error(f"Pozisyon kapatma hatası (deneme {attempt + 1}): {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
        
        logger.error(f"Pozisyon kapatma {max_retries} denemede başarısız.")
        await send_telegram_message(f"❌ Pozisyon kapatma başarısız: {max_retries} deneme sonrası hata.")
        return False
    except Exception as e:
        logger.error(f"Pozisyon kapatma genel hatası: {str(e)}")
        return False

# Pozisyon açma
async def open_position(signal, usdt_balance):
    try:
        # Fonlama oranı (opsiyonel)
        funding_rate = get_funding_rate()
        if funding_rate is None:
            logger.warning("Fonlama oranı alınamadı, devam ediliyor.")
        
        # Bakiye kontrolü
        if usdt_balance is None or usdt_balance < MIN_BALANCE:
            logger.error(f"Yetersiz USDT bakiyesi: {usdt_balance:.2f} USDT")
            return {"success": False, "error": "Yetersiz bakiye"}
        
        # Kontrat detayları
        contract = get_contract_details()
        multiplier = contract.get('multiplier', 0.001)
        min_order_size = contract.get('min_order_size', 1)
        max_leverage = contract.get('max_leverage', 20)
        tick_size = contract.get('tick_size', 0.01)
        logger.info(f"Kontrat detayları: tick_size={tick_size}, multiplier={multiplier}, min_order_size={min_order_size}, max_leverage={max_leverage}")
        
        # Fiyat al
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("Fiyat alınamadı, pozisyon açılamıyor.")
            return {"success": False, "error": "Fiyat alınamadı"}
        logger.info(f"Alınan Fiyat: {eth_price:.2f} USDT, Symbol: {SYMBOL}")
        
        # Kaldıraç hesaplama
        usdt_amount = usdt_balance
        leverage = str(LEVERAGE_MAX) if max_leverage >= LEVERAGE_MAX else str(max_leverage)
        total_value = usdt_amount * int(leverage)
        size = max(min_order_size, int(total_value / (eth_price * multiplier)))
        position_value = size * eth_price * multiplier
        required_margin = position_value / int(leverage)
        logger.info(f"{leverage}x Kaldıraç: {size} kontrat, Toplam Değer: {position_value:.2f} USDT, Gerekli Margin: {required_margin:.2f} USDT")
        
        if required_margin > usdt_balance:
            logger.warning(f"{leverage}x için yetersiz bakiye: Gerekli {required_margin:.2f} USDT, mevcut {usdt_balance:.2f} USDT")
            leverage = str(LEVERAGE_FALLBACK) if max_leverage >= LEVERAGE_FALLBACK else str(max_leverage)
            total_value = usdt_amount * int(leverage)
            size = max(min_order_size, int(total_value / (eth_price * multiplier) / 2))
            position_value = size * eth_price * multiplier
            required_margin = position_value / int(leverage)
            logger.info(f"{leverage}x Kaldıraç: {size} kontrat, Toplam Değer: {position_value:.2f} USDT, Gerekli Margin: {required_margin:.2f} USDT")
        
        if required_margin > usdt_balance:
            logger.error(f"Yetersiz bakiye: Gerekli {required_margin:.2f} USDT, mevcut {usdt_balance:.2f} USDT")
            return {"success": False, "error": f"Yetersiz bakiye: {required_margin:.2f} USDT gerekli"}
        
        # Take-profit fiyatı
        take_profit_price = eth_price * (1 + TAKE_PROFIT_PCT) if signal == "buy" else eth_price * (1 - TAKE_PROFIT_PCT)
        take_profit_price = round_to_tick_size(take_profit_price, tick_size)
        logger.info(f"Take Profit Fiyatı: {take_profit_price:.2f} (tick_size={tick_size})")
        
        if take_profit_price <= 0:
            logger.error("Geçersiz take-profit fiyatı")
            return {"success": False, "error": "Geçersiz take-profit fiyatı"}
        
        # Pozisyon açma siparişi
        order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": signal,
            "symbol": SYMBOL,
            "leverage": leverage,
            "type": "limit",
            "price": str(round(eth_price, 2)),
            "size": size,
            "marginMode": "ISOLATED"
        }
        
        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = f"POST/api/v1/orders{json.dumps(order_data)}"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers(payload)
        logger.info(f"Headers: {safe_headers(headers)}")
        logger.info(f"Sipariş verisi: {order_data}")
        response = requests.post(url, headers=headers, json=order_data, timeout=10)
        data = response.json()
        logger.info(f"Pozisyon açma yanıtı: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Pozisyon açılamadı: {data.get('msg', 'Bilinmeyen hata')}")
            return {"success": False, "error": data.get('msg', 'Bilinmeyen hata')}
        
        order_id = data.get('data', {}).get('orderId')
        logger.info(f"Pozisyon açma emri gönderildi! Sipariş ID: {order_id}")

        # Emirin fill olmasını bekle
        max_wait_time = 30
        check_interval = 2
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            if check_order_status(order_id):
                logger.info(f"Pozisyon açıldı, TP emri gönderiliyor.")
                break
            logger.info(f"Emir {order_id} henüz fill olmadı, bekleniyor...")
            time.sleep(check_interval)
        else:
            logger.error(f"Emir {order_id} {max_wait_time}s içinde fill olmadı.")
            await send_telegram_message(f"⚠️ Hata: Pozisyon emri {order_id} {max_wait_time}s içinde fill olmadı.")
            return {"success": False, "error": f"Emir {max_wait_time}s içinde fill olmadı"}

        # Pozisyon doğrulama
        positions = check_positions()
        if not positions:
            logger.error("Pozisyon açılmadı, TP emri gönderilemiyor.")
            await send_telegram_message(f"⚠️ Hata: Pozisyon açılmadı, TP emri gönderilemedi.")
            return {"success": False, "error": "Pozisyon açılmadı"}
            except Exception as e:
    logger.error(f"Pozisyon kontrol hatası: {str(e)}")
    return {"success": False, "error": f"Pozisyon kontrol hatası: {str(e)}"}

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
    logger.info(f"TP isteği: {tp_order_data}")
    st_response = requests.post(st_url, headers=headers, json=tp_order_data, timeout=10)
    st_data = st_response.json()
    logger.info(f"TP sipariş yanıtı: {st_data}")

    if st_data.get('code') == '200000':
        st_order_id = st_data.get('data', {}).get('orderId')
        await send_telegram_message(f"✅ TP başarıyla ayarlandı: {take_profit_price:.2f}")
        logger.info(f"TP emri başarıyla ayarlandı, Order ID: {st_order_id}")
        # Telegram bildirimi (pozisyon açılma)
        await send_telegram_message(
            f"📈 Yeni Pozisyon Açıldı ({SYMBOL})\n"
            f"Yön: {'Long' if signal == 'buy' else 'Short'}\n"
            f"Giriş Fiyatı: {eth_price:.2f} USDT\n"
            f"Kontrat: {size}\n"
            f"Kaldıraç: {leverage}x\n"
            f"Pozisyon Değeri: {position_value:.2f} USDT\n"
            f"Stop Loss: %2 zarar kontrolü (döngüde)\n"
            f"Take Profit: {take_profit_price:.2f} USDT\n"
            f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        return {"success": True, "orderId": order_id, "size": size}
    else:
        logger.error(f"TP ayarlanamadı: {st_data.get('msg', 'Bilinmeyen hata')}")
        await send_telegram_message(f"⚠️ TP emri başarısız: {st_data.get('msg', 'Bilinmeyen hata')}")
        return {"success": False, "error": f"TP emri başarısız: {st_data.get('msg', 'Bilinmeyen hata')}"}
except Exception as e:
    logger.error(f"TP gönderme hatası: {str(e)}")
    await send_telegram_message(f"⚠️ TP emri başarısız: {str(e)}")
    return {"success": False, "error": f"TP gönderme hatası: {str(e)}"}
        
        # Telegram bildirimi
       await send_telegram_message(
    f"📈 Yeni Pozisyon Açıldı ({SYMBOL})\n"
    f"Yön: {'Long' if signal == 'buy' else 'Short'}\n"
    f"Giriş Fiyatı: {eth_price:.2f} USDT\n"
    f"Kontrat: {size}\n"
    f"Kaldıraç: {leverage}x\n"
    f"Pozisyon Değeri: {position_value:.2f} USDT\n"
    f"Stop Loss: %2 zarar kontrolü (döngüde)\n"
    f"Take Profit: {take_profit_price:.2f} USDT\n"
    f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
)
return {"success": True, "orderId": order_id, "size": size}
    
    except Exception as e:
        logger.error(f"Pozisyon açma hatası: {str(e)}")
        await send_telegram_message(f"⚠️ Pozisyon açma hatası: {str(e)}")
        return {"success": False, "error": str(e)}

# Mevcut pozisyon yönetimi
async def manage_existing_position(position):
    try:
        current_price = get_cached_price()
        if not current_price:
            logger.warning("Fiyat alınamadı, pozisyon yönetimi atlanıyor.")
            return

        entry_price = position['entry_price']
        side = position['side']
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if side == 'long' else ((entry_price - current_price) / entry_price * 100)
        
        if pnl_pct <= -2:
            logger.warning(f"%2 zarar tespit edildi! {SYMBOL} {side} pozisyonu kapatılıyor.")
            await close_position_with_retry(position)

    except Exception as e:
        logger.error(f"Pozisyon yönetim hatası: {str(e)}")
        await send_telegram_message(f"⚠️ Pozisyon yönetim hatası: {str(e)}")

# Ana döngü
async def main():
    global last_position
    notification_cooldown = {
        'balance_warning': 0,
        'position_active': False
    }
    
    while True:
        try:
            # 1. Bakiye ve Pozisyon Kontrolü
            usdt_balance, position_margin = check_usdm_balance()
            positions = check_positions()
            current_price = get_cached_price()
            
            # 2. Kritik Durum Kontrolleri
            if usdt_balance < MIN_BALANCE:
                if not positions:
                    if time.time() - notification_cooldown['balance_warning'] > 3600:
                        await send_telegram_message(
                            f"⚠️ Yetersiz Bakiye: {usdt_balance:.2f} USDT (Min: {MIN_BALANCE} USDT)\n"
                            f"⏳ Sonraki kontrol: 5 dakika sonra"
                        )
                        notification_cooldown['balance_warning'] = time.time()
                    await asyncio.sleep(300)
                    continue
                else:
                    logger.warning(f"Pozisyon açık ama bakiye düşük: {usdt_balance:.2f} USDT")

            # 2.2 Aktif Pozisyon Kontrolü
            if positions:
                if not notification_cooldown['position_active']:
                    pos = positions[0]
                   await send_telegram_message(
    f"♻️ Açık Pozisyon Tespit Edildi:\n"
    f"Yön: {pos['side'].upper()}\n"
    f"Giriş: {pos['entry_price']:.2f}\n"
    f"Miktar: {abs(pos['currentQty'])} kontrat\n"
    f"Mevcut Fiyat: {current_price:.2f if current_price is not None else 'Bilinmiyor'}"
)
                    notification_cooldown['position_active'] = True
                
                await manage_existing_position(positions[0])
                
                # Pozisyon kapanış kontrolü
                if last_position and not positions:
                    fills = check_fills()
                    if fills:
                        logger.info(f"Kapanış detayları: {fills}")
                        await send_telegram_message(
                            f"📉 Pozisyon Kapatıldı!\n"
                            f"Sembol: {SYMBOL}\n"
                            f"Yön: {last_position['side'].upper()}\n"
                            f"Giriş: {last_position['entry_price']:.2f} USDT\n"
                            f"Kapanış: {fills[0]['price']:.2f} USDT\n"
                            f"Neden: {fills[0]['reason']}\n"
                            f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        )
                    last_position = None
                
                await asyncio.sleep(60)
                continue
            else:
                if notification_cooldown['position_active']:
                    await send_telegram_message("✅ Tüm pozisyonlar kapandı")
                    notification_cooldown['position_active'] = False
                last_position = None

            # 3. Normal İşlem Akışı
            indicators = calculate_indicators()
            if not indicators:
                await asyncio.sleep(60)
                continue

            deepsearch_result = run_deepsearch()
            signal = get_grok_signal(indicators, deepsearch_result)
            
            if signal != "bekle":
                logger.info(f"Yeni sinyal alındı: {signal.upper()}")
                await open_position(signal, usdt_balance)
            
            await asyncio.sleep(60)

        except requests.exceptions.RequestException as e:
            logger.error(f"API bağlantı hatası: {str(e)}")
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Beklenmeyen hata: {str(e)}")
            await send_telegram_message(f"⛔ KRİTİK HATA: {str(e)[:200]}...")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
