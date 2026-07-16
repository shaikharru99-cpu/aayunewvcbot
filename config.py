# --- TELEGRAM API CONFIGURATION  ---
API_ID = 39800351
API_HASH = "2a6fbe5d5c92adf1b49f9667be3598c3"

# --- BOT CONFIGURATION ---
BOT_TOKEN = "8978249219:AAFz5VrmMNE_yFHeuD6y_vbi9r0BlIh5_UE" 

# --- DATABASE CONFIGURATION ---
MONGO_URL = "mongodb+srv://ArruBhai:NVPavPk34HtQ6RW1@cluster0.hhlnxa9.mongodb.net/?appName=Cluster0"
DB_NAME = "telegram_bot_db223344" 

# --- ASSETS ---
WELCOME_IMAGE = "https://i.ibb.co/ch8W4QmS/ARLTools.png"

# --- LOGGING ---
LOG_CHANNEL = -1004337410413

# --- PERMISSIONS & ADMINS ---
OWNER_ID = 6360979950
ADMIN_IDS = [6360979950]

# Try importing from external config to respect any external environment values
try:
    import config
    API_ID = getattr(config, 'API_ID', API_ID)
    API_HASH = getattr(config, 'API_HASH', API_HASH)
    BOT_TOKEN = getattr(config, 'BOT_TOKEN', BOT_TOKEN)
    MONGO_URL = getattr(config, 'MONGO_URL', MONGO_URL)
    DB_NAME = getattr(config, 'DB_NAME', DB_NAME)
    WELCOME_IMAGE = getattr(config, 'WELCOME_IMAGE', WELCOME_IMAGE)
    LOG_CHANNEL = getattr(config, 'LOG_CHANNEL', LOG_CHANNEL)
    OWNER_ID = getattr(config, 'OWNER_ID', OWNER_ID)
    ADMIN_IDS = getattr(config, 'ADMIN_IDS', ADMIN_IDS)
except ImportError:
    pass

ENV_ADMINS = ADMIN_IDS
