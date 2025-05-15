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
SYMBOL = "ETHUSDTM"
LEVERAGE = 10
STOP_LOSS_PCT = 0.02  # %2
TAKE_PROFIT_PCT = 0.005  # %0.5
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
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            df["MACD"] = macd["MACD_12_26_9"]
            df["MACD_SIGNAL"] = macd["MACDS_12_26_9"]
            df["MA200"] = ta.sma(df["close"], length=200)
            df["EMA50"] = ta.ema(df["close"], length=50)
            indicators[tf_name] = {
                "RSI": df["RSI"].iloc[-1],
                "MACD": df["MACD"].iloc[-1],
                "MACD_SIGNAL": df["MACD_SIGNAL"].iloc[-1],
                "MA200": df["MA200"].iloc[-1],
                "EMA50": df["EMA50"].iloc[-1],
                "PRICE": df["close"].iloc[-1]
            }
        logger.info(f"İndikatörler: {indicators}")
        return indicators
    except Exception as e:
        logger.error(f"İndikatör hesaplama hatası: {str(e)}")
        return None

# DeepSearch simülasyon
def run_deepsearch():
    global last_deepsearch_result, last_deepsearch_time
    try:
        if time.time() - last_deepsearch_time < DEEPSEARCH_INTERVAL:
            logger.info("DeepSearch: Son sonucu kullanıyor")
            return last_deepsearch_result
        
        news_sentiment = "Neutral"  # Grok API’si eklenecek
        logger.info("DeepSearch: Haber taraması yapıldı")
        last_deepsearch_result = {"sentiment": news_sentiment, "timestamp": time.time()}
        last_deepsearch_time = time.time()
        return last_deepsearch_result
    except Exception as e:
        logger.error(f"DeepSearch hatası: {str(e)}")
        return None

# Grok sinyal
def get_grok_signal(indicators, deepsearch_result):
    try:
        if not indicators or not deepsearch_result:
            return "bekle"
        
        score = 0
        for tf, ind in indicators.items():
            if ind["RSI"] < 30:
                score += 0.2
            elif ind["RSI"] > 70:
                score -= 0.2
            if ind["MACD"] > ind["MACD_SIGNAL"]:
                score += 0.2
            elif ind["MACD"] < ind["MACD_SIGNAL"]:
                score -= 0.2
            if ind["EMA50"] > ind["MA200"]:
                score += 0.1
        
        if deepsearch_result["sentiment"] == "Bullish":
            score += 0.3
        elif deepsearch_result["sentiment"] == "Bearish":
            score -= 0.3
        
        logger.info(f"Grok sinyal puanı: {score}")
        if score > 0.5:
            return "buy"
        elif score < -0.5:
            return "sell"
        return "bekle"
    except Exception as e:
        logger.error(f"Grok sinyal hatası: {str(e)}")
        return "bekle"

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

# Pozisyon açma
async def open_position(side, usdt_balance):
    try:
        contract = get_contract_details()
        multiplier = contract["multiplier"]
        min_order_size = contract["min_order_size"]
        max_leverage = contract["max_leverage"]
        leverage = min(LEVERAGE, max_leverage)
        
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("ETH fiyatı alınamadı")
            return {"error": "Fiyat alınamadı"}
        
        total_value = usdt_balance * leverage
        size = max(min_order_size, int(total_value / (eth_price * multiplier)))
        position_value = size * eth_price * multiplier
        required_margin = position_value / leverage
        
        if required_margin > usdt_balance:
            logger.error(f"Yetersiz bakiye: Gerekli {required_margin:.2f} USDT, mevcut {usdt_balance:.2f} USDT")
            return {"error": "Yetersiz bakiye"}
        
        if side == "buy":
            stop_loss_price = eth_price * (1 - STOP_LOSS_PCT)
            take_profit_price = eth_price * (1 + TAKE_PROFIT_PCT)
        else:
            stop_loss_price = eth_price * (1 + STOP_LOSS_PCT)
            take_profit_price = eth_price * (1 - TAKE_PROFIT_PCT)
        
        order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": side,
            "symbol": SYMBOL,
            "leverage": str(leverage),
            "type": "market",
            "size": size,
            "marginMode": "isolated",
            "stopLossPrice": round(stop_loss_price, 2),
            "takeProfitPrice": round(take_profit_price, 2)
        }
        
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = "POST" + "/api/v1/orders" + json.dumps(order_data)
        headers = signer.headers(payload)
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()
        logger.info(f"Pozisyon açma yanıtı: {data}")
        
        if data.get('code') == '200000':
            order_id = data.get('data', {}).get('orderId')
            message = (
                f"📈 Yeni Pozisyon Açıldı ({SYMBOL})\n"
                f"Yön: {'Long' if side == 'buy' else 'Short'}\n"
                f"Giriş Fiyatı: {eth_price:.2f} USDT\n"
                f"Kontrat: {size}\n"
                f"Kaldıraç: {leverage}x\n"
                f"Pozisyon Değeri: {position_value:.2f} USDT\n"
                f"Stop Loss: {stop_loss_price:.2f} USDT\n"
                f"Take Profit: {take_profit_price:.2f} USDT\n"
                f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            await send_telegram_message(message)
            logger.info(f"Pozisyon açıldı! Sipariş ID: {order_id}")
            return {"success": True, "order_id": order_id}
        logger.error(f"Pozisyon açılamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return {"error": data.get('msg', 'Bilinmeyen hata')}
    except Exception as e:
        logger.error(f"Pozisyon açma hatası: {str(e)}")
        return {"error": str(e)}

# Ana döngü
async def main():
    while True:
        try:
            position = check_positions()
            if position["exists"]:
                logger.info(f"Açık pozisyon: {position['side']}, Giriş: {position['entry_price']}, PnL: {position['pnl']}")
                time.sleep(60)
                continue
            
            usdt_balance, position_margin = check_usdm_balance()
            logger.info(f"Bakiye: {usdt_balance:.2f} USDT, Pozisyon Margin: {position_margin:.2f} USDT")
            if usdt_balance < 5:
                logger.error(f"Yetersiz bakiye: {usdt_balance:.2f} USDT")
                time.sleep(60)
                continue
            
            indicators = calculate_indicators()
            deepsearch_result = run_deepsearch()
            signal = get_grok_signal(indicators, deepsearch_result)
            
            if signal == "bekle":
                logger.info("Grok sinyali: Bekle")
                time.sleep(60)
                continue
            
            result = await open_position(signal, usdt_balance)
            if result.get("success"):
                logger.info("Pozisyon açıldı, bekleniyor")
            else:
                logger.error(f"Pozisyon açma başarısız: {result.get('error')}")
            time.sleep(60)
        except Exception as e:
            logger.error(f"Döngü hatası: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
