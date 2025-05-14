import os
import requests
import http.client
import hmac
import hashlib
import base64
import time
from telegram import Bot
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
import asyncio
import json
import logging
from datetime import datetime, timedelta
from datetime import timezone

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Çevre değişkenleri
load_dotenv()
KUCOIN_API_KEY = os.getenv('KUCOIN_API_KEY')
KUCOIN_API_SECRET = os.getenv('KUCOIN_API_SECRET')
KUCOIN_API_PASSPHRASE = os.getenv('KUCOIN_API_PASSPHRASE')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GROK_API_KEY = os.getenv('GROK_API_KEY')

# Çevre değişkenlerini kontrol et
if not all([KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GROK_API_KEY]):
    logger.error("Eksik çevre değişkeni! Lütfen .env dosyasını ve Heroku Config Vars'ı kontrol edin.")
    exit(1)

# KuCoin API imzalama
def generate_signature(endpoint, method, params, api_secret):
    timestamp = str(int(time.time() * 1000))
    str_to_sign = timestamp + method + endpoint + params
    sign = hmac.new(api_secret.encode('utf-8'), str_to_sign.encode('utf-8'), hashlib.sha256).digest()
    return base64.b64encode(sign).decode('utf-8'), timestamp

# Telegram istemcisi
try:
    telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logger.info("Telegram bot başarıyla başlatıldı")
except Exception as e:
    logger.error(f"Telegram bot başlatılamadı: {str(e)}")
    exit(1)

# Global değişkenler
last_deep_search = {'sentiment': 'neutral', 'timestamp': None}
open_position = None
STOP_LOSS = 0.02
last_report_time = None
trade_history = []

# Telegram mesajı için yardımcı fonksiyon (senkron bağlamda kullanım için)
def send_telegram_message_sync(message):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message))
        logger.info(f"Telegram mesajı gönderildi: {message[:50]}...")
    except Exception as e:
        logger.error(f"Telegram mesajı gönderilemedi: {str(e)}")

# Telegram bildirimi (asenkron)
async def send_telegram_message(message):
    try:
        await telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.info(f"Telegram mesajı gönderildi: {message[:50]}...")
    except Exception as e:
        logger.error(f"Telegram hatası: {str(e)}")

# Piyasa verileri (HTTP ile)
def get_market_data(symbol='ETHUSDTM', timeframe='5', limit=100):
    try:
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        from_time = int((datetime.now(timezone.utc) - timedelta(minutes=limit * int(timeframe))).timestamp() * 1000)
        to_time = int(datetime.now(timezone.utc).timestamp() * 1000)
        endpoint = f"/api/v1/kline/query?symbol={symbol}&granularity={timeframe}&from={from_time}&to={to_time}"
        params = f"symbol={symbol}&granularity={timeframe}&from={from_time}&to={to_time}"
        sign, timestamp = generate_signature("/api/v1/kline/query", "GET", params, KUCOIN_API_SECRET)
        headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': sign,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
        }
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        if data['code'] != '200000':
            raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
        klines = data['data']
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['close'] = df['close'].astype(float)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['ma50'] = ta.sma(df['close'], length=50)
        df['ma200'] = ta.sma(df['close'], length=200)
        df['macd'] = ta.macd(df['close'], fast=12, slow=26, signal=9)['MACD_12_26_9']
        df['macd_signal'] = ta.macd(df['close'], fast=12, slow=26, signal=9)['MACDs_12_26_9']
        logger.info(f"Piyasa verisi alındı: {symbol}, RSI: {df['rsi'].iloc[-1]:.2f}")
        return df
    except Exception as e:
        logger.error(f"Veri çekme hatası: {str(e)}")
        send_telegram_message_sync(f"❌ Veri çekme hatası: {str(e)}")
        return None

