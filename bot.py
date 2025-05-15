import time
import logging
import requests
import base64
import hashlib
import hmac
import json
import uuid

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# API bilgileri
KUCOIN_API_KEY = "6825e85e61d4190001723c42"
KUCOIN_API_SECRET = "d1d22a52-876f-43ea-a38e-7c6918dca081"
KUCOIN_API_PASSPHRASE = "123456789"

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

# Fonlama oranı alma
def get_funding_rate(symbol="ETHUSDTM"):
    try:
        url = f"https://api-futures.kucoin.com/api/v1/funding-rate/{symbol}/current"
        response = requests.get(url)
        data = response.json()
        logger.info(f"Funding Rate yanıtı: {data}")
        
        if data.get('code') == '200000':
            funding_data = data.get('data', {})
            logger.info(f"*** {symbol} Fonlama Oranı ***")
            logger.info(f"Fonlama Oranı: {funding_data.get('value')*100:.4f}%")
            logger.info(f"Tahmini Oran: {funding_data.get('predictedValue')*100:.4f}%")
            return funding_data
        else:
            logger.error(f"Fonlama oranı alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Fonlama oranı hatası: {str(e)}")
        return None

# Margin modu kontrol
def check_margin_mode(symbol="ETHUSDTM"):
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/margin-mode?symbol={symbol}"
        payload = f"GET/api/v1/margin-mode?symbol={symbol}"
        headers = signer.headers(payload)
        logger.info(f"Headers (margin-mode): {headers}")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Margin Mode yanıtı: {data}")
        
        if data.get('code') == '200000':
            margin_mode = data.get('data', {}).get('marginMode', 'Bilinmeyen')
            logger.info(f"*** {symbol} Margin Modu: {margin_mode} ***")
            return margin_mode
        else:
            logger.error(f"Margin modu kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Margin modu hatası: {str(e)}")
        return None

# Margin modu değiştirme
def switch_margin_mode(symbol="ETHUSDTM", mode="ISOLATED"):
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/switch-margin-mode"
        order_data = {"symbol": symbol, "marginMode": mode}
        payload = "POST" + "/api/v1/switch-margin-mode" + json.dumps(order_data)
        headers = signer.headers(payload)
        logger.info(f"Headers (switch-margin-mode): {headers}")
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()
        logger.info(f"Switch Margin Mode yanıtı: {data}")
        
        if data.get('code') == '200000':
            logger.info(f"*** {symbol} Margin Modu {mode} olarak değiştirildi ***")
            return True
        else:
            logger.error(f"Margin modu değiştirme başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return False
    except Exception as e:
        logger.error(f"Margin modu değiştirme hatası: {str(e)}")
        return False

# Bakiye kontrol (USD-M)
def check_usdm_balance():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/account-overview?currency=USDT"
        payload = "GET" + "/api/v1/account-overview?currency=USDT"
        headers = signer.headers(payload)
        logger.info(f"Headers (usdm): {headers}")
        response = requests.request('get', url, headers=headers)
        data = response.json()
        logger.info(f"API yanıtı (usdm): {data}")
        
        if data.get('code') == '200000':
            usdt_balance = data.get('data', {}).get('availableBalance', 0)
            logger.info(f"*** USD-M USDT Bakiyesi Bulundu! Kullanılabilir: {usdt_balance} ***")
            return usdt_balance
        else:
            logger.error(f"USD-M bakiye kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Hata (usdm): {str(e)}")
        return None

# ETH fiyatını alma
def get_eth_price():
    try:
        url = "https://api-futures.kucoin.com/api/v1/ticker?symbol=ETHUSDTM"
        response = requests.get(url)
        data = response.json()
        if data.get('code') == '200000':
            price = float(data.get('data', {}).get('price', 0))
            logger.info(f"ETH/USDTM Fiyatı: {price} USDT")
            return price
        else:
            logger.error(f"Fiyat alınamadı: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Fiyat alma hatası: {str(e)}")
        return None

# Pozisyon açma
def open_position():
    try:
        # Fonlama oranı
        funding_rate = get_funding_rate()
        if not funding_rate:
            logger.warning("Fonlama oranı alınamadı, devam ediliyor.")
        
        # Bakiye kontrolü
        usdt_balance = check_usdm_balance()
        if usdt_balance is None or usdt_balance < 11:
            logger.error("Yetersiz USDT bakiyesi veya bakiye alınamadı.")
            return {"error": "Yetersiz bakiye"}
        
        # Margin modu kontrol
        margin_mode = check_margin_mode()
        if margin_mode is None:
            logger.warning("Margin modu alınamadı, ISOLATED deneniyor.")
            margin_mode = "ISOLATED"
        
        if margin_mode != "ISOLATED":
            logger.info(f"Margin modu {margin_mode}, ISOLATED moduna geçiliyor.")
            if not switch_margin_mode(mode="ISOLATED"):
                logger.error("Margin modu ISOLATED yapılamadı.")
                return {"error": "Margin modu değiştirilemedi"}
        
        # ETH fiyatını al
        eth_price = get_eth_price()
        if not eth_price:
            logger.error("ETH fiyatı alınamadı, pozisyon açılamıyor.")
            return {"error": "Fiyat alınamadı"}
        
        # Pozisyon parametreleri
        usdt_amount = 11
        leverage = 3
        total_value = usdt_amount * leverage
        multiplier = 0.001
        size = int(total_value / (eth_price * multiplier))
        logger.info(f"Pozisyon: {size} kontrat (Toplam Değer: {total_value} USDT, Fiyat: {eth_price} USDT)")
        
        # Sipariş verisi
        order_data = {
            "clientOid": str(uuid.uuid4()),
            "side": "buy",
            "symbol": "ETHUSDTM",
            "leverage": str(leverage),
            "type": "market",
            "size": size,
            "marginMode": "ISOLATED"
        }
        
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/orders"
        payload = "POST" + "/api/v1/orders" + json.dumps(order_data)
        headers = signer.headers(payload)
        logger.info(f"Headers: {headers}")
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()
        logger.info(f"Pozisyon açma yanıtı: {data}")
        
        if data.get('code') == '200000':
            logger.info(f"Pozisyon başarıyla açıldı! Sipariş ID: {data.get('data', {}).get('orderId')}")
        else:
            logger.error(f"Pozisyon açılamadı: {data.get('msg', 'Bilinmeyen hata')}")
        
        return data
    except Exception as e:
        logger.error(f"Pozisyon açma hatası: {str(e)}")
        return {"error": str(e)}

# Ana döngü
if __name__ == "__main__":
    while True:
        try:
            result = open_position()
            logger.info(f"Sonuç: {result}")
            if result.get('code') == '200000':
                logger.info("Pozisyon açıldı, bot durduruluyor.")
                break
            time.sleep(60)  # Hata durumunda 60 saniye bekle
        except Exception as e:
            logger.error(f"Döngü hatası: {str(e)}")
            time.sleep(60)
