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

# Bakiye kontrol (account-overview-all)
def check_balance():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/account-overview-all"
        payload = "GET" + "/api/v1/account-overview-all"
        headers = signer.headers(payload)
        logger.info(f"Headers (overview): {headers}")
        response = requests.request('get', url, headers=headers)
        data = response.json()
        logger.info(f"API yanıtı (overview): {data}")
        
        if data.get('code') == '200000':
            accounts = data.get('data', {}).get('accounts', [])
            usdt_balance = None
            for account in accounts:
                currency = account.get('currency', 'Bilinmeyen')
                equity = account.get('accountEquity', 0)
                available = account.get('availableBalance', 0)
                logger.info(f"Hesap: {account.get('accountName')} | Para Birimi: {currency} | Toplam Bakiye: {equity} | Kullanılabilir: {available}")
                
                if currency == 'USDT':
                    logger.info(f"*** USDT Bakiyesi Bulundu! Toplam: {equity} | Kullanılabilir: {available} ***")
                    usdt_balance = available
            
            if usdt_balance is None:
                logger.warning("USDT bakiyesi bulunamadı (overview).")
            
            return usdt_balance
        else:
            logger.error(f"Bakiye kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Hata (overview): {str(e)}")
        return None

# Bakiye kontrol (account-overview, USD-M için)
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

# Çekilebilir margin kontrol
def check_max_withdraw_margin():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/max-withdraw-margin"
        payload = "GET" + "/api/v1/max-withdraw-margin"
        headers = signer.headers(payload)
        logger.info(f"Headers (max-withdraw-margin): {headers}")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Max Withdraw Margin yanıtı: {data}")
        
        if data.get('code') == '200000':
            usdt_balance = data.get('data', {}).get('availableBalance', 0)
            logger.info(f"*** Çekilebilir USDT Bakiyesi: {usdt_balance} ***")
            return usdt_balance
        else:
            logger.error(f"Max Withdraw Margin başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Max Withdraw Margin hatası: {str(e)}")
        return None

# Ledger kontrol
def check_ledger():
    try:
        signer = KcSigner(KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
        url = "https://api-futures.kucoin.com/api/v1/account-ledgers?currency=USDT"
        payload = "GET" + "/api/v1/account-ledgers?currency=USDT"
        headers = signer.headers(payload)
        logger.info(f"Headers (ledger): {headers}")
        response = requests.get(url, headers=headers)
        data = response.json()
        logger.info(f"Ledger yanıtı: {data}")
        
        if data.get('code') == '200000':
            ledgers = data.get('data', {}).get('items', [])
            usdt_balance = None
            for ledger in ledgers:
                amount = ledger.get('amount', 0)
                context = ledger.get('context', {})
                biz_type = ledger.get('bizType', 'Bilinmeyen')
                logger.info(f"İşlem: {biz_type} | Miktar: {amount} | Detay: {context}")
                if biz_type == 'TRANSFER' and amount > 0:
                    usdt_balance = amount
                    logger.info(f"*** USDT Transfer Bulundu! Miktar: {amount} ***")
            
            if not ledgers:
                logger.warning("Hiç ledger kaydı bulunamadı.")
            if usdt_balance is None:
                logger.warning("USDT transfer kaydı bulunamadı.")
            
            return usdt_balance
        else:
            logger.error(f"Ledger kontrolü başarısız: {data.get('msg', 'Bilinmeyen hata')}")
            return None
    except Exception as e:
        logger.error(f"Ledger hatası: {str(e)}")
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
        
        # Bakiye kontrolü (USD-M öncelikli)
        usdt_balance = check_usdm_balance()
        if usdt_balance is None or usdt_balance < 11:
            logger.warning("account-overview ile USDT bulunamadı, account-overview-all kontrol ediliyor.")
            usdt_balance = check_balance()
        
        if usdt_balance is None or usdt_balance < 11:
            logger.warning("account-overview-all ile USDT bulunamadı, max-withdraw-margin kontrol ediliyor.")
            usdt_balance = check_max_withdraw_margin()
        
        if usdt_balance is None or usdt_balance < 11:
            logger.warning("max-withdraw-margin ile USDT bulunamadı, ledger kontrol ediliyor.")
            usdt_balance = check_ledger()
        
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
            "marginMode": "CROSS"
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