# BTC fiyat değişimi (HTTP ile)
def get_btc_price_change():
    try:
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        endpoint = "/api/v1/market/stats?symbol=BTCUSDTM"
        params = "symbol=BTCUSDTM"
        sign, timestamp = generate_signature("/api/v1/market/stats", "GET", params, KUCOIN_API_SECRET)
        headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': sign,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
        }
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        if data['code'] != '200000':
            raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
        price_change_percent = float(data['data'].get('changeRate', 0)) * 100
        logger.info(f"BTC 24 saatlik değişim: {price_change_percent:.2f}%")
        return price_change_percent
    except Exception as e:
        logger.error(f"BTC fiyat hatası: {str(e)}")
        send_telegram_message_sync(f"❌ BTC fiyat hatası: {str(e)}")
        return 0

# Grok API ile analiz
def grok_api_analysis(df, sentiment='neutral', btc_price_change=0):
    if df is None:
        logger.error("Veri eksik, analiz yapılamadı")
        return None, None  None, None
    last_row = df.iloc[-1]
    payload = {
        'symbol': 'ETH/USDT',
        'rsi': last_row['rsi'],
        'ma50': last_row['ma50'],
        'ma200': last_row['ma200'],
        'macd': last_row['macd'],
        'macd_signal': last_row['macd_signal'],
        'sentiment': sentiment,
        'btc_price_change': btc_price_change
    }
    
    headers = {
        'Authorization': f'Bearer {GROK_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post('https://api.x.ai/grok/analyze', json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        decision = result.get('decision')
        if last_row['rsi'] < 45:
            decision = 'buy'
            signal_strength = 'strong' if last_row['rsi'] < 30 else 'normal'
        elif last_row['rsi'] > 55:
            decision = 'sell'
            signal_strength = 'strong' if last_row['rsi'] > 70 else 'normal'
        else:
            decision = None
            signal_strength = 'normal'
        take_profit = 0.01 if signal_strength == 'strong' else 0.005
        log_message = (f"Grok API kararı: {decision}, RSI: {last_row['rsi']:.2f}, "
                       f"MA50: {last_row['ma50']:.2f}, MA200: {last_row['ma200']:.2f}, "
                       f"MACD: {last_row['macd']:.2f}, Sentiment: {sentiment}, "
                       f"BTC: {btc_price_change:.2f}%")
        logger.info(log_message)
        send_telegram_message_sync(f"📊 Analiz: {log_message}")
        return decision, min(result.get('leverage', 5), 5), take_profit
    except Exception as e:
        error_message = f"Grok API hatası: {str(e)}"
        logger.error(error_message)
        send_telegram_message_sync(f"❌ {error_message}")
        return None, None, None

# İşlem aç (HTTP ile)
def open_position(symbol, side, leverage, balance, take_profit):
    try:
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        endpoint = f"/api/v1/ticker?symbol={symbol}"
        params = f"symbol={symbol}"
        sign, timestamp = generate_signature("/api/v1/ticker", "GET", params, KUCOIN_API_SECRET)
        headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': sign,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
        }
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        if data['code'] != '200000':
            raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
        price = float(data['data']['price'])
        
        size = (balance * leverage) / price
        size = round(size, 2)
        
        # Market order oluştur
        order_endpoint = "/api/v1/orders"
        order_params = f"clientOid={str(int(time.time() * 1000))}&side={side}&symbol={symbol}&type=market&leverage={leverage}&size={size}"
        order_sign, order_timestamp = generate_signature("/api/v1/orders", "POST", order_params, KUCOIN_API_SECRET)
        order_headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': order_sign,
            'KC-API-TIMESTAMP': order_timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        conn.request("POST", order_endpoint, order_params, order_headers)
        order_res = conn.getresponse()
        order_data = json.loads(order_res.read().decode("utf-8"))
        if order_data['code'] != '200000':
            raise Exception(f"Order hatası: {order_data.get('msg', 'Bilinmeyen hata')}")
        
        logger.info(f"Pozisyon açıldı: {symbol}, Yön: {side}, Büyüklük: {size}, Fiyat: {price}")
        return {'order': order_data, 'size': size, 'entry_price': price, 'side': side, 'leverage': leverage, 'take_profit': take_profit}
    except Exception as e:
        error_message = f"Pozisyon açma hatası: {str(e)}"
        logger.error(error_message)
        send_telegram_message_sync(f"❌ {error_message}")
        return str(e)

