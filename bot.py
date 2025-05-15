import http.client
import hmac
import hashlib
import base64
import time
import json
import logging

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# API bilgileri (Manuel olarak giriyoruz)
KUCOIN_API_KEY = "68251ed54985e300012f8549"
KUCOIN_API_SECRET = "349d4bcd-95c7-45c5-805a-c1ce1d020dc6"
KUCOIN_API_PASSPHRASE = "vedat1453"  # Passphrase'i kontrol et, doğru olduğundan emin ol

# KuCoin API imzalama
def generate_signature(endpoint, method, params, api_secret):
    timestamp = str(int(time.time() * 1000))
    if method == "GET":
        str_to_sign = timestamp + method + endpoint + params
    else:  # POST
        str_to_sign = timestamp + method + endpoint + json.dumps(params)
    logger.info(f"Signature string: {str_to_sign}")
    sign = hmac.new(api_secret.encode('utf-8'), str_to_sign.encode('utf-8'), hashlib.sha256).digest()
    return base64.b64encode(sign).decode('utf-8'), timestamp

# Manuel API testi
def manual_api_test():
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
    manual_api_test()
