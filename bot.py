import base64
import hashlib
import hmac
import time
import http.client
import json
import logging
import requests

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

# Test fonksiyonu (GET ve POST için)
def test_endpoint(domain, endpoint, params, method="GET", data=None):
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = f"https://{domain}{endpoint}"
        payload = method + endpoint + (params if method == "GET" else (json.dumps(data) if data else ""))
        headers = signer.headers(payload)
        logger.info(f"Domain: {domain}, Endpoint: {endpoint}, Method: {method}, Headers: {headers}")
        
        if method == "GET":
            response = requests.request('get', url, headers=headers)
        else:  # POST
            response = requests.request('post', url, headers=headers, data=json.dumps(data) if data else None)
        
        logger.info(f"Status Code: {response.status_code}")
        data = response.json()
        logger.info(f"API yanıtı: {data}")
        if data.get('code') == '200000':
            logger.info(f"Bağlantı başarılı! Yanıt: {data.get('data')}")
        else:
            logger.error(f"Bağlantı başarısız: {data.get('msg', 'Bilinmeyen hata')}")
        return data
    except Exception as e:
        logger.error(f"Test hatası: {str(e)}")
        return {"error": str(e)}

# Test endpoint’leri
endpoints = [
    {
        "domain": "api-futures.kucoin.com",
        "endpoint": "/api/v1/position?symbol=ETHUSDTM",
        "params": "symbol=ETHUSDTM",
        "method": "GET",
        "data": None
    },
    {
        "domain": "api-futures.kucoin.com",
        "endpoint": "/api/v1/deposit-address",
        "params": "",
        "method": "POST",
        "data": {"currency": "USDT"}
    },
    {
        "domain": "api-futures.kucoin.com",
        "endpoint": "/api/v1/contracts/active",
        "params": "",
        "method": "GET",
        "data": None
    }
]

if __name__ == "__main__":
    for test in endpoints:
        logger.info(f"Testing: {test['domain']} {test['endpoint']} ({test['method']})")
        test_endpoint(test['domain'], test['endpoint'], test['params'], test['method'], test['data'])