# İşlem kapat (HTTP ile)
def close_position(symbol, position, reason):
    try:
        side = 'buy' if position['side'] == 'sell' else 'sell'
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        endpoint = f"/api/v1/ticker?symbol={symbol}"
        params = f"symbol={symbol}"
        sign, timestamp = generate_signature("/api/v1/ticker", "GET", params, KUCOIN_API_SECRET)
        headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': sign,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
        }
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        if data['code'] != '200000':
            raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
        close_price = float(data['data']['price'])
        
        # Market order oluştur
        order_endpoint = "/api/v1/orders"
        order_params = f"clientOid={str(int(time.time() * 1000))}&side={side}&symbol={symbol}&type=market&leverage={position['leverage']}&size={position['size']}"
        order_sign, order_timestamp = generate_signature("/api/v1/orders", "POST", order_params, KUCOIN_API_SECRET)
        order_headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': order_sign,
            'KC-API-TIMESTAMP': order_timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        conn.request("POST", order_endpoint, order_params, order_headers)
        order_res = conn.getresponse()
        order_data = json.loads(order_res.read().decode("utf-8"))
        if order_data['code'] != '200000':
            raise Exception(f"Order hatası: {order_data.get('msg', 'Bilinmeyen hata')}")
        
        if position['side'] == 'buy':
            profit = (close_price - position['entry_price']) * position['size'] * position['leverage']
        else:
            profit = (position['entry_price'] - close_price) * position['size'] * position['leverage']
        trade_history.append({'time': datetime.now(timezone.utc), 'profit': profit, 'reason': reason})
        logger.info(f"Pozisyon kapandı: {symbol}, Kâr/Zarar: {profit:.2f}, Neden: {reason}")
        return {'order': order_data, 'profit': profit, 'close_price': close_price, 'reason': reason}
    except Exception as e:
        error_message = f"Pozisyon kapatma hatası: {str(e)}"
        logger.error(error_message)
        send_telegram_message_sync(f"❌ {error_message}")
        return str(e)

# Take-profit ve stop-loss kontrolü
def check_take_profit_stop_loss(position, current_price):
    try:
        if position['side'] == 'buy':
            price_change = (current_price - position['entry_price']) / position['entry_price']
            if price_change >= position['take_profit']:
                return 'take-profit'
            if price_change <= -STOP_LOSS:
                return 'stop-loss'
        else:
            price_change = (position['entry_price'] - current_price) / position['entry_price']
            if price_change >= position['take_profit']:
                return 'take-profit'
            if price_change <= -STOP_LOSS:
                return 'stop-loss'
        return None
    except Exception as e:
        logger.error(f"Take-profit/stop-loss kontrol hatası: {str(e)}")
        return None

# Günlük rapor
async def daily_report():
    global trade_history
    try:
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        endpoint = "/api/v1/account-overview?currency=USDT"
        params = "currency=USDT"
        sign, timestamp = generate_signature("/api/v1/account-overview", "GET", params, KUCOIN_API_SECRET)
        headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-SIGN': sign,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
        }
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        if data['code'] != '200000':
            raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
        balance = float(data['data']['accountEquity'])
        
        last_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_trades = [t for t in trade_history if t['time'] >= last_24h]
        trade_count = len(recent_trades)
        total_profit = sum(t['profit'] for t in recent_trades)
        sentiment = last_deep_search['sentiment']
        report = (f"📅 Günlük Rapor ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})\n"
                  f"🟢 Bot aktif ve çalışıyor\n"
                  f"💰 Bakiye: {balance:.2f} USDT\n"
                  f"📊 Son 24 saatteki işlem sayısı: {trade_count}\n"
                  f"📈 Toplam kâr/zarar: {total_profit:.2f} USDT\n"
                  f"📰 Son DeepSearch Sentiment: {sentiment}")
        await send_telegram_message(report)
        trade_history = [t for t in trade_history if t['time'] >= last_24h]
        logger.info("Günlük rapor gönderildi")
    except Exception as e:
        error_message = f"Günlük rapor hatası: {str(e)}"
        logger.error(error_message)
        await send_telegram_message(f"❌ {error_message}")

