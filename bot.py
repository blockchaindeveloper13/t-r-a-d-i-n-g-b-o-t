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

# Loglama ayarları
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
SYMBOL = "ETHUSDTM"  # BTCUSDTM olabilir, panelde kontrol et
STOP_LOSS_PCT = 0.01  # %1
TAKE_PROFIT_PCT = 0.01  # %1
DEEPSEARCH_INTERVAL = 4 * 3600  # 4 saat
DEEPSEARCH_PER_DAY = 6

# DeepSearch sonuçlarını saklamak
last_deepsearch_result = None
last_deepsearch_time = 0

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

# K-line verileri
def get_klines(granularity=60, limit=200):
    try:
        url = f"https://api-futures.kucoin.com/api/v1/kline/query?symbol={SYMBOL}&granularity={granularity}&limit={limit}"
        response = requests.get(url)
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
        logger.error(f"Grok sinyal hatası: {str(e)}, indicators: {indicators}, deepsearch_result: {deepsearch_result}")
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
        payload = "GET" + "/api/v1/account-overview?currency=USDT"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers)
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
        response = requests.get(url)
        data = response.json()
        logger.info(f"Kontrat yanıtı: {data}")
        if data.get('code') == '200000':
            for contract in data.get('data', []):
                if contract.get('symbol') == SYMBOL:
                    return {
                        "multiplier": float(contract.get('multiplier', 0.001)),
                        "min_order_size": int(contract.get('minOrderQty', 1)),
                        "max_leverage": int(contract.get('maxLeverage', 20))
                    }
            logger.warning(f"{SYMBOL} kontratı bulunamadı")
            return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20}
        logger.error(f"Kontrat detayları alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20}
    except Exception as e:
        logger.error(f"Kontrat detayları hatası: {str(e)}")
        return {"multiplier": 0.001, "min_order_size": 1, "max_leverage": 20}

# Pozisyon kontrol
def check_positions():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/positions?symbol={SYMBOL}"
        payload = f"GET/api/v1/positions?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Pozisyon yanıtı: {data}")
        if data.get('code') == '200000':
            positions = data.get('data', [])
            if positions:
                pos = positions[0]
                return {
                    "exists": True,
                    "side": "long" if pos.get('currentQty', 0) > 0 else "short",
                    "entry_price": float(pos.get('avgEntryPrice', 0)),
                    "margin": float(pos.get('posMargin', 0)),
                    "pnl": float(pos.get('unrealisedPnl', 0))
                }
            return {"exists": False}
        logger.error(f"Pozisyon kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
        return {"exists": False}
    except Exception as e:
        logger.error(f"Pozisyon kontrol hatası: {str(e)}")
        return {"exists": False}

# ETH fiyatı
def get_eth_price():
    try:
        url = f"https://api-futures.kucoin.com/api/v1/ticker?symbol={SYMBOL}"
        response = requests.get(url)
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
        response = requests.get(url)
        data = response.json()
        logger.info(f"Fonlama oranı yanıtı: {data}")
        if data.get('code') == '200000':
            return float(data.get('data', {}).get('fundingRate', 0))
        logger.error(f"Fonlama oranı alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return None
    except Exception as e:
        logger.error(f"Fonlama oranı hatası: {str(e)}")
        return None

# Pozisyon açma
# Yardımcı fonksiyon: Aktif stop emirlerini kontrol et
def check_stop_orders():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/stop-orders?symbol={SYMBOL}"
        payload = f"GET/api/v1/stop-orders?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Stop emirleri yanıtı: {data}")
        if data.get('code') == '200000':
            return data.get('data', [])
        logger.error(f"Stop emirleri alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return []
    except Exception as e:
        logger.error(f"Stop emirleri kontrol hatası: {str(e)}")
        return []
def round_to_tick_size(price: float, tick_size: float) -> float:
    """Fiyatı kontratın tick boyutuna göre yuvarlar."""
    return round(price / tick_size) * tick_size
# Pozisyon açma (Güncellenmiş)
import time

# Yardımcı fonksiyon: Emir durumunu kontrol et
def check_order_status(order_id: str) -> bool:
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/orders/{order_id}"
        payload = f"GET/api/v1/orders/{order_id}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers)
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

# Pozisyon açma (Güncellenmiş)
import asyncio
import time
from datetime import datetime, timedelta

# Fiyat önbellekleme
current_price_cache = {'price': None, 'timestamp': 0}

def get_cached_price():
    now = time.time()
    if now - current_price_cache['timestamp'] < 5:  # 5 saniyelik cache
        return current_price_cache['price']
    
    price = get_eth_price()
    if price:
        current_price_cache.update({'price': price, 'timestamp': now})
    return price

# Pozisyon açma (Güncellenmiş)
async def open_position(signal, usdt_balance):
    try:
        # Fonlama oranı (opsiyonel)
        funding_rate = get_funding_rate()
        if not funding_rate:
            logger.warning("Fonlama oranı alınamadı, devam ediliyor.")
        
        # Bakiye kontrolü
        if usdt_balance is None or usdt_balance < 5:  # Minimum 5 USDT
            logger.error("Yetersiz USDT bakiyesi veya bakiye alınamadı.")
            return {"success": False, "error": "Yetersiz bakiye"}
        
        # Kontrat detayları (tickSize'ı da al)
        contract = get_contract_details()
        if not contract:
            logger.warning("Kontrat detayları alınamadı, varsayılan değerler kullanılıyor.")
            multiplier = 0.001
            min_order_size = 1
            max_leverage = 20
            tick_size = 0.01  # Varsayılan
        else:
            multiplier = float(contract.get('multiplier', 0.001))
            min_order_size = int(contract.get('minOrderQty', 1))
            max_leverage = int(contract.get('maxLeverage', 20))
            tick_size = float(contract.get('tickSize', 0.01))  # tickSize'ı al
            logger.info(f"Kontrat detayları: tickSize={tick_size}, multiplier={multiplier}, min_order_size={min_order_size}, max_leverage={max_leverage}")
        
        # Fiyat al
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("Fiyat alınamadı, pozisyon açılamıyor.")
            return {"success": False, "error": "Fiyat alınamadı"}
        logger.info(f"Alınan Fiyat: {eth_price:.2f} USDT, Symbol: {SYMBOL}")
        
        # 10x kaldıraç denemesi
        usdt_amount = usdt_balance  # Maksimum bakiyeyi kullan
        leverage = "10" if max_leverage >= 10 else str(max_leverage)  # String
        total_value = usdt_amount * int(leverage)
        size = max(min_order_size, int(total_value / (eth_price * multiplier)))
        position_value = size * eth_price * multiplier
        required_margin = position_value / int(leverage)
        logger.info(f"10x Kaldıraç: {size} kontrat (Toplam Değer: {position_value:.2f} USDT, Gerekli Margin: {required_margin:.2f} USDT, Fiyat: {eth_price:.2f} USDT)")
        
        if required_margin > usdt_balance:
            logger.warning(f"10x kaldıraç için yetersiz bakiye: Gerekli margin {required_margin:.2f} USDT, mevcut {usdt_balance:.2f} USDT")
            # 5x kaldıraçla daha küçük pozisyon
            leverage = "5" if max_leverage >= 5 else str(max_leverage)
            total_value = usdt_amount * int(leverage)
            size = max(min_order_size, int(total_value / (eth_price * multiplier) / 2))
            position_value = size * eth_price * multiplier
            required_margin = position_value / int(leverage)
            logger.info(f"5x Kaldıraç: {size} kontrat (Toplam Değer: {position_value:.2f} USDT, Gerekli Margin: {required_margin:.2f} USDT)")
        
        if required_margin > usdt_balance:
            logger.error(f"Yetersiz bakiye: Gerekli margin {required_margin:.2f} USDT, mevcut {usdt_balance:.2f} USDT")
            return {"success": False, "error": f"Yetersiz bakiye: {required_margin:.2f} USDT gerekli"}
        
     # Take-profit fiyatını hesapla ve tickSize'a yuvarla
take_profit_price = eth_price * (1 + TAKE_PROFIT_PCT) if signal == "buy" else eth_price * (1 - TAKE_PROFIT_PCT)
take_profit_price = round_to_tick_size(take_profit_price, tick_size)
logger.info(f"Take Profit Fiyatı: {take_profit_price:.2f} (tickSize={tick_size})")
        
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
        
        # KuCoin API isteği (pozisyon açma)
        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = f"POST/api/v1/orders{json.dumps(order_data)}"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers(payload)
        logger.info(f"Headers: {headers}")
        logger.info(f"Sipariş verisi: {order_data}")
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()
        logger.info(f"Pozisyon açma yanıtı: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Pozisyon açılamadı: {data.get('msg', 'Bilinmeyen hata')}")
            return {"success": False, "error": data.get('msg', 'Bilinmeyen hata')}
        
        order_id = data.get('data', {}).get('orderId')
        logger.info(f"Pozisyon açma emri gönderildi! Sipariş ID: {order_id}")

        # Emirin fill olmasını bekle (maksimum 30 saniye)
        max_wait_time = 30  # saniye
        check_interval = 2  # saniye
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            if check_order_status(order_id):
                logger.info(f"Pozisyon açıldı, TP/SL emirleri gönderiliyor.")
                break
            logger.info(f"Emir {order_id} henüz fill olmadı, bekleniyor...")
            time.sleep(check_interval)
        else:
            logger.error(f"Emir {order_id} {max_wait_time} saniye içinde fill olmadı, işlem iptal ediliyor.")
            await send_telegram_message(
                f"⚠️ Hata: Pozisyon emri {order_id} {max_wait_time}s içinde fill olmadı, işlem iptal edildi."
            )
            return {"success": False, "error": f"Emir {max_wait_time}s içinde fill olmadı"}

        # Pozisyon açıldığını doğrula
        position = check_positions()
        if not position.get("exists"):
            logger.error("Pozisyon açılmadı, TP/SL emirleri gönderilemiyor.")
            await send_telegram_message(
                f"⚠️ Hata: Pozisyon açılmadı, TP/SL emirleri gönderilemedi."
            )
            return {"success": False, "error": "Pozisyon açılmadı"}

        # TAKE-PROFIT için AYRI AYRI siparişler oluştur
        tp_order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": "sell" if signal == "buy" else "buy",
            "symbol": SYMBOL,
            "type": "market",
            "size": size,
            "stopPrice": str(take_profit_price),
            "stopPriceType": "TP",  # Take Profit
            "reduceOnly": True,  # Sadece pozisyonu kapat
            "workingType": "Mark",  # Mark price baz al
            "marginMode": "ISOLATED"
        }
        

       # TP emri gönder (yeniden deneme ile)
max_retries = 5
retry_delay = 2
success = False
for attempt in range(max_retries):
    try:
        st_url = "https://api-futures.kucoin.com/api/v1/st-orders"
        st_payload = f"POST/api/v1/st-orders{json.dumps(tp_order_data)}"
        headers = signer.headers(st_payload)
        logger.info(f"TP isteği gönderiliyor (deneme {attempt + 1}/{max_retries}): {tp_order_data}")
        st_response = requests.post(st_url, headers=headers, json=tp_order_data, timeout=10)
        st_data = st_response.json()
        logger.info(f"TP sipariş yanıtı: {st_data}")
        
        if st_data.get('code') == '200000':
            st_order_id = st_data.get('data', {}).get('orderId')
            logger.info(f"TP emri başarıyla ayarlandı, Order ID: {st_order_id}")
            success = True
            break
        else:
            logger.error(f"TP ayarlanamadı: {st_data.get('msg', 'Bilinmeyen hata')}")
            if attempt < max_retries - 1:
                logger.info(f"TP için tekrar deneme: {attempt + 1}/{max_retries}")
                time.sleep(retry_delay)
                continue
    except Exception as e:
        logger.error(f"TP gönderme hatası (deneme {attempt + 1}): {str(e)}")
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
            continue
if not success:
    logger.error(f"TP emri {max_retries} denemede başarısız oldu.")
    await send_telegram_message(
        f"⚠️ TP emri başarısız: {max_retries} deneme sonrası hata."
    )
       
        # Pozisyon durumunu tekrar kontrol et
        position = check_positions()
        if not position.get("exists"):
            logger.warning("Pozisyon kapanmış veya bulunamadı, işlem durumu kontrol edilmeli.")
            await send_telegram_message(
                f"⚠️ Uyarı: Pozisyon bulunamadı, kapanmış olabilir. Lütfen kontrol edin."
            )
       else:
    logger.info(f"Pozisyon başarıyla açıldı: {SYMBOL}, Giriş: {eth_price:.2f}")

        # Telegram bildirimi (başarılı pozisyon açılışı için)
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
        await send_telegram_message(
            f"⚠️ Pozisyon açma hatası: {str(e)}"
        )
        return {"success": False, "error": str(e)}


            
         position = check_positions()
if position["exists"]:
    logger.info(f"Açık pozisyon: {position['side']}, Giriş: {position['entry_price']}, PnL: {position['pnl']}")
    last_position = position
    
    # Pozisyon sonrası bakiye/margin logu
    usdt_balance, position_margin = check_usdm_balance()
    logger.info(f"Pozisyon sonrası bakiye: {usdt_balance:.2f} USDT, Margin: {position_margin:.2f} USDT")
    
    # %2 zarar kontrolü
    current_price = get_cached_price()
    if not current_price:
        logger.warning("Fiyat alınamadı, bir sonraki döngüde tekrar denenecek.")
        await asyncio.sleep(60)
        continue
    
    entry_price = position['entry_price']
    side = position['side']
    size = abs(position.get('currentQty', 0))  # Pozisyon büyüklüğü
    pnl_pct = ((current_price - entry_price) / entry_price * 100) if side == 'long' else ((entry_price - current_price) / entry_price * 100)
    
    if pnl_pct <= -2:  # %2 zarar
        logger.warning(f"%2 zarar tespit edildi! {SYMBOL} {side} pozisyonu kapatılıyor.")
        # Market emriyle pozisyonu kapat
        close_order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": "sell" if side == "long" else "buy",
            "symbol": SYMBOL,
            "type": "market",
            "size": size,
            "reduceOnly": True,
            "marginMode": "ISOLATED"
        }
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = f"POST/api/v1/orders{json.dumps(close_order_data)}"
        headers = signer.headers(payload)
        
        response = requests.post(url, headers=headers, json=close_order_data, timeout=10)
        data = response.json()
        
        if data.get('code') == '200000':
            close_order_id = data.get('data', {}).get('orderId')
            logger.info(f"Pozisyon %2 zararla kapatıldı, Order ID: {close_order_id}")
            await send_telegram_message(
                f"🛑 Pozisyon %2 Zararla Kapatıldı!\n"
                f"Sembol: {SYMBOL}\n"
                f"Yön: {side.upper()}\n"
                f"Giriş: {entry_price:.2f} USDT\n"
                f"Kapanış: {current_price:.2f} USDT\n"
                f"Büyüklük: {size} kontrat\n"
                f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            # Açık limit/piyasa emirlerini temizle
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
        else:
            logger.error(f"Pozisyon kapatma başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            await send_telegram_message(
                f"❌ Pozisyon kapatma başarısız: {data.get('msg', 'Bilinmeyen hata')}"
            )
    
    await asyncio.sleep(60)
    continue
            
            indicators = calculate_indicators()
            deepsearch_result = run_deepsearch()
            signal = get_grok_signal(indicators, deepsearch_result)
            
            if signal == "bekle":
                logger.info("Grok sinyali: Bekle")
                await asyncio.sleep(60)
                continue
            
            result = await open_position(signal, usdt_balance)
            if result.get("success"):
                logger.info("Pozisyon açıldı, bekleniyor")
            else:
                logger.error(f"Pozisyon açma başarısız: {result.get('error')}")
            await asyncio.sleep(60)
        
        except Exception as e:
            logger.error(f"Döngü hatası: {str(e)}")
            await send_telegram_message(f"⚠️ Döngü hatası: {str(e)}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
