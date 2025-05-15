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

# Pozisyon ve bakiye kontrol
def check_positions(currency="USDT"):
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://api-futures.kucoin.com/api/v1/positions?currency={currency}"
        payload = f"GET/api/v1/positions?currency={currency}"
        headers = signer.headers(payload)
        logger.info(f"Headers: {headers}")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"API yanıtı: {data}")
        
        if data.get('code') == '200000':
            positions = data.get('data', [])
            usdt_balance = None
            for position in positions:
                symbol = position.get('symbol', 'Bilinmeyen')
                settle_currency = position.get('settleCurrency', 'Bilinmeyen')
                pos_margin = position.get('posMargin', 0)
                logger.info(f"Sembol: {symbol} | Para Birimi: {settle_currency} | Pozisyon Margin: {pos_margin}")
                
                if settle_currency == 'USDT':
                    logger.info(f"*** USDT Pozisyonu Bulundu! Margin: {pos_margin} ***")
                    usdt_balance = pos_margin
            
            if not positions:
                logger.warning("Hiç pozisyon bulunamadı.")
            if usdt_balance is None:
                logger.warning("USDT pozisyonu veya margin bulunamadı.")
            
            return usdt_balance
        else:
            logger.error(f"Pozisyon kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Hata: {str(e)}")
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
        
        # Pozisyon ve bakiye kontrol
        usdt_balance = check_positions()
        if usdt_balance is None or usdt_balance < 11:
            logger.error("Yetersiz USDT bakiyesi veya bakiye alınamadı.")
            return {"error": "Yetersiz bakiye"}
        
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
            "marginMode": "CROSS"  # Cross margin, dökümana uygun
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

# Ana program
if __name__ == "__main__":
    try:
        result = open_position()
        logger.info(f"Sonuç: {result}")
        if result.get('code') == '200000':
            logger.info("Pozisyon açıldı, bot durduruluyor.")
        else:
            logger.error("Pozisyon açılamadı, lütfen logları kontrol edin.")
    except Exception as e:
        logger.error(f"Başlatma hatası: {str(e)}")