# DeepSearch simülasyonu
def deep_search_simulation():
    try:
        import random
        sentiments = ['positive', 'neutral', 'negative']
        sentiment = random.choice(sentiments)
        logger.info(f"DeepSearch simülasyonu: {sentiment}")
        return sentiment
    except Exception as e:
        logger.error(f"DeepSearch simülasyon hatası: {str(e)}")
        return 'neutral'

# DeepSearch zamanlaması
def should_run_deep_search():
    global last_deep_search
    now = datetime.now(timezone.utc)
    if last_deep_search['timestamp'] is None:
        return True
    if now - last_deep_search['timestamp'] >= timedelta(hours=6):
        return True
    return False

# Günlük rapor zamanlaması
def should_run_daily_report():
    global last_report_time
    now = datetime.now(timezone.utc)
    if last_report_time is None:
        return True
    if now - last_report_time >= timedelta(hours=24):
        return True
    return False

# Ana döngü
async def main():
    global last_deep_search, open_position, last_report_time
    symbol = 'ETHUSDTM'
    # Başlangıç bildirimi
    try:
        await send_telegram_message("🟢 Bot başlatıldı! ETH/USDT izleniyor...")
        logger.info("Bot başlatıldı, ana döngü başlıyor")
    except Exception as e:
        logger.error(f"Başlangıç bildirimi hatası: {str(e)}")
    
    while True:
        try:
            # Piyasa verileri
            df = get_market_data(symbol)
            if df is None:
                await send_telegram_message("❌ Piyasa verisi alınamadı")
                time.sleep(60)
                continue
            
            # DeepSearch
            if should_run_deep_search():
                sentiment = deep_search_simulation()
                last_deep_search = {'sentiment': sentiment, 'timestamp': datetime.now(timezone.utc)}
                await send_telegram_message(f"📰 DeepSearch Sonucu: ETH/USDT Sentiment = {sentiment}")
            else:
                sentiment = last_deep_search['sentiment']
            
            # Günlük rapor
            if should_run_daily_report():
                await daily_report()
                last_report_time = datetime.now(timezone.utc)
            
            # BTC fiyat değişimi
            btc_price_change = get_btc_price_change()
            if btc_price_change <= -3:
                await send_telegram_message(f"⚠️ BTC %3’ten fazla düştü: {btc_price_change:.2f}%")
            elif btc_price_change >= 3:
                await send_telegram_message(f"📈 BTC %3’ten fazla yükseldi: {btc_price_change:.2f}%")
            
            # Grok’un kararı
            decision, leverage, take_profit = grok_api_analysis(df, sentiment, btc_price_change)
            
            # Bakiye kontrolü
            conn = http.client.HTTPSConnection("api-futures.kucoin.com")
            endpoint = "/api/v1/account-overview?currency=USDT"
            params = "currency=USDT"
            sign, timestamp = generate_signature("/api/v1/account-overview", "GET", params, KUCOIN_API_SECRET)
            headers = {
                'KC-API-KEY': KUCOIN_API_KEY,
                'KC-API-SIGN': sign,
                'KC-API-TIMESTAMP': timestamp,
                'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
            }
            conn.request("GET", endpoint, '', headers)
            res = conn.getresponse()
            data = json.loads(res.read().decode("utf-8"))
            if data['code'] != '200000':
                raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
            balance = float(data['data']['accountEquity'])
            
            # Mevcut pozisyon kontrolü
            if open_position:
                conn = http.client.HTTPSConnection("api-futures.kucoin.com")
                endpoint = f"/api/v1/ticker?symbol={symbol}"
                params = f"symbol={symbol}"
                sign, timestamp = generate_signature("/api/v1/ticker", "GET", params, KUCOIN_API_SECRET)
                headers = {
                    'KC-API-KEY': KUCOIN_API_KEY,
                    'KC-API-SIGN': sign,
                    'KC-API-TIMESTAMP': timestamp,
                    'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
                }
                conn.request("GET", endpoint, '', headers)
                res = conn.getresponse()
                data = json.loads(res.read().decode("utf-8"))
                if data['code'] != '200000':
                    raise Exception(f"API hatası: {data.get('msg', 'Bilinmeyen hata')}")
                current_price = float(data['data']['price'])
                
                close_reason = check_take_profit_stop_loss(open_position, current_price)
                if close_reason:
                    close_result = close_position(symbol, open_position, close_reason)
                    if isinstance(close_result, dict):
                        message = (f"📉 Pozisyon Kapandı\n"
                                   f"Sembol: ETH/USDT\n"
                                   f"Yön: {open_position['side'].upper()}\n"
                                   f"Kâr/Zarar: {close_result['profit']:.2f} USDT\n"
                                   f"Kapanış Fiyatı: ${close_result['close_price']:.2f}\n"
                                   f"Kaldıraç: {open_position['leverage']}x\n"
                                   f"Büyüklük: {open_position['size']} ETH\n"
                                   f"Bakiye: {balance:.2f} USDT\n"
                                   f"Neden: {close_result['reason']}")
                        await send_telegram_message(message)
                        open_position = None
            
            # Yeni pozisyon
            if decision and not open_position and take_profit:
                position = open_position(symbol, decision, leverage, balance, take_profit)
                if isinstance(position, dict):
                    open_position = position
                    signal_strength = 'strong' if take_profit == 0.01 else 'normal'
                    message = (f"📊 Yeni Pozisyon Açıldı\n"
                               f"Sembol: ETH/USDT\n"
                               f"Yön: {decision.upper()}\n"
                               f"Kaldıraç: {leverage}x\n"
                               f"Büyüklük: {position['size']} ETH\n"
                               f"Giriş Fiyatı: ${position['entry_price']:.2f}\n"
                               f"Take-Profit: {take_profit*100:.1f}%\n"
                               f"Bakiye: {balance:.2f} USDT\n"
                               f"Sinyal Gücü: {signal_strength}\n"
                               f"Sentiment: {sentiment}\n"
                               f"BTC Değişim: {btc_price_change:.2f}%")
                    await send_telegram_message(message)
            
            # Ters sinyalde kapatma
            if open_position and decision and decision != open_position['side']:
                close_result = close_position(symbol, open_position, 'ters sinyal')
                if isinstance(close_result, dict):
                    message = (f"📉 Pozisyon Kapandı\n"
                               f"Sembol: ETH/USDT\n"
                               f"Yön: {open_position['side'].upper()}\n"
                               f"Kâr/Zarar: {close_result['profit']:.2f} USDT\n"
                               f"Kapanış Fiyatı: ${close_result['close_price']:.2f}\n"
                               f"Kaldıraç: {open_position['leverage']}x\n"
                               f"Büyüklük: {open_position['size']} ETH\n"
                               f"Bakiye: {balance:.2f} USDT\n"
                               f"Neden: {close_result['reason']}")
                    await send_telegram_message(message)
                    open_position = None
            
            time.sleep(300)
        
        except Exception as e:
            error_message = f"Genel hata: {str(e)}"
            logger.error(error_message)
            await send_telegram_message(f"❌ {error_message}")
            time.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
