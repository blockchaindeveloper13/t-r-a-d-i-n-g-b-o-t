import base64
import hashlib
import hmac
import time
import http.client
import json
import logging

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

# Test fonksiyonu
def test_endpoint(domain, endpoint, params):
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        conn = http.client.HTTPSConnection(domain)
        payload = "GET" + endpoint + params
        headers = signer.headers(payload)
        logger.info(f"Domain: {domain}, Endpoint: {endpoint}, Headers: {headers}")
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        logger.info(f"API yanıtı: {data}")
        if data['code'] == '200000':
            logger.info(f"Bağlantı başarılı! Yanıt: {data['data']}")
        else:
            logger.error(f"Bağlantı başarısız: {data.get('msg', 'Bilinmeyen hata')}")
        return data
    except Exception as e:
        logger.error(f"Test hatası: {str(e)}")
        return {"error": str(e)}

# Test endpoint’leri (v1)
endpoints = [
    {"domain": "api-futures.kucoin.com", "endpoint": "/api/v1/position?symbol=ETHUSDTM", "params": "symbol=ETHUSDTM"},
    {"domain": "api-futures.kucoin.com", "endpoint": "/api/v1/account-overview-all", "params": ""},
    {"domain": "api-futures.kucoin.com", "endpoint": "/api/v1/ticker?symbol=ETHUSDTM", "params": "symbol=ETHUSDTM"},
    {"domain": "api-futures.kucoin.com", "endpoint": "/api/v1/contracts/active", "params": ""},
    {"domain": "api.kucoin.com", "endpoint": "/api/v1/accounts", "params": ""}
]

if __name__ == "__main__":
    for test in endpoints:
        logger.info(f"Testing: {test['domain']} {test['endpoint']}")
        test_endpoint(test['domain'], test['endpoint'], test['params'])
