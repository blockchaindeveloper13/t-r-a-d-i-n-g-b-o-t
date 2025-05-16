import requests
import json
import time
import uuid
import asyncio
import logging
import http.client
from urllib.parse import urlencode
from datetime import datetime

# Logging ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Sabitler (Bu değerleri kendi ayarlarınla değiştir)
SYMBOL = "ETHUSDTM"
KUCOIN_API_KEY = "your_api_key"
KUCOIN_API_SECRET = "your_api_secret"
KUCOIN_API_PASSPHRASE = "your_api_passphrase"
TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"
TELEGRAM_CHAT_ID = "-1001234567890"  # Grup ID’sini buraya gir (doğru ID olduğundan emin ol)
STOP_LOSS_PCT = 0.008  # %0.8 stop-loss
TAKE_PROFIT_PCT = 0.012  # %1.2 take-profit

# KuCoin API için kimlik doğrulama sınıfı (Varsayıyorum ki bu sınıfın var)
class KcSigner:
    def __init__(self, api_key, api_secret, api_passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    def headers(self, payload, timestamp=None):
        import hmac
        import hashlib
        import base64
        if timestamp is None:
            timestamp = str(int(time.time() * 1000))
        sign = base64.b64encode(hmac.new(
            self.api_secret.encode('utf-8'),
            (timestamp + payload).encode('utf-8'),
            hashlib.sha256
        ).digest()).decode('utf-8')
        return {
            "KC-API-KEY": self.api_key,
            "KC-API-SIGN": sign,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": self.api_passphrase,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json"
        }

# Fiyat alma fonksiyonu
def get_eth_price():
    try:
        url = "https://api-futures.kucoin.com/api/v1/mark-price?symbol=ETHUSDTM"
        response = requests.get(url)
        data = response.json()
        if data.get('code') == '200000':
            price = float(data['data']['value'])
            return price
        else:
            logger.error(f"Fiyat alınamadı: {data.get('msg')}")
            return None
    except Exception as e:
        logger.error(f"Fiyat alma hatası: {str(e)}")
        return None

# Pozisyon kontrol fonksiyonu
def check_positions():
    try:
        url = "https://api-futures.kucoin.com/api/v1/position?symbol=ETHUSDTM"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers("GET/api/v1/position?symbol=ETHUSDTM")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Pozisyon yanıtı: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Pozisyon alınamadı: {data.get('msg')}")
            return {"exists": False}
        
        position_data = data.get('data', {})
        if not position_data or not position_data.get('isOpen', False):
            return {"exists": False}
        
        return {
            "exists": True,
            "side": "long" if position_data['currentQty'] > 0 else "short",
            "entry_price": position_data['avgEntryPrice'],
            "pnl": position_data['unrealisedPnl'],
            "currentQty": position_data['currentQty'],
            "currentTimestamp": position_data['currentTimestamp']
        }
    except Exception as e:
        logger.error(f"Pozisyon kontrol hatası: {str(e)}")
        return {"exists": False}

# Aktif stop emirlerini kontrol fonksiyonu
def check_stop_orders():
    try:
        url = "https://api-futures.kucoin.com/api/v1/stopOrders?symbol=ETHUSDTM"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers("GET/api/v1/stopOrders?symbol=ETHUSDTM")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Stop emirleri yanıtı: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Stop emirleri alınamadı: {data.get('msg')}")
            return None
        
        orders = data.get('data', {}).get('items', [])
        return orders if orders else None
    except Exception as e:
        logger.error(f"Stop emir kontrol hatası: {str(e)}")
        return None

# Bakiye kontrol fonksiyonu
def check_usdm_balance():
    try:
        url = "https://api-futures.kucoin.com/api/v1/account-overview?currency=USDT"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers("GET/api/v1/account-overview?currency=USDT")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Bakiye yanıtı: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Bakiye alınamadı: {data.get('msg')}")
            return None, None
        
        account_data = data.get('data', {})
        available_balance = float(account_data.get('availableBalance', 0))
        position_margin = float(account_data.get('positionMargin', 0))
        return available_balance, position_margin
    except Exception as e:
        logger.error(f"Bakiye kontrol hatası: {str(e)}")
        return None, None

# Kontrat detaylarını alma fonksiyonu
def get_contract_details():
    try:
        url = "https://api-futures.kucoin.com/api/v1/contract/detail/ETHUSDTM"
        response = requests.get(url)
        data = response.json()
        if data.get('code') == '200000':
            return data.get('data', {})
        else:
            logger.error(f"Kontrat detayları alınamadı: {data.get('msg')}")
            return None
    except Exception as e:
        logger.error(f"Kontrat detay alma hatası: {str(e)}")
        return None

# Tüm emirleri iptal etme fonksiyonu
async def cancel_all_orders(symbol):
    try:
        endpoint = f"/api/v3/orders?symbol={symbol}"
        method = "DELETE"
        payload = ''
        timestamp = str(int(time.time() * 1000))
        
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers(f"{method}{endpoint}{payload}", timestamp=timestamp)
        
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        conn.request(method, endpoint, payload, headers)
        res = conn.getresponse()
        data = res.read().decode("utf-8")
        response = json.loads(data)
        logger.info(f"Tüm emir iptal yanıtı: {response}")
        
        if response.get('code') == '200000':
            logger.info(f"Tüm emir başarıyla iptal edildi: {symbol}")
            return True
        else:
            logger.error(f"Emir iptal edilemedi: {response.get('msg', 'Bilinmeyen hata')}")
            return False
    except Exception as e:
        logger.error(f"Emir iptal hatası: {str(e)}")
        return False
    finally:
        conn.close()

# Pozisyon kapatma fonksiyonu
async def close_position():
    try:
        position = check_positions()
        if not position["exists"]:
            logger.info("Kapatılacak pozisyon bulunamadı.")
            return False

        size = abs(position["currentQty"])
        side = "sell" if position["side"] == "long" else "buy"
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("Fiyat alınamadı, pozisyon kapatılamadı.")
            return False

        order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": side,
            "symbol": SYMBOL,
            "type": "market",
            "size": size,
            "marginMode": "ISOLATED"
        }

        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = f"POST/api/v1/orders{json.dumps(order_data)}"
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        headers = signer.headers(payload)
        logger.info(f"Pozisyon kapatma isteği gönderiliyor: {order_data}")
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()
        logger.info(f"Pozisyon kapatma yanıtı: {data}")

        if data.get('code') == '200000':
            logger.info(f"Pozisyon başarıyla kapatıldı! Sipariş ID: {data.get('data', {}).get('orderId')}")
            return True
        else:
            logger.error(f"Pozisyon kapatılamadı: {data.get('msg', 'Bilinmeyen hata')}")
            return False
    except Exception as e:
        logger.error(f"Pozisyon kapatma hatası: {str(e)}")
        return False

