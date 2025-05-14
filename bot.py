import os
import requests
from kucoin.client import Trade, Market
from telegram import Bot
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
import asyncio
import time
import json
import logging
from datetime import datetime, timedelta

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

# KuCoin istemcileri
try:
    trade_client = Trade(key=KUCOIN_API_KEY, secret=KUCOIN_API_SECRET, passphrase=KUCOIN_API_PASSPHRASE)
    market_client = Market(key=KUCOIN_API_KEY, secret=KUCOIN_API_SECRET, passphrase=KUCOIN_API_PASSPHRASE)
    logger.info("KuCoin istemcileri başarıyla başlatıldı")
except Exception as e:
    logger.error(f"KuCoin istemcisi başlatılamadı: {str(e)}")
    exit(1)

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

# Piyasa verileri
def get_market_data(symbol='ETHUSDTM', timeframe='5min', limit=100):
    try:
        klines = market_client.get_kline_data(symbol, timeframe, limit=limit)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'amount'])
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

# BTC fiyat değişimi
def get_btc_price_change():
    try:
        ticker = market_client.get_24hr_stats('BTCUSDTM')
        price_change_percent = float(ticker.get('changeRate', 0)) * 100
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
        return None, None, None
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

# İşlem aç
def open_position(symbol, side, leverage, balance, take_profit):
    try:
        price = float(market_client.get_ticker(symbol)['price'])
        size = (balance * leverage) / price
        size = round(size, 2)
        order = trade_client.create_market_order(symbol, side, leverage=leverage, size=size)
        logger.info(f"Pozisyon açıldı: {symbol}, Yön: {side}, Büyüklük: {size}, Fiyat: {price}")
        return {'order': order, 'size': size, 'entry_price': price, 'side': side, 'leverage': leverage, 'take_profit': take_profit}
    except Exception as e:
        error_message = f"Pozisyon açma hatası: {str(e)}"
        logger.error(error_message)
        send_telegram_message_sync(f"❌ {error_message}")
        return str(e)

# İşlem kapat
def close_position(symbol, position, reason):
    try:
        side = 'buy' if position['side'] == 'sell' else 'sell'
        order = trade_client.create_market_order(symbol, side, leverage=position['leverage'], size=position['size'])
        close_price = float(market_client.get_ticker(symbol)['price'])
        if position['side'] == 'buy':
            profit = (close_price - position['entry_price']) * position['size'] * position['leverage']
        else:
            profit = (position['entry_price'] - close_price) * position['size'] * position['leverage']
        trade_history.append({'time': datetime.utcnow(), 'profit': profit, 'reason': reason})
        logger.info(f"Pozisyon kapandı: {symbol}, Kâr/Zarar: {profit:.2f}, Neden: {reason}")
        return {'order': order, 'profit': profit, 'close_price': close_price, 'reason': reason}
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
        balance = float(trade_client.get_account_balance()['balance'])
        last_24h = datetime.utcnow() - timedelta(hours=24)
        recent_trades = [t for t in trade_history if t['time'] >= last_24h]
        trade_count = len(recent_trades)
        total_profit = sum(t['profit'] for t in recent_trades)
        sentiment = last_deep_search['sentiment']
        report = (f"📅 Günlük Rapor ({datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})\n"
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
    now = datetime.utcnow()
    if last_deep_search['timestamp'] is None:
        return True
    if now - last_deep_search['timestamp'] >= timedelta(hours=6):
        return True
    return False

# Günlük rapor zamanlaması
def should_run_daily_report():
    global last_report_time
    now = datetime.utcnow()
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
                last_deep_search = {'sentiment': sentiment, 'timestamp': datetime.utcnow()}
                await send_telegram_message(f"📰 DeepSearch Sonucu: ETH/USDT Sentiment = {sentiment}")
            else:
                sentiment = last_deep_search['sentiment']
            
            # Günlük rapor
            if should_run_daily_report():
                await daily_report()
                last_report_time = datetime.utcnow()
            
            # BTC fiyat değişimi
            btc_price_change = get_btc_price_change()
            if btc_price_change <= -3:
                await send_telegram_message(f"⚠️ BTC %3’ten fazla düştü: {btc_price_change:.2f}%")
            elif btc_price_change >= 3:
                await send_telegram_message(f"📈 BTC %3’ten fazla yükseldi: {btc_price_change:.2f}%")
            
            # Grok’un kararı
            decision, leverage, take_profit = grok_api_analysis(df, sentiment, btc_price_change)
            balance = float(trade_client.get_account_balance()['balance'])
            
            # Mevcut pozisyon kontrolü
            if open_position:
                current_price = float(market_client.get_ticker(symbol)['price'])
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
