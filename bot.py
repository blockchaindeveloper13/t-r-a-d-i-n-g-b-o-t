import http.client
import json
import time
import logging

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# API bilgileri
KUCOIN_API_KEY = "68251ed54985e300012f8549"
KUCOIN_API_PASSPHRASE = "vedat1453"

# Test isteği (KC-API-SIGN olmadan)
def test_api_no_sign():
    try:
        conn = http.client.HTTPSConnection("api-futures.kucoin.com")
        endpoint = "/api/v1/account-overview?currency=USDT"
        params = "currency=USDT"
        timestamp = str(int(time.time() * 1000))
        headers = {
            'KC-API-KEY': KUCOIN_API_KEY,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': KUCOIN_API_PASSPHRASE
        }
        logger.info(f"Headers: {headers}")
        conn.request("GET", endpoint, '', headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        logger.info(f"API yanıtı: {data}")
        if data['code'] == '200000':
            logger.info(f"Bağlantı başarılı! Bakiye: {data['data']['accountEquity']} USDT")
        else:
            logger.error(f"Bağlantı başarısız: {data.get('msg', 'Bilinmeyen hata')}")
        return data
    except Exception as e:
        logger.error(f"Test hatası: {str(e)}")
        return {"error": str(e)}

if __name__ == "__main__":
    test_api_no_sign()
