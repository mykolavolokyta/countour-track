import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'contour.db'))
APP_ORIGIN = os.getenv('APP_ORIGIN', 'http://localhost:3000').rstrip('/')
COOKIE_SECURE = APP_ORIGIN.startswith('https://')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')
MORALIS_API_KEY = os.getenv('MORALIS_API_KEY', '')
# Server-owned endpoints; never accept arbitrary RPC URLs from a source form.
BSC_RPC_URLS = tuple(url.strip() for url in os.getenv(
    'BSC_RPC_URLS', 'https://bsc-dataseed.bnbchain.org,https://bsc-dataseed-public.bnbchain.org'
).split(',') if url.strip())
COINGECKO_DEMO_API_KEY = os.getenv('COINGECKO_DEMO_API_KEY', '')

# OIDC credentials stay on the server. Redirect URLs derive only from APP_ORIGIN.
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
TELEGRAM_CLIENT_ID = os.getenv('TELEGRAM_CLIENT_ID', '')
TELEGRAM_CLIENT_SECRET = os.getenv('TELEGRAM_CLIENT_SECRET', '')
ALLOW_REGISTRATION = os.getenv('ALLOW_REGISTRATION', 'true').lower() == 'true'
