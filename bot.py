import time
import logging
import requests
import base64
import hashlib
import hmac
import json
import uuid
import os
import talib
import numpy as np
from datetime import datetime, timedelta
import telegram
from telegram.error import TelegramError

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Heroku config vars
GROK_API_KEY = os.environ.get('GROK_API_KEY')
KUCOIN_API_KEY = os.environ.get('KUCOIN_API_KEY')
KUCOIN_API_SECRET = os.environ.get('KUCOIN_API_SECRET')
KUCOIN_API_PASSPHRASE = os.environ.get('KUCOIN_API_PASSPHRASE')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Telegram bot
telegram_bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

# Sabit ayarlar
SYMBOL = "ETHUSDTM"
LEVERAGE = 10
STOP_LOSS_PCT = 0.02  # %2
TAKE_PROFIT_PCT = 0.005  # %0.5
DEEPSEARCH_INTERVAL = 4 * 3600  # 4 saat
DEEPSEARCH_PER_DAY = 6

# DeepSearch sonuçlarını saklamak için
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

# KuCoin’dan K-line verileri alma
def get_klines(timeframe="1hour", limit=200):
    try:
        url = f"https://api-futures.kucoin.com/api/v1/market/candles?symbol={SYMBOL}&type={timeframe}&limit={limit}"
        response = requests.get(url)
        data = response.json()
        if data.get('code') == '200000':
            klines = data.get('data', [])
            closes = np.array([float(k[2]) for k in klines[::-1]], dtype=np.float64)
            return closes
        logger.error(f"K-line alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
        return None
    except Exception as e:
        logger.error(f"K-line hatası: {str(e)}")
        return None

# Teknik indikatörler hesaplama
def calculate_indicators():
    try:
        indicators = {}
        timeframes = {"1hour": 1, "6hour": 6, "5day": 120, "30day": 720}  # Saat cinsinden
        for tf, hours in timeframes.items():
            closes = get_klines(tf.lower(), 200)
            if closes is None or len(closes) < 200:
                logger.warning(f"{tf} için yeterli veri yok")
                continue
            indicators[tf] = {
                "RSI": talib.RSI(closes, timeperiod=14)[-1],
                "MACD": talib.MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)[0][-1],
                "MACD_SIGNAL": talib.MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)[1][-1],
                "MA200": talib.MA(closes, timeperiod=200)[-1],
                "EMA50": talib.EMA(closes, timeperiod=50)[-1],
                "PRICE": closes[-1]
            }
        logger.info(f"İndikatörler: {indicators}")
        return indicators
    except Exception as e:
        logger.error(f"İndikatör hesaplama hatası: {str(e)}")
        return None

# DeepSearch simülasyonu (Grok API’si yerine geçici)
def run_deepsearch():
    global last_deepsearch_result, last_deepsearch_time
    try:
        if time.time() - last_deepsearch_time < DEEPSEARCH_INTERVAL:
            logger.info("DeepSearch: Son sonucu kullanıyor")
            return last_deepsearch_result
        
        # Simüle edilmiş DeepSearch (haber odaklı)
        # Gerçek Grok API’si için endpoint eklenecek
        news_sentiment = "Neutral"  # Örnek: "Bullish", "Bearish", "Neutral"
        logger.info("DeepSearch: Haber taraması yapıldı")
        last_deepsearch_result = {"sentiment": news_sentiment, "timestamp": time.time()}
        last_deepsearch_time = time.time()
        return last_deepsearch_result
    except Exception as e:
        logger.error(f"DeepSearch hatası: {str(e)}")
        return None

# Grok’tan sinyal alma (simülasyon, Grok API’si eklenecek)
def get_grok_signal(indicators, deepsearch_result):
    try:
        if not indicators or not deepsearch_result:
            return "bekle"
        
        score = 0
        for tf, ind in indicators.items():
            # RSI: <30 long, >70 short
            if ind["RSI"] < 30:
                score += 0.2
            elif ind["RSI"] > 70:
                score -= 0.2
            # MACD: MACD > signal long, < signal short
            if ind["MACD"] > ind["MACD_SIGNAL"]:
                score += 0.2
            elif ind["MACD"] < ind["MACD_SIGNAL"]:
                score -= 0.2
            # EMA50 > MA200 bullish
            if ind["EMA50"] > ind["MA200"]:
                score += 0.1
        
        # DeepSearch sentiment
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
        if data.get('code') == '200000':
            usdt_balance = data.get('data', {}).get('availableBalance', 0)
            position_margin = data.get('data', {}).get('positionMargin', 0)
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

# Mevcut pozisyonları kontrol
def check_positions():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/positions?symbol={SYMBOL}"
        payload = f"GET/api/v1/positions?symbol={SYMBOL}"
        headers = signer.headers(payload)
        response = requests.get(url, headers=headers)
        data = response.json()
        if data.get('code') == '200000':
            positions = data.get('data', [])
            if positions:
                pos = positions[0]
                return {
                    "exists": True,
                    "side": "long" if pos.get('currentQty', 0) > 0 else "short",
                    "entry_price": pos.get('avgEntryPrice', 0),
                    "margin": pos.get('posMargin', 0),
                    "pnl": pos.get('unrealisedPnl', 0)
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
        
        # Tüm bakiyeyi kullan
        total_value = usdt_balance * leverage
        size = max(min_order_size, int(total_value / (eth_price * multiplier)))
        position_value = size * eth_price * multiplier
        required_margin = position_value / leverage
        
        if required_margin > usdt_balance:
            logger.error(f"Yetersiz bakiye: Gerekli {required_margin:.2f} USDT, mevcut {usdt_balance:.2f} USDT")
            return {"error": "Yetersiz bakiye"}
        
        # Stop loss ve take profit
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
            "leverage": leverage,
            "type": "market",
            "size": size,
            "marginMode": "ISOLATED",
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
            # Pozisyon kontrol
            position = check_positions()
            if position["exists"]:
                # Pozisyon açık, kapanış bildirimi için kontrol
                if position["pnl"] != 0:  # Basit kapanış tespiti
                    message = (
                        f"📉 Pozisyon Kapandı ({SYMBOL})\n"
                        f"Yön: {position['side'].capitalize()}\n"
                        f"Çıkış Fiyatı: {get_eth_price():.2f} USDT\n"
                        f"PNL: {position['pnl']:.2f} USDT\n"
                        f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                    )
                    await send_telegram_message(message)
                time.sleep(60)
                continue
            
            # Bakiye al
            usdt_balance, _ = check_usdm_balance()
            if usdt_balance < 5:  # Minimum 5 USDT
                logger.error(f"Yetersiz bakiye: {usdt_balance:.2f} USDT")
                time.sleep(60)
                continue
            
            # İndikatörler ve DeepSearch
            indicators = calculate_indicators()
            deepsearch_result = run_deepsearch()
            signal = get_grok_signal(indicators, deepsearch_result)
            
            if signal == "bekle":
                logger.info("Grok sinyali: Bekle")
                time.sleep(60)
                continue
            
            # Pozisyon aç
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
