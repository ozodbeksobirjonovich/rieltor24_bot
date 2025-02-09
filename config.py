import os
from dotenv import load_dotenv

load_dotenv()  # .env faylidan o'zgaruvchilarni yuklaydi

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Foydalanuvchi va guruhlar ro'yxatlari (virgulla bilan ajratilgan)
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(',') if x.strip()]
SOURCE_GROUPS = [int(x) for x in os.getenv("SOURCE_GROUPS", "").split(',') if x.strip()]
TARGET_GROUPS = [int(x) for x in os.getenv("TARGET_GROUPS", "").split(',') if x.strip()]

# Forwarding parametrlar
FORWARD_INTERVAL = int(os.getenv("FORWARD_INTERVAL", "30"))
BOOST_EVERY_N = int(os.getenv("BOOST_EVERY_N", "5"))