# Telegram bildirim fonksiyonu
async def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=data)
        if response.status_code == 200:
            logger.info(f"Telegram mesajı gönderildi: {message}")
            return True
        else:
            logger.error(f"Telegram mesajı gönderilemedi: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram mesajı gönderilemedi: {str(e)}")
        return False

# Pozisyon açma fonksiyonu
async def open_position(signal, usdt_balance, position_margin):
    try:
        # Gerçek kullanılabilir bakiyeyi hesapla
        effective_balance = usdt_balance - position_margin
        if effective_balance < 5:
            logger.error(f"Yetersiz kullanılabilir bakiye: {effective_balance:.2f} USDT (Toplam: {usdt_balance:.2f}, Pozisyon Margin: {position_margin:.2f})")
            return {"success": False, "error": "Yetersiz bakiye"}
        
        # Kontrat detayları
        contract = get_contract_details()
        if not contract:
            logger.warning("Kontrat detayları alınamadı, varsayılan değerler kullanılıyor.")
            multiplier = 0.001
            min_order_size = 1
            max_leverage = 20
            tick_size = 0.01
        else:
            multiplier = float(contract.get('multiplier', 0.001))
            min_order_size = int(contract.get('minOrderQty', 1))
            max_leverage = int(contract.get('maxLeverage', 20))
            tick_size = float(contract.get('tickSize', 0.01))
            logger.info(f"Kontrat detayları: tickSize={tick_size}, multiplier={multiplier}, min_order_size={min_order_size}, max_leverage={max_leverage}")
        
        # Fiyat al
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("Fiyat alınamadı, pozisyon açılamıyor.")
            return {"success": False, "error": "Fiyat alınamadı"}
        logger.info(f"Alınan Fiyat: {eth_price:.2f} USDT, Symbol: {SYMBOL}")
        
        # 10x kaldıraç denemesi
        usdt_amount = effective_balance * 0.9
        leverage = "10" if max_leverage >= 10 else str(max_leverage)
        total_value = usdt_amount * int(leverage)
        size = max(min_order_size, int(total_value / (eth_price * multiplier)))
        position_value = size * eth_price * multiplier
        required_margin = position_value / int(leverage)
        logger.info(f"10x Kaldıraç: {size} kontrat (Toplam Değer: {position_value:.2f} USDT, Gerekli Margin: {required_margin:.2f} USDT, Fiyat: {eth_price:.2f} USDT)")
        
        if required_margin > effective_balance:
            logger.warning(f"10x kaldıraç için yetersiz bakiye: Gerekli margin {required_margin:.2f} USDT, mevcut {effective_balance:.2f} USDT")
            leverage = "5" if max_leverage >= 5 else str(max_leverage)
            total_value = usdt_amount * int(leverage)
            size = max(min_order_size, int(total_value / (eth_price * multiplier) / 2))
            position_value = size * eth_price * multiplier
            required_margin = position_value / int(leverage)
            logger.info(f"5x Kaldıraç: {size} kontrat (Toplam Değer: {position_value:.2f} USDT, Gerekli Margin: {required_margin:.2f} USDT)")
        
        if required_margin > effective_balance:
            logger.error(f"Yetersiz bakiye: Gerekli margin {required_margin:.2f} USDT, mevcut {effective_balance:.2f} USDT")
            return {"success": False, "error": f"Yetersiz bakiye: {required_margin:.2f} USDT gerekli"}
        
        # Stop-loss ve take-profit fiyatlarını hesapla
        def round_to_tick_size(price, tick_size):
            return round(price / tick_size) * tick_size
        
        stop_loss_price = eth_price * (1 - STOP_LOSS_PCT) if signal == "buy" else eth_price * (1 + STOP_LOSS_PCT)
        take_profit_price = eth_price * (1 + TAKE_PROFIT_PCT) if signal == "buy" else eth_price * (1 - TAKE_PROFIT_PCT)
        stop_loss_price = round_to_tick_size(stop_loss_price, tick_size)
        take_profit_price = round_to_tick_size(take_profit_price, tick_size)
        logger.info(f"Stop Loss Fiyatı: {stop_loss_price:.2f}, Take Profit Fiyatı: {take_profit_price:.2f} (tickSize={tick_size})")
        
        # Fiyat kontrolü
        if stop_loss_price <= 0 or take_profit_price <= 0:
            logger.error("Geçersiz stop-loss/take-profit fiyatı")
            return {"success": False, "error": "Geçersiz fiyat"}
        
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
        logger.info(f"Headers: {headers}")
        logger.info(f"Sipariş verisi: {order_data}")
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()
        logger.info(f"Pozisyon açma yanıtı: {data}")
        
        if data.get('code') != '200000':
            logger.error(f"Pozisyon açılamadı: {data.get('msg', 'Bilinmeyen hata')}")
            return {"success": False, "error": data.get('msg', 'Bilinmeyen hata')}
        
        order_id = data.get('data', {}).get('orderId')
        logger.info(f"Pozisyon başarıyla açıldı! Sipariş ID: {order_id}")
        
        # Stop-loss ve take-profit emri
        st_order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": "sell" if signal == "buy" else "buy",
            "symbol": SYMBOL,
            "type": "market",
            "size": size,
            "triggerStopDownPrice": stop_loss_price,
            "triggerStopUpPrice": take_profit_price,
            "stopPriceType": "TP"
        }
        
        st_url = "https://api-futures.kucoin.com/api/v1/st-orders"
        st_payload = f"POST/api/v1/st-orders{json.dumps(st_order_data)}"
        headers = signer.headers(st_payload)
        logger.info(f"Stop-loss/take-profit isteği gönderiliyor: {st_order_data}")
        st_response = requests.post(st_url, headers=headers, json=st_order_data)
        st_data = st_response.json()
        logger.info(f"Stop-loss/take-profit sipariş yanıtı: {st_data}")
        
        if st_data.get('code') == '200000':
            st_order_id = st_data.get('data', {}).get('orderId')
            logger.info(f"Stop-loss ve take-profit başarıyla ayarlandı, Order ID: {st_order_id}")
            stop_orders = check_stop_orders()
            if stop_orders:
                logger.info(f"Aktif stop emirleri bulundu: {stop_orders}")
            else:
                logger.warning("Aktif stop emri bulunamadı, emir oluşturulmamış olabilir.")
            
            await send_telegram_message(
                f"📈 Yeni Pozisyon Açıldı ({SYMBOL})\n"
                f"Yön: {'Long' if signal == 'buy' else 'Short'}\n"
                f"Giriş Fiyatı: {eth_price:.2f} USDT\n"
                f"Kontrat: {size}\n"
                f"Kaldıraç: {leverage}x\n"
                f"Pozisyon Değeri: {position_value:.2f} USDT\n"
                f"Stop Loss: {stop_loss_price:.2f} USDT\n"
                f"Take Profit: {take_profit_price:.2f} USDT\n"
                f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            return {"success": True, "orderId": order_id}
        else:
            logger.error(f"Stop-loss/take-profit ayarlanamadı: {st_data.get('msg', 'Bilinmeyen hata')}")
            return {"success": False, "error": f"Stop-loss/take-profit ayarlanamadı: {st_data.get('msg', 'Bilinmeyen hata')}"}
    
    except Exception as e:
        logger.error(f"Pozisyon açma hatası: {str(e)}")
        return {"success": False, "error": str(e)}

# Sinyal alma fonksiyonları (Basitleştirilmiş, varsayımsal)
def calculate_indicators():
    return {"rsi": 50, "macd": 0}  # Örnek

def run_deepsearch():
    return {"trend": "neutral"}  # Örnek

def get_grok_signal(indicators, deepsearch_result):
    # Basit bir sinyal mantığı
    if indicators["rsi"] > 70 and deepsearch_result["trend"] == "bullish":
        return "buy"
    elif indicators["rsi"] < 30 and deepsearch_result["trend"] == "bearish":
        return "sell"
    else:
        return "bekle"

# Ana döngü
async def main():
    last_position = None
    while True:
        try:
            position_response = check_positions()
            if position_response["exists"]:
                position = position_response
                logger.info(f"Açık pozisyon: {position['side']}, Giriş: {position['entry_price']}, PnL: {position['pnl']}")
                
                # Fiyatı al ve SL/TP kontrolü yap
                current_price = get_eth_price()
                if not current_price:
                    logger.error("Fiyat alınamadı, kontrol yapılamadı.")
                    time.sleep(60)
                    continue
                
                entry_price = position["entry_price"]
                side = position["side"]
                stop_loss_price = entry_price * (1 - STOP_LOSS_PCT) if side == "long" else entry_price * (1 + STOP_LOSS_PCT)
                take_profit_price = entry_price * (1 + TAKE_PROFIT_PCT) if side == "long" else entry_price * (1 - TAKE_PROFIT_PCT)
                
                # SL/TP seviyesine ulaşıldı mı?
                should_close = False
                close_reason = None
                if side == "long" and current_price <= stop_loss_price:
                    should_close = True
                    close_reason = "Stop-Loss"
                elif side == "long" and current_price >= take_profit_price:
                    should_close = True
                    close_reason = "Take-Profit"
                elif side == "short" and current_price >= stop_loss_price:
                    should_close = True
                    close_reason = "Stop-Loss"
                elif side == "short" and current_price <= take_profit_price:
                    should_close = True
                    close_reason = "Take-Profit"
                
                if should_close:
                    # 1. Tüm emirleri iptal et
                    cancel_success = await cancel_all_orders(SYMBOL)
                    if cancel_success:
                        logger.info("Tüm açık emir iptal edildi.")
                    else:
                        logger.warning("Açık emir iptal edilemedi, devam ediliyor.")
                    
                    # 2. Pozisyonu kapat
                    close_success = await close_position()
                    if close_success:
                        logger.info(f"Pozisyon kapandı ({close_reason}): {side}, Giriş: {entry_price}, Kapanış: {current_price}")
                        await send_telegram_message(
                            f"📉 Pozisyon Kapatıldı ({SYMBOL})\n"
                            f"Sebep: {close_reason}\n"
                            f"Yön: {side}\n"
                            f"Giriş Fiyatı: {entry_price:.2f} USDT\n"
                            f"Kapanış Fiyatı: {current_price:.2f} USDT\n"
                            f"PnL: {position['pnl']:.2f} USDT\n"
                            f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        )
                    else:
                        logger.error("Pozisyon kapatılamadı.")
                        time.sleep(60)
                        continue
                
                last_position = position
                time.sleep(60)
                continue
            
            # Pozisyon kapanmışsa kontrol et
            if last_position and last_position["exists"]:
                last_entry_price = last_position["entry_price"]
                last_side = last_position["side"]
                last_position = None  # Pozisyon kapandı, sıfırlıyoruz
            
            usdt_balance, position_margin = check_usdm_balance()
            if usdt_balance is None or position_margin is None:
                logger.error("Bakiye alınamadı, devam ediliyor.")
                time.sleep(60)
                continue
            logger.info(f"Bakiye: {usdt_balance:.2f} USDT, Pozisyon Margin: {position_margin:.2f} USDT")
            
            if position_response["exists"]:
                logger.info("Açık pozisyon var, yeni pozisyon açılmayacak.")
                time.sleep(60)
                continue
            
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
            
            result = await open_position(signal, usdt_balance, position_margin)
            if result.get("success"):
                logger.info("Pozisyon açıldı, bekleniyor")
            else:
                logger.error(f"Pozisyon açma başarısız: {result.get('error')}")
            time.sleep(60)
        except Exception as e:
            logger.error(f"Döngü hatası: {str(e)}")
            time.sleep(60)

# Botu çalıştır
if __name__ == "__main__":
    asyncio.run(main())
