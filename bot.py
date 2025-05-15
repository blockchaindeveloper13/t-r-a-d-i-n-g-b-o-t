import time
import logging
import requests
import base64
import hashlib
import hmac
import json

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

# Bakiye kontrol fonksiyonu
def check_balance():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/account-overview-all"
        payload = "GET" + "/api/v1/account-overview-all"
        headers = signer.headers(payload)
        logger.info(f"Headers: {headers}")
        response = requests.request('get', url, headers=headers)
        data = response.json()
        logger.info(f"API yanıtı: {data}")
        
        if data.get('code') == '200000':
            accounts = data.get('data', {}).get('accounts', [])
            summary = data.get('data', {}).get('summary', {})
            logger.info(f"Bakiye kontrolü başarılı! Özet: {summary}")
            
            # Tüm hesapları logla
            for account in accounts:
                currency = account.get('currency', 'Bilinmeyen')
                equity = account.get('accountEquity', 0)
                available = account.get('availableBalance', 0)
                logger.info(f"Hesap: {account.get('accountName')} | Para Birimi: {currency} | Toplam Bakiye: {equity} | Kullanılabilir: {available}")
                
                # USDT’yi özellikle vurgula
                if currency == 'USDT':
                    logger.info(f"*** USDT Bakiyesi Bulundu! Toplam: {equity} | Kullanılabilir: {available} ***")
            
            # Eğer USDT bulunmadıysa
            if not any(account.get('currency') == 'USDT' for account in accounts):
                logger.warning("USDT bakiyesi bulunamadı. Hesapta USDT olmayabilir.")
            
            return data
        else:
            logger.error(f"Bakiye kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return data
    except Exception as e:
        logger.error(f"Hata: {str(e)}")
        return {"error": str(e)}

# Ana döngü
if __name__ == "__main__":
    while True:
        try:
            result = check_balance()
            logger.info(f"Sonuç: {result}")
            time.sleep(60)  # Her 60 saniyede bir kontrol
        except Exception as e:
            logger.error(f"Döngü hatası: {str(e)}")
            time.sleep(60)  # Hata durumunda 60 saniye bekle
