import asyncio
import logging
import os
import sys
import traceback
import random
import json
import re
import signal
from datetime import datetime, timedelta

# --- HIDE UNRAISABLE ASYNCIO TRACEBACKS ---
def custom_unraisablehook(unraisable):
    err_msg = str(unraisable.exc_value)
    if any(x in err_msg for x in ["GeneratorExit", "Task was destroyed", "coroutine ignored"]):
        return
    sys.__unraisablehook__(unraisable)

sys.unraisablehook = custom_unraisablehook

# Third-party imports
from telethon import TelegramClient, events, functions, types, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError,
    PasswordHashInvalidError, PhoneNumberInvalidError,
    UserAlreadyParticipantError, InviteHashExpiredError,
    FloodWaitError, ChannelPrivateError, UserDeactivatedError,
    UserBannedInChannelError, GroupCallInvalidError, MessageIdInvalidError,
    AuthKeyUnregisteredError
)
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, GetFullChatRequest
from telethon.tl.functions.phone import JoinGroupCallRequest
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged
import motor.motor_asyncio

# --- IMPORT CONFIGURATION ---
try:
    from config import API_ID, API_HASH, BOT_TOKEN, MONGO_URL, LOG_CHANNEL, ADMIN_IDS as ENV_ADMINS, OWNER_ID
except ImportError:
    print("❌ Critical Error: config.py not found or missing OWNER_ID! Please create it and define OWNER_ID.")
    sys.exit(1)

try:
    from config import WELCOME_IMAGE
except ImportError:
    WELCOME_IMAGE = "https://i.ibb.co/0yZT5SFv/Auto-Rt-Lv-Tools.png"

# --- CONSTANTS & EMOJI UI GRID ---
EMOJIS = ["👍", "❤️", "🔥", "🎉", "😍", "🤩", "⚡️", "💯", "😎", "👏", "🥳", "🚀", "🤡", "🙏", "👀", "✍️"]
DELAY_RANGE = (5, 15)

def build_emoji_keyboard(selected_emojis, mode, extra=""):
    """ Builds a dynamic 4x4 inline keyboard for multiple emoji selection. """
    buttons = []
    row = []
    prefix = f"emj_{mode}_{extra}_" if extra else f"emj_{mode}_"
    
    for em in EMOJIS:
        text = f"{em} ✅" if em in selected_emojis else em
        row.append(Button.inline(text, prefix + em))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    
    conf_data = f"conf_{mode}_{extra}" if extra else f"conf_{mode}"
    rand_data = f"rand_{mode}_{extra}" if extra else f"rand_{mode}"
    cancel_data = f"t_menu_{extra}" if mode == "tgt" else "reacts_menu"
    
    buttons.append([
        Button.inline("✅ CONFIRM", conf_data),
        Button.inline("🎲 RANDOM ALL", rand_data)
    ])
    buttons.append([Button.inline("❌ CANCEL", cancel_data)])
    return buttons

# --- ADVANCED LOGGING SETUP ---
class HumanReadableFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        
        if "[VC DEBUG]" in msg:
            return True
            
        if any(x in msg for x in ["Task was destroyed", "GeneratorExit", "coroutine ignored", "Exception ignored in"]):
            return False
            
        prefix = ""
        name_match = re.search(r"for (.*?) \(\d+\)", msg)
        if name_match: prefix = f"👤 [{name_match.group(1)}] ➜ "
        else:
            name_match2 = re.search(r"userbot (.*?):", msg)
            if name_match2: prefix = f"👤 [{name_match2.group(1)}] ➜ "
            else:
                bot_match = re.search(r"bot (\d+)", msg, re.IGNORECASE)
                if bot_match: prefix = f"👤 [Bot ID: {bot_match.group(1)}] ➜ "

        if "used under two different IP addresses" in msg:
            record.msg = f"{prefix}🚫 [SESSION REVOKED] Telegram killed the session due to Heroku's ghost server overlap. Please re-add this bot."
            record.args = ()
        elif "Connection reset by peer" in msg:
            record.msg = f"{prefix}🔌 [TCP RESET] Telegram forcefully dropped the connection. Auto-reconnecting..."
            record.args = ()
        elif "RpcCallFailError" in msg:
            record.msg = f"{prefix}📡 [TELEGRAM INTERNAL] Telegram servers are failing to respond. Retrying later..."
            record.args = ()
        elif "Flood wait" in msg.lower() or "floodwait" in msg.lower():
            sec_match = re.search(r"(\d+)s", msg)
            secs = sec_match.group(1) if sec_match else "several"
            if "on react" in msg.lower(): return False 
            record.msg = f"{prefix}⏳ [RATE LIMITED] Telegram paused this bot for {secs} seconds to prevent spam."
            record.args = ()
        elif "Connection issue for" in msg and "Timeout" in msg:
            record.msg = f"{prefix}⌛ [TIMEOUT] Failed to connect to Telegram servers. Will retry."
            record.args = ()
        elif prefix: 
            record.msg = f"{prefix}{msg}"
            record.args = ()
            
        return True

class EmojiFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno >= logging.CRITICAL: lvl = "💥 [CRITICAL]"
        elif record.levelno >= logging.ERROR: lvl = "🔴 [ERROR]"
        elif record.levelno >= logging.WARNING: lvl = "🟡 [WARNING]"
        elif record.levelno >= logging.INFO: lvl = "🟢 [INFO]"
        else: lvl = "⚪ [DEBUG]"
        formatter = logging.Formatter(f"{lvl} %(asctime)s ➜ %(message)s", datefmt="%H:%M:%S")
        return formatter.format(record)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if root_logger.hasHandlers(): root_logger.handlers.clear()

console_handler = logging.StreamHandler()
console_handler.setFormatter(EmojiFormatter())
console_handler.addFilter(HumanReadableFilter())
root_logger.addHandler(console_handler)

logger = logging.getLogger("ManagerBot")
logging.getLogger("telethon").setLevel(logging.ERROR)

# --- DATABASE HANDLER (MongoDB) ---
class Database:
    def __init__(self):
        self.client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
        self.db = self.client['telegram_bot_db223344']
        self.sessions = self.db['sessions']
        self.settings = self.db['settings']

    async def add_session(self, user_id, name, session_string):
        await self.sessions.update_one(
            {"user_id": user_id},
            {"$set": {"name": name, "session_string": session_string, "active": True, "added_at": datetime.now()}},
            upsert=True
        )

    async def get_all_sessions(self):
        cursor = self.sessions.find({"active": True})
        return await cursor.to_list(length=None)

    async def remove_session(self, user_id):
        await self.sessions.delete_one({"user_id": user_id})

    async def get_config(self):
        doc = await self.settings.find_one({"_id": "global_config"})
        if not doc:
            doc = {
                "target_chats": [],
                "target_settings": {}, 
                "admins": [],
                "auto_views_limit": 0,  
                "auto_reacts_limit": 0,
                "auto_join_limit": 0
            }
            await self.settings.insert_one({"_id": "global_config", **doc})
        return doc

    async def update_config(self, key, value):
        await self.settings.update_one(
            {"_id": "global_config"},
            {"$set": {key: value}},
            upsert=True
        )

    async def update_target_settings(self, chat_id, key, value):
        db_key = f"target_settings.{chat_id}.{key}"
        await self.settings.update_one(
            {"_id": "global_config"},
            {"$set": {db_key: value}},
            upsert=True
        )

    async def add_target_chat(self, chat_id, added_by_id):
        await self.settings.update_one(
            {"_id": "global_config"},
            {
                "$addToSet": {"target_chats": chat_id},
                "$set": {f"target_settings.{chat_id}.added_by": added_by_id}
            },
            upsert=True
        )

    async def remove_target_chat(self, chat_id):
        await self.settings.update_one(
            {"_id": "global_config"},
            {"$pull": {"target_chats": chat_id}, "$unset": {f"target_settings.{chat_id}": ""}}
        )

    async def clear_target_chats(self):
        await self.settings.update_one(
            {"_id": "global_config"},
            {"$set": {"target_chats": [], "target_settings": {}}}
        )

    async def add_admin(self, user_id):
        await self.settings.update_one(
            {"_id": "global_config"},
            {"$addToSet": {"admins": user_id}},
            upsert=True
        )

    async def remove_admin(self, user_id):
        await self.settings.update_one(
            {"_id": "global_config"},
            {"$pull": {"admins": user_id}}
        )

db = Database()

# --- GLOBAL STATE ---
active_userbots = {}
login_states = {}
GLOBAL_CONF = {}

API_SEMAPHORE = asyncio.Semaphore(10) 
BANNED_ACCOUNTS = []

async def mark_bot_banned(user_id, name, reason="Banned/Revoked"):
    if user_id in active_userbots:
        client = active_userbots[user_id]
        client._vc_stop_flag = True
        try: await client.disconnect()
        except: pass
        del active_userbots[user_id]
        
    await db.remove_session(user_id)
    if not any(acc['id'] == user_id for acc in BANNED_ACCOUNTS):
        BANNED_ACCOUNTS.append({"id": user_id, "name": name, "reason": reason})
    logger.info(f"🗑️ Cleaned up revoked/banned bot: {name} ({user_id})")

# --- MASTER BOT INSTANCE ---
bot = TelegramClient('master_bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- HELPER FUNCTIONS ---
async def log_to_channel(message):
    if LOG_CHANNEL:
        try:
            await bot.send_message(LOG_CHANNEL, f"**[LOG]** {message}")
        except Exception as e:
            logger.error(f"Failed to log to channel: {e}")

async def refresh_global_config():
    global GLOBAL_CONF
    GLOBAL_CONF = await db.get_config()
    db_admins = GLOBAL_CONF.get("admins", [])
    GLOBAL_CONF['all_admins'] = list(set(ENV_ADMINS + db_admins))
    logger.info(f"⚙️ Config Refreshed. Target Chats Monitored: {len(GLOBAL_CONF.get('target_chats', []))}")

def is_owner(user_id): return user_id == OWNER_ID

def is_admin(user_id): return is_owner(user_id) or user_id in GLOBAL_CONF.get('all_admins', [])

def parse_msg_link(link):
    match = re.search(r't\.me/(?:c/)?([^/]+)/(\d+)', link)
    if match:
        peer_str = match.group(1)
        msg_id = int(match.group(2))
        if peer_str.isdigit() and 'c/' in link:
            peer_str = int("-100" + peer_str)
        return peer_str, msg_id
    return None, None

def get_effective_limit(chat_id, setting_type):
    str_id = str(chat_id)
    target_settings = GLOBAL_CONF.get('target_settings', {})
    chat_conf = target_settings.get(str_id, {})
    specific_val = chat_conf.get(setting_type, -1)
    if specific_val != -1: return int(specific_val)
    key_map = {"view_limit": "auto_views_limit", "react_limit": "auto_reacts_limit", "join_limit": "auto_join_limit"}
    return int(GLOBAL_CONF.get(key_map.get(setting_type), 0))

def get_effective_emoji_list(chat_id):
    str_id = str(chat_id)
    target_settings = GLOBAL_CONF.get('target_settings', {})
    chat_conf = target_settings.get(str_id, {})
    e_spec = chat_conf.get('emoji', [])
    
    if not e_spec or e_spec == "RANDOM": 
        return EMOJIS.copy()
    if isinstance(e_spec, str): 
        return [e_spec]
    if isinstance(e_spec, list):
        return e_spec
    return EMOJIS.copy()

# --- USERBOT LOGIC: VIEWS & REACTIONS ---
async def trigger_single_bot_engagement(client, peer_str, msg_id, action_type, emoji_list, index=0):
    base_delay = random.uniform(0.5, 1.5)
    stagger_delay = index * random.uniform(0.3, 0.8) 
    await asyncio.sleep(base_delay + stagger_delay)
    
    try:
        try:
            if isinstance(peer_str, str) and not peer_str.startswith('-'):
                entity = await client.get_entity(peer_str)
            else:
                entity = await client.get_entity(int(peer_str))
        except ValueError:
            try:
                await client.get_dialogs(limit=500) 
                resolve_id = int(peer_str) if (isinstance(peer_str, str) and peer_str.startswith('-')) or isinstance(peer_str, int) else peer_str
                entity = await client.get_entity(resolve_id)
            except Exception:
                return False
                
        if action_type == "view":
            await client(functions.messages.GetMessagesViewsRequest(peer=entity, id=[msg_id], increment=True))
        elif action_type == "react":
            safe_emojis = emoji_list if (isinstance(emoji_list, list) and emoji_list) else EMOJIS
            react_emoji = random.choice(safe_emojis)
                
            try:
                await client(functions.messages.SendReactionRequest(peer=entity, msg_id=msg_id, reaction=[types.ReactionEmoji(emoticon=react_emoji)]))
            except TypeError:
                await client(functions.messages.SendReactionRequest(peer=entity, msg_id=msg_id, reaction=react_emoji))
                
        return True
    except Exception as e:
        if "Flood" not in str(e):
            pass 
        return False

async def process_one_time_engagement(link, count, action_type, emoji_list=None):
    peer_str, msg_id = parse_msg_link(link)
    if not peer_str or not msg_id: return False, "❌ Invalid Telegram Message Link."

    available_bots = list(active_userbots.values())
    if not available_bots: return False, "❌ No active bots available."

    target_count = min(int(count), len(available_bots))
    selected_bots = random.sample(available_bots, target_count)
    
    if emoji_list == "RANDOM" or not emoji_list:
        emoji_list = EMOJIS.copy()
    elif isinstance(emoji_list, str):
        emoji_list = [emoji_list]
        
    tasks = [trigger_single_bot_engagement(client, peer_str, msg_id, action_type, emoji_list, i) for i, client in enumerate(selected_bots)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    success_count = sum(1 for r in results if r is True)
    action_text = "Views" if action_type == "view" else "Reactions"
    return True, f"✅ Successfully queued **{success_count}** {action_text} for processing."

# --- USERBOT ACTION: JOIN VIA LINK ---
async def join_channel_via_link(client, link):
    try:
        if '+' in link or 'joinchat' in link:
            try:
                hash_val = link.split('+')[-1] if '+' in link else link.split('joinchat/')[-1]
                hash_val = hash_val.replace('/', '')
                await client(ImportChatInviteRequest(hash_val))
                return True, "Joined (Private)"
            except UserAlreadyParticipantError: return True, "Already Joined"
            except InviteHashExpiredError: return False, "Link Expired"
        else:
            username = link.split('/')[-1]
            try:
                await client(JoinChannelRequest(username))
                return True, "Joined (Public)"
            except UserAlreadyParticipantError: return True, "Already Joined"
    except FloodWaitError as e: return False, f"FloodWait ({e.seconds}s)"
    except Exception as e: return False, str(e)[:50]

# --- USERBOT LOGIC: VC & LIVE ---
async def get_active_call_for_bot(client, chat_id):
    """
    🛠️ CRITICAL FIX: Per-Bot Cache Isolation.
    Every bot manages its OWN Voice Chat token, guaranteeing 0% FROZEN_METHOD_INVALID errors.
    """
    if not hasattr(client, 'vc_call_cache'):
        client.vc_call_cache = {}
        
    if chat_id in client.vc_call_cache:
        return client.vc_call_cache[chat_id]

    try:
        try:
            entity = await client.get_entity(chat_id)
        except ValueError:
            try:
                await client.get_dialogs(limit=50)
                entity = await client.get_entity(chat_id)
            except Exception as e:
                return "EntityNotFound"
                
        if isinstance(entity, (types.Channel, types.InputPeerChannel)):
            full_chat = await client(GetFullChannelRequest(entity))
        else:
            chat_id_num = entity.chat_id if hasattr(entity, 'chat_id') else entity.id
            full_chat = await client(GetFullChatRequest(chat_id_num))
            
        call = getattr(full_chat.full_chat, 'call', None)
        if call:
            client.vc_call_cache[chat_id] = call
        return call
    except Exception as e:
        return f"API_Error: {str(e)[:40]}"

async def join_channel_live(client, chat_id):
    if getattr(client, '_vc_stop_flag', False) or not client.is_connected():
        return False, "Bot is offline or stopping"
        
    try:
        call_to_join = await get_active_call_for_bot(client, chat_id)
        if isinstance(call_to_join, str): return False, f"Resolution Failed: {call_to_join}"
        if not call_to_join: return False, "No Active VC"

        try:
            async with API_SEMAPHORE:
                await asyncio.sleep(random.uniform(0.1, 0.3)) 
                
                if not hasattr(client, 'vc_ssrcs'): client.vc_ssrcs = {}
                if chat_id not in client.vc_ssrcs: client.vc_ssrcs[chat_id] = random.randint(100000, 99999999)
                
                ssrc = client.vc_ssrcs[chat_id]
                join_as = await client.get_input_entity(client.me.id)
                payload = {"transport": {"rtcp-mux": True}, "ssrc": ssrc}
                
                await client(JoinGroupCallRequest(
                    call=call_to_join, join_as=join_as,
                    params=types.DataJSON(data=json.dumps(payload)),
                    muted=True, video_stopped=True
                ))
            return True, "Joined live stream successfully"
            
        except Exception as e:
            error_str = str(e).lower()
            if "already in" in error_str or "already joined" in error_str: 
                return True, "Already in the call"
            
            # 🛠️ INSTANT RECOVERY: If the admin restarts the VC, drop the stale cache and wait for next loop to re-fetch
            if any(x in error_str for x in ["group call is invalid", "groupcall_invalid", "frozen", "method_invalid"]):
                if hasattr(client, 'vc_call_cache'):
                    client.vc_call_cache.pop(chat_id, None) 
                return False, "VC object stale or ended"
                
            elif isinstance(e, FloodWaitError) or "flood" in error_str: 
                return False, f"Flood wait error ({getattr(e, 'seconds', 30)}s)"
            elif "rpccallfail" in error_str: 
                return False, "RpcCallFailError" 
            
            if any(x in error_str for x in ["unregistered", "revoked", "deactivated"]):
                await mark_bot_banned(client.me.id, getattr(client.me, 'first_name', 'Unknown'), "Banned during VC Check")
                return False, "Banned"
                
            return False, f"Join error: {str(e)[:50]}"
    except Exception as e:
        return False, f"Error: {str(e)[:50]}"

async def ban_notifier_engine():
    while True:
        await asyncio.sleep(30)
        if BANNED_ACCOUNTS:
            to_notify = BANNED_ACCOUNTS.copy()
            BANNED_ACCOUNTS.clear()
            text = "🚨 **𝗔𝗰𝗰𝗼𝘂𝗻𝘁(𝘀) 𝗥𝗲𝘃𝗼𝗸𝗲𝗱 / 𝗕𝗮𝗻𝗻𝗲𝗱** 🚨\n\n"
            text += "The following bots were terminated by Telegram or encountered IP Conflicts. They have been **automatically removed** from the database:\n\n"
            for idx, acc in enumerate(to_notify, 1): text += f"{idx}. **{acc['name']}** (`{acc['id']}`)\n"
            text += f"\n**Total Removed:** `{len(to_notify)}`"
            notify_list = set([OWNER_ID] + GLOBAL_CONF.get('all_admins', []))
            for admin in notify_list:
                try: await bot.send_message(admin, text)
                except Exception: pass

async def global_vc_manager():
    await asyncio.sleep(5) 
    logger.info("⚙️ Starting Centralized Advanced VC Manager Engine...")
    initialized_targets = set()

    async def process_ping(client, chat_id):
        success, msg = await join_channel_live(client, chat_id)
        if not hasattr(client, 'in_vc'): client.in_vc = {}
        was_in_vc = client.in_vc.get(chat_id, False)
        bot_name = getattr(client.me, 'first_name', 'Unknown')
        
        if success:
            if not was_in_vc: 
                logger.info(f"🎙️ [VC JOIN] 👤 [{bot_name}] ➜ Successfully joined VC in chat {chat_id}")
            client.in_vc[chat_id] = True
            # 🛠️ AGGRESSIVE KEEP-ALIVE: Ping exactly every 15s to perfectly evade Telegram's 30s timeout!
            client.vc_cooldowns[chat_id] = datetime.now() + timedelta(seconds=15)
            
        elif "Flood wait error" in msg:
            sec_match = re.search(r"\((\d+)s\)", msg)
            wait = int(sec_match.group(1)) if sec_match else 15
            client.vc_cooldowns[chat_id] = datetime.now() + timedelta(seconds=wait + 5)
            client.in_vc[chat_id] = False
            
        elif "VC object stale" in msg:
            client.vc_cooldowns[chat_id] = datetime.now() + timedelta(seconds=random.uniform(2.0, 5.0))
            if was_in_vc:
                logger.info(f"🔄 [VC REFRESH] 👤 [{bot_name}] ➜ VC restarted by admin. Re-joining...")
                client.in_vc[chat_id] = False
                
        else:
            human_reason = msg
            if "RpcCallFailError" in msg: human_reason = "Telegram Server Glitch (RpcCallFail)"
            elif "offline" in msg.lower(): human_reason = "TCP Connection Dropped (Network Issue)"
            elif "No Active VC" in msg: human_reason = "No Active VC"
            elif "EntityNotFound" in msg: human_reason = "Bot is not in the chat"
            
            if was_in_vc:
                if "No Active VC" in human_reason: 
                    logger.info(f"🔚 [VC ENDED] 👤 [{bot_name}] ➜ The Voice Chat was closed.")
                else:
                    logger.warning(f"📉 [VC DROP] 👤 [{bot_name}] ➜ Disconnected from VC: {human_reason}")
                client.in_vc[chat_id] = False
                
            if "No Active VC" in human_reason or "EntityNotFound" in human_reason:
                # If there's truly no VC, safely check every 30s without hitting rate limits
                client.vc_cooldowns[chat_id] = datetime.now() + timedelta(seconds=30)
            else:
                client.vc_cooldowns[chat_id] = datetime.now() + timedelta(seconds=15)

    while True:
        try:
            targets = GLOBAL_CONF.get('target_chats', [])
            bots = list(active_userbots.values())
            
            if not targets or not bots:
                await asyncio.sleep(2)
                continue
            now = datetime.now()
            
            for chat_id in targets:
                if chat_id not in initialized_targets:
                    for idx, client in enumerate(active_userbots.values()):
                        if not hasattr(client, 'vc_cooldowns'): client.vc_cooldowns = {}
                        # Stagger completely so bots join flawlessly
                        client.vc_cooldowns[chat_id] = now + timedelta(seconds=idx * 1.5)
                    initialized_targets.add(chat_id)

            for uid, client in list(active_userbots.items()):
                if getattr(client, '_vc_stop_flag', False) or not client.is_connected():
                    if hasattr(client, 'in_vc'):
                        for cid, in_vc in client.in_vc.items():
                            if in_vc: client.in_vc[cid] = False
                    continue
                    
                my_index = list(active_userbots.keys()).index(uid)
                for chat_id in targets:
                    join_limit = get_effective_limit(chat_id, "join_limit")
                    if join_limit != 0 and my_index >= join_limit: continue
                        
                    if not hasattr(client, 'vc_cooldowns'): client.vc_cooldowns = {}
                    cooldown = client.vc_cooldowns.get(chat_id, datetime.min)
                    
                    if now >= cooldown:
                        client.vc_cooldowns[chat_id] = now + timedelta(seconds=60) 
                        # 🛠️ ASYNC SWARM: 90 bots ping independently without waiting for each other!
                        asyncio.create_task(process_ping(client, chat_id))
            
        except Exception as e:
            logger.error(f"VC Manager encountered a loop error: {e}")
        await asyncio.sleep(0.5)


async def start_userbot(session_string, user_id, name, startup_delay=0):
    if startup_delay > 0: await asyncio.sleep(startup_delay)
        
    try:
        client = TelegramClient(
            StringSession(session_string), 
            API_ID, 
            API_HASH,
            connection=ConnectionTcpAbridged,
            device_model="ManagerBot",
            system_version="Linux System",
            app_version="3.0",
            connection_retries=None,
            retry_delay=3,
            auto_reconnect=True,
            request_retries=3
        )
        
        try:
            await client.connect()
        except Exception as e:
            logger.warning(f"Connection issue for {name} ({user_id}): {e}")
            error_str = str(e).lower()
            if any(x in error_str for x in ["unregistered", "revoked", "deactivated", "two different ip addresses"]):
                await mark_bot_banned(user_id, name, "Session Revoked on Startup")
            return None

        if not await client.is_user_authorized():
            await log_to_channel(f"⚠️ Session for {name} (ID: {user_id}) is expired. Removing.")
            await mark_bot_banned(user_id, name, "Unauthorized on Startup")
            return None

        try: await client.get_dialogs(limit=50)
        except Exception: pass

        me = await client.get_me()
        client.me = me
        client._vc_stop_flag = False
        active_userbots[me.id] = client

        # --- EVENT: AUTO REACTION & VIEW INCREMENT ---
        @client.on(events.NewMessage(incoming=True))
        async def reaction_handler(event):
            if getattr(client, '_vc_stop_flag', False): return
            if not event.is_channel: return
            targets = GLOBAL_CONF.get('target_chats', [])
            if targets and event.chat_id not in targets: return
            if event.message.action: return
            
            if hasattr(client, 'react_cooldown') and datetime.now() < client.react_cooldown: return

            active_list = list(active_userbots.keys())
            my_index = active_list.index(client.me.id) if client.me.id in active_list else 999
            
            base_delay = random.uniform(1.0, 3.0)
            stagger = my_index * random.uniform(0.5, 1.5)
            await asyncio.sleep(base_delay + stagger)
            
            view_limit = get_effective_limit(event.chat_id, "view_limit")
            react_limit = get_effective_limit(event.chat_id, "react_limit")

            if view_limit == 0 or my_index < view_limit:
                try: await client(functions.messages.GetMessagesViewsRequest(peer=event.peer_id, id=[event.id], increment=True))
                except Exception: pass

            if react_limit == 0 or my_index < react_limit:
                try:
                    chat_emoji_list = get_effective_emoji_list(event.chat_id)
                    emoji = random.choice(chat_emoji_list) if chat_emoji_list else random.choice(EMOJIS)
                    
                    try:
                        await client(functions.messages.SendReactionRequest(peer=event.peer_id, msg_id=event.id, reaction=[types.ReactionEmoji(emoticon=emoji)]))
                    except TypeError:
                        await client(functions.messages.SendReactionRequest(peer=event.peer_id, msg_id=event.id, reaction=emoji))
                except FloodWaitError as e: 
                    client.react_cooldown = datetime.now() + timedelta(seconds=e.seconds + 10)
                except Exception as e:
                    pass

        return client

    except Exception as e:
        logger.error(f"Failed to start userbot {name}: {e}")
        return None

async def reload_userbots():
    for uid, client in list(active_userbots.items()):
        try:
            client._vc_stop_flag = True
            if client.is_connected(): await client.disconnect()
        except: pass
    active_userbots.clear()

    await refresh_global_config()
    sessions = await db.get_all_sessions()
    
    tasks = [start_userbot(s['session_string'], s['user_id'], s.get('name', 'Unknown'), i * 0.5) for i, s in enumerate(sessions)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successful_bots = [r for r in results if r is not None and not isinstance(r, Exception)]
    
    return len(successful_bots)

# --- MASTER BOT INTERFACE ---
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if not is_admin(event.sender_id):
        return await event.respond("⛔ **Access Denied.** You are not an admin.")
    await show_dashboard(event)


async def show_dashboard(event, edit=False):
    active_count = len(active_userbots)
    targets_count = len(GLOBAL_CONF.get('target_chats', []))
    admins_count = len(GLOBAL_CONF.get('all_admins', []))

    role_str = "👑 Owner" if is_owner(event.sender_id) else "🛡️ Admin"

    text = (
        "💠 **𝗠𝗮𝗻𝗮𝗴𝗲𝗿 𝗣𝗿𝗼 𝗗𝗮𝘀𝗵𝗯𝗼𝗮𝗿𝗱** 💠\n\n"
        f"👤 **Your Role:** `{role_str}`\n\n"
        "📊 **𝗦𝘆𝘀𝘁𝗲𝗺 𝗦𝘁𝗮𝘁𝘂𝘀:**\n"
        f"├ 🤖 **Active Bots:** `{active_count}`\n"
        f"├ 🎯 **Target Chats:** `{targets_count}`\n"
        f"└ 👥 **Admins:** `{admins_count}`\n\n"
        "👨‍💻 **Developer:** [Arru](https://t.me/OpArru)\n\n"
        "🛠️ **𝗦𝗲𝗹𝗲𝗰𝘁 𝗮 𝗖𝗮𝘁𝗲𝗴𝗼𝗿𝘆 𝗕𝗲𝗹𝗼𝘄:**"
    )

    buttons = [
        [Button.inline("🤖 Bots", b"menu_bots"), Button.inline("👁️ Engagement", b"menu_engagement")],
        [Button.inline("🎙️ Join Chat", b"menu_tools"), Button.inline("⚙️ Settings", b"menu_settings")],
        [Button.inline("🔄 Reload System", b"reload")]
    ]

    if edit:
        try: await event.edit(text, buttons=buttons, file=WELCOME_IMAGE)
        except Exception: await event.edit(text, buttons=buttons)
    else:
        await event.respond(text, buttons=buttons, file=WELCOME_IMAGE)


@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if not is_admin(event.sender_id):
        return await event.answer("⛔ Access Denied", alert=True)

    data = event.data.decode('utf-8')
    await process_callback_data(event, data)


async def process_callback_data(event, data):
    chat_id = event.chat_id

    # --- ADVANCED UI: EMOJI TOGGLE LOGIC ---
    if data.startswith("emj_one_"):
        em = data.split("_", 2)[2]
        state = login_states.get(chat_id, {})
        if 'selected_emojis' not in state: state['selected_emojis'] = []
        if em in state['selected_emojis']: state['selected_emojis'].remove(em)
        else: state['selected_emojis'].append(em)
        await event.edit(buttons=build_emoji_keyboard(state['selected_emojis'], "one"))
        return
        
    elif data == "conf_one":
        state = login_states.get(chat_id, {})
        emojis = state.get('selected_emojis', [])
        link = state.get('link')
        count = state.get('count')
        login_states.pop(chat_id, None)
        
        msg = await event.edit("⏳ **Processing Reactions...** *(Batched mode)*")
        await process_one_time_engagement(link, count, "react", emojis)
        await msg.edit("✅ Background queuing complete.", buttons=[[Button.inline("❤️ Back to Reacts Menu", b"reacts_menu")]])
        return
        
    elif data == "rand_one":
        state = login_states.get(chat_id, {})
        link = state.get('link')
        count = state.get('count')
        login_states.pop(chat_id, None)
        
        msg = await event.edit("⏳ **Processing Random Reactions...** *(Batched mode)*")
        await process_one_time_engagement(link, count, "react", EMOJIS.copy())
        await msg.edit("✅ Background queuing complete.", buttons=[[Button.inline("❤️ Back to Reacts Menu", b"reacts_menu")]])
        return

    elif data.startswith("emj_tgt_"):
        parts = data.split("_", 3)
        chat_id_target = parts[2]
        em = parts[3]
        state = login_states.get(chat_id, {})
        if 'selected_emojis' not in state: state['selected_emojis'] = []
        if em in state['selected_emojis']: state['selected_emojis'].remove(em)
        else: state['selected_emojis'].append(em)
        await event.edit(buttons=build_emoji_keyboard(state['selected_emojis'], "tgt", chat_id_target))
        return
        
    elif data.startswith("conf_tgt_"):
        chat_id_target = data.split("_")[2]
        state = login_states.get(chat_id, {})
        emojis = state.get('selected_emojis', [])
        if not emojis: emojis = "RANDOM"
        
        await db.update_target_settings(chat_id_target, "emoji", emojis)
        await refresh_global_config()
        login_states.pop(chat_id, None)
        
        disp = ", ".join(emojis) if isinstance(emojis, list) else emojis
        await event.edit(f"✅ **Settings Updated!**\nTarget `{chat_id_target}` emoji set to: **{disp}**",
                            buttons=[[Button.inline("🔙 Back to Chat Settings", f"t_menu_{chat_id_target}")]])
        return

    elif data.startswith("rand_tgt_"):
        chat_id_target = data.split("_")[2]
        await db.update_target_settings(chat_id_target, "emoji", "RANDOM")
        await refresh_global_config()
        login_states.pop(chat_id, None)
        await event.edit(f"✅ **Settings Updated!**\nTarget `{chat_id_target}` emoji set to: **RANDOM**",
                            buttons=[[Button.inline("🔙 Back to Chat Settings", f"t_menu_{chat_id_target}")]])
        return


    # --- OWNER-ONLY ROUTES PROTECTION ---
    if data in ["remove_menu", "clean_bots", "admin_menu", "add_admin_step", "rm_admin_menu", "clear_tgt", "set_limit_view", "set_limit_react", "set_limit_join"]:
        if not is_owner(event.sender_id):
            return await event.answer("⛔ Owner Only Action: You don't have permission.", alert=True)

    if data.startswith("rm_adm_"):
        if not is_owner(event.sender_id):
            return await event.answer("⛔ Owner Only Action: You cannot remove admins.", alert=True)

    elif data.startswith("rm_tgt_"):
        target_id = int(data.split("_")[2])
        target_settings = GLOBAL_CONF.get('target_settings', {}).get(str(target_id), {})
        added_by = target_settings.get('added_by')
        
        if not is_owner(event.sender_id) and added_by != event.sender_id:
            return await event.answer("⛔ You can only remove targets you personally added. Owner access required to remove others'.", alert=True)
            
        await db.remove_target_chat(target_id)
        await refresh_global_config()
        await event.answer("Target Removed.", alert=True)
        return await process_callback_data(event, "manage_targets_list")

    elif data.startswith("rm_"): 
        if not is_owner(event.sender_id):
            return await event.answer("⛔ Owner Only Action: Admins cannot remove bots.", alert=True)

    if data.startswith("menu_") or data == "main_menu":
        if chat_id in login_states: del login_states[chat_id]

    # --- MAIN NAVIGATION ---
    if data == "main_menu":
        await show_dashboard(event, edit=True)

    # --- SUB-MENUS ---
    elif data == "menu_bots":
        text = "🤖 **𝗕𝗼𝘁 𝗠𝗮𝗻𝗮𝗴𝗲𝗺𝗲𝗻𝘁**\nManage your connected Userbots."
        buttons = []
        if is_owner(event.sender_id):
            buttons = [
                [Button.inline("➕ Add Bot", b"add_menu"), Button.inline("🗑️ Remove", b"remove_menu")],
                [Button.inline("📜 Active Bots", b"list"), Button.inline("🧹 Clean Dead", b"clean_bots")],
                [Button.inline("🔙 Back", b"main_menu")]
            ]
        else: 
            buttons = [
                [Button.inline("➕ Add Bot", b"add_menu"), Button.inline("📜 Active Bots", b"list")],
                [Button.inline("🔙 Back", b"main_menu")]
            ]
        await event.edit(text, buttons=buttons)

    elif data == "menu_engagement":
        text = "👁️ **𝗘𝗻𝗴𝗮𝗴𝗲𝗺𝗲𝗻𝘁 𝗧𝗼𝗼𝗹𝘀**\nTrigger custom views and reactions."
        buttons = [
            [Button.inline("👁️ Views", b"views_menu"), Button.inline("❤️ Reactions", b"reacts_menu")],
            [Button.inline("🎯 Target Automations", b"target_menu")],
            [Button.inline("🔙 Back", b"main_menu")]
        ]
        await event.edit(text, buttons=buttons)

    elif data == "menu_tools":
        text = "🎙️ **𝗖𝗵𝗮𝘁 & 𝗩𝗖 𝗧𝗼𝗼𝗹𝘀**\nForce bots to join/leave chats or voice calls."
        buttons = [
            [Button.inline("🔗 Join Chat", b"join_link_menu"), Button.inline("🎙️ Join VC", b"join_vc_menu")],
            [Button.inline("🚪 Leave Chat", b"leave_chat_menu")],
            [Button.inline("🔙 Back", b"main_menu")]
        ]
        await event.edit(text, buttons=buttons)

    elif data == "menu_settings":
        text = "⚙️ **𝗦𝘆𝘀𝘁𝗲𝗺 𝗦𝗲𝘁𝘁𝗶𝗻𝗴𝘀**\nConfigure admins and view statistics."
        buttons = []
        if is_owner(event.sender_id):
            buttons.append([Button.inline("👥 Admins", b"admin_menu"), Button.inline("📊 Statistics", b"stats_menu")])
        else: 
            buttons.append([Button.inline("📊 Statistics", b"stats_menu")])
        buttons.append([Button.inline("🔙 Back", b"main_menu")])
        await event.edit(text, buttons=buttons)

    # --- ENGAGEMENT: VIEWS ---
    elif data == "views_menu":
        text = "👁️ **𝗖𝘂𝘀𝘁𝗼𝗺 𝗩𝗶𝗲𝘄𝘀**\nSend views to a specific post."
        buttons = [
            [Button.inline("🎯 Send Views", b"setup_onetime_views")],
            [Button.inline("🔙 Back", b"menu_engagement")]
        ]
        await event.edit(text, buttons=buttons)

    elif data == "setup_onetime_views":
        login_states[chat_id] = {"step": "ONETIME_VIEW_LINK"}
        await event.edit("🔗 **Step 1/2:** Send the Telegram Message Link.\n*(Format: t.me/channel/123)*\n\n__Type /cancel to abort.__", 
                         buttons=[[Button.inline("❌ Cancel", b"views_menu")]])

    # --- ENGAGEMENT: REACTIONS ---
    elif data == "reacts_menu":
        text = "❤️ **𝗖𝘂𝘀𝘁𝗼𝗺 𝗥𝗲𝗮𝗰𝘁𝗶𝗼𝗻𝘀**\nSend reactions to a specific post."
        buttons = [
            [Button.inline("🎯 Send Reactions", b"setup_onetime_reacts")],
            [Button.inline("🔙 Back", b"menu_engagement")]
        ]
        await event.edit(text, buttons=buttons)

    elif data == "setup_onetime_reacts":
        login_states[chat_id] = {"step": "ONETIME_REACT_LINK"}
        await event.edit("🔗 **Step 1/3:** Send the Telegram Message Link.\n*(Format: t.me/channel/123)*\n\n__Type /cancel to abort.__", 
                         buttons=[[Button.inline("❌ Cancel", b"reacts_menu")]])

    # --- ENGAGEMENT: TARGET AUTOMATIONS ---
    elif data == "target_menu":
        targets = GLOBAL_CONF.get('target_chats', [])
        v_limit = GLOBAL_CONF.get('auto_views_limit', 0)
        r_limit = GLOBAL_CONF.get('auto_reacts_limit', 0)
        j_limit = GLOBAL_CONF.get('auto_join_limit', 0)
        
        status = "Global (All Chats)" if not targets else f"{len(targets)} Specific Chats"
        v_text = "ALL Bots" if v_limit == 0 else f"{v_limit} Bots"
        r_text = "ALL Bots" if r_limit == 0 else f"{r_limit} Bots"
        j_text = "ALL Bots" if j_limit == 0 else f"{j_limit} Bots"

        text = (
            f"🎯 **𝗧𝗮𝗿𝗴𝗲𝘁 𝗔𝘂𝘁𝗼𝗺𝗮𝘁𝗶𝗼𝗻𝘀**\n"
            f"Monitored Chats: `{status}`\n"
            f"Def. View Limit: `{v_text}`\n"
            f"Def. React Limit: `{r_text}`\n"
            f"Def. Join Limit: `{j_text}`\n\n"
            "Bots automatically engage with new posts in these chats."
        )
        buttons = [
            [Button.inline("➕ Add Target", b"add_tgt_id"), Button.inline("📋 Manage Targets", b"manage_targets_list")]
        ]
        
        if is_owner(event.sender_id):
            buttons.append([Button.inline("⚙️ Set Global View Limit", b"set_limit_view")])
            buttons.append([Button.inline("⚙️ Set Global React Limit", b"set_limit_react")])
            buttons.append([Button.inline("⚙️ Set Global Join VC Limit", b"set_limit_join")])
            buttons.append([Button.inline("🗑️ Clear All Targets", b"clear_tgt")])
            
        buttons.append([Button.inline("🔙 Back", b"menu_engagement")])
        await event.edit(text, buttons=buttons)

    elif data == "manage_targets_list":
        targets = GLOBAL_CONF.get('target_chats', [])
        if not targets: return await event.answer("No targets configured.", alert=True)
        
        msg = await event.edit("⏳ **Fetching Target Info...**")
        
        chat_names = {}
        if active_userbots:
            client = list(active_userbots.values())[0]
            for tid in targets:
                try:
                    entity = await client.get_entity(tid)
                    chat_names[tid] = getattr(entity, 'title', "Unknown Chat")
                except:
                    chat_names[tid] = "Unknown/Inaccessible"
        else:
            chat_names = {tid: "Unknown (No Bots)" for tid in targets}

        text = "📋 **𝗦𝗲𝗹𝗲𝗰𝘁 𝗮 𝗧𝗮𝗿𝗴𝗲𝘁 𝘁𝗼 𝗖𝗼𝗻𝗳𝗶𝗴𝘂𝗿𝗲:**\nChoose a chat to set specific limits or remove it."
        
        buttons = []
        for tid in targets:
            name_short = chat_names[tid][:25] + ("..." if len(chat_names[tid]) > 25 else "")
            buttons.append([Button.inline(f"📢 {name_short}", f"t_menu_{tid}")])
            
        buttons.append([Button.inline("🔙 Back", b"target_menu")])
        await msg.edit(text, buttons=buttons)

    elif data.startswith("t_menu_"):
        chat_id_tgt = int(data.split("_")[2])
        
        target_settings = GLOBAL_CONF.get('target_settings', {}).get(str(chat_id_tgt), {})
        v_spec = target_settings.get('view_limit', -1)
        r_spec = target_settings.get('react_limit', -1)
        j_spec = target_settings.get('join_limit', -1)
        e_spec = target_settings.get('emoji', 'RANDOM')
        added_by = target_settings.get('added_by')
        
        g_v = int(GLOBAL_CONF.get('auto_views_limit', 0))
        g_r = int(GLOBAL_CONF.get('auto_reacts_limit', 0))
        g_j = int(GLOBAL_CONF.get('auto_join_limit', 0))
        
        v_display = f"{v_spec} (Specific)" if v_spec != -1 else f"Global ({g_v if g_v != 0 else 'ALL'})"
        r_display = f"{r_spec} (Specific)" if r_spec != -1 else f"Global ({g_r if g_r != 0 else 'ALL'})"
        j_display = f"{j_spec} (Specific)" if j_spec != -1 else f"Global ({g_j if g_j != 0 else 'ALL'})"
        
        if isinstance(e_spec, list): e_display = ", ".join(e_spec)
        else: e_display = e_spec
        
        chat_name = f"ID: {chat_id_tgt}"
        if active_userbots:
             try:
                 entity = await list(active_userbots.values())[0].get_entity(chat_id_tgt)
                 chat_name = getattr(entity, 'title', chat_name)
             except: pass

        adder_text = f"`{added_by}`" if added_by else "Unknown"
        text = (
            f"⚙️ **𝗧𝗮𝗿𝗴𝗲𝘁 𝗦𝗲𝘁𝘁𝗶𝗻𝗴𝘀**\n"
            f"📢 **Chat:** {chat_name}\n"
            f"👤 **Added By:** {adder_text}\n\n"
            f"👀 **View Limit:** `{v_display}`\n"
            f"❤️ **React Limit:** `{r_display}`\n"
            f"🎙️ **Join VC Limit:** `{j_display}`\n"
            f"🎭 **Specific Emoji:** `{e_display}`\n\n"
            "__Specific limits override global settings.__"
        )
        
        buttons = []

        if is_owner(event.sender_id) or event.sender_id == added_by:
            buttons.append([Button.inline("✏️ Set View Limit", f"set_t_v_{chat_id_tgt}"), Button.inline("✏️ Set React Limit", f"set_t_r_{chat_id_tgt}")])
            buttons.append([Button.inline("✏️ Set Join VC Limit", f"set_t_j_{chat_id_tgt}"), Button.inline("🎭 Set Emoji", f"set_t_e_{chat_id_tgt}")])
            buttons.append([Button.inline("🗑️ Remove Target", f"rm_tgt_{chat_id_tgt}")])

        buttons.append([Button.inline("🔙 Back", b"manage_targets_list")])
        await event.edit(text, buttons=buttons)

    elif data.startswith("set_t_v_") or data.startswith("set_t_r_") or data.startswith("set_t_j_") or data.startswith("set_t_e_"):
        parts = data.split("_")
        type_code = parts[2]
        setting_type = "VIEW" if type_code == "v" else ("REACT" if type_code == "r" else ("JOIN" if type_code == "j" else "EMOJI"))
        chat_id_target = int(parts[3])
        
        target_settings = GLOBAL_CONF.get('target_settings', {}).get(str(chat_id_target), {})
        added_by = target_settings.get('added_by')
        
        if not is_owner(event.sender_id) and added_by != event.sender_id:
            return await event.answer("⛔ You can only modify limits for targets you personally added.", alert=True)
        
        if setting_type == "EMOJI":
            current_emojis = target_settings.get('emoji', [])
            if isinstance(current_emojis, str) and current_emojis != "RANDOM": current_emojis = [current_emojis]
            elif current_emojis == "RANDOM": current_emojis = []
                
            login_states[chat_id] = {
                "step": "SET_SPECIFIC_EMOJI",
                "target_id": chat_id_target,
                "selected_emojis": current_emojis
            }
            await event.edit(
                f"🎭 **Set Specific Emoji for Target**\n\nClick to toggle. Select multiple to automatically divide reactions among bots!",
                buttons=build_emoji_keyboard(current_emojis, "tgt", str(chat_id_target))
            )
        else:
            login_states[chat_id] = {
                "step": f"SET_SPECIFIC_{setting_type}",
                "target_id": chat_id_target
            }
            l_name = "Views" if setting_type == "VIEW" else ("Reactions" if setting_type == "REACT" else "Joins")
            await event.edit(
                f"🔢 **Set Specific {l_name} Limit**\n\n"
                f"Enter the number of bots for this chat.\n"
                f"• Send `-1` to use **Global Defaults**.\n"
                f"• Send `0` for **ALL Active Bots**.",
                buttons=[[Button.inline("❌ Cancel", f"t_menu_{chat_id_target}")]]
            )

    elif data in ["set_limit_view", "set_limit_react", "set_limit_join"]:
        if data == "set_limit_view": l_type = "Views"
        elif data == "set_limit_react": l_type = "Reactions"
        else: l_type = "Joins"
        
        login_states[chat_id] = {"step": f"SET_LIMIT_{l_type.upper()}"}
        await event.edit(f"⚙️ **Set Global Limit for {l_type}**\n\nSend a number (e.g., `5`).\n*Send `0` to use ALL active bots.*",
                         buttons=[[Button.inline("❌ Cancel", b"target_menu")]])

    elif data == "add_tgt_id":
        login_states[chat_id] = {"step": "ADD_TARGET"}
        await event.edit("➕ **Send Target Chat ID**\nExample: `-100123456789`", buttons=[[Button.inline("❌ Cancel", b"target_menu")]])

    elif data == "clear_tgt":
        await db.clear_target_chats()
        await refresh_global_config()
        await event.answer("Targets cleared! Monitoring ALL channels bots are in.", alert=True)
        return await process_callback_data(event, "target_menu")

    # --- STATISTICS ---
    elif data == "stats_menu":
        try:
            await event.delete()
            online_count = 0
            offline_count = 0
            for uid, client in active_userbots.items():
                if client.is_connected(): online_count += 1
                else: offline_count += 1

            stats_text = (
                f"📊 **𝗕𝗼𝘁 𝗦𝘁𝗮𝘁𝗶𝘀𝘁𝗶𝗰𝘀**\n\n"
                f"🟢 **Online Bots:** `{online_count}`\n"
                f"🔴 **Offline Bots:** `{offline_count}`\n"
                f"🤖 **Total Bots:** `{len(active_userbots)}`\n"
                f"🎯 **Monitored Targets:** `{len(GLOBAL_CONF.get('target_chats', []))}`\n\n"
                f"*(Detailed bot list hidden to prevent Telegram message length limits)*"
            )
            await event.respond(stats_text, buttons=[[Button.inline("🔙 Back", b"menu_settings")]])
        except Exception as e:
            logger.error(f"Stats Menu Error: {e}")

    # --- TOOLS: JOIN/LEAVE MENUS ---
    elif data == "join_link_menu":
        login_states[chat_id] = {"step": "JOIN_LINK"}
        text = ("🔗 **𝗠𝗮𝘀𝘀 𝗝𝗼𝗶𝗻 𝗖𝗵𝗮𝘁**\nSend a Public or Private link. Bots will join the chat/channel.\n"
                "__Supported: t.me/channel or t.me/+hash__\nType /cancel to abort.")
        await event.edit(text, buttons=[[Button.inline("❌ Cancel", b"menu_tools")]])

    elif data == "join_vc_menu":
        login_states[chat_id] = {"step": "JOIN_VC_LINK"}
        text = ("🎙️ **𝗠𝗮𝘀𝘀 𝗝𝗼𝗶𝗻 𝗩𝗼𝗶𝗰𝗲 𝗖𝗵𝗮𝘁**\nSend a Link. Bots will join the chat and enter the active call muted.\n"
                "Type /cancel to abort.")
        await event.edit(text, buttons=[[Button.inline("❌ Cancel", b"menu_tools")]])

    elif data == "leave_chat_menu":
        login_states[chat_id] = {"step": "LEAVE_LINK"}
        text = ("🚪 **𝗠𝗮𝘀𝘀 𝗟𝗲𝗮𝘃𝗲 𝗖𝗵𝗮𝘁**\nSend a public link or chat username to make all bots LEAVE it.\n"
                "Type /cancel to abort.")
        await event.edit(text, buttons=[[Button.inline("❌ Cancel", b"menu_tools")]])

    # --- ADMIN MANAGEMENT (OWNER ONLY ROUTE PROTECTED ABOVE) ---
    elif data == "admin_menu":
        admins = GLOBAL_CONF.get('all_admins', [])
        text = "👥 **𝗔𝗱𝗺𝗶𝗻 𝗠𝗮𝗻𝗮𝗴𝗲𝗺𝗲𝗻𝘁**\n\nCurrent Admins:\n"
        for adm in admins: text += f"• `{adm}`\n"
        buttons = [
            [Button.inline("➕ Add", b"add_admin_step"), Button.inline("➖ Remove", b"rm_admin_menu")],
            [Button.inline("🔙 Back", b"menu_settings")]
        ]
        await event.edit(text, buttons=buttons)

    elif data == "add_admin_step":
        login_states[chat_id] = {"step": "ADD_ADMIN"}
        await event.edit("➕ **Send the Telegram ID** of the new admin.\n\n__Type /cancel to abort.__", 
                         buttons=[[Button.inline("❌ Cancel", b"admin_menu")]])

    elif data == "rm_admin_menu":
        admins = GLOBAL_CONF.get('all_admins', [])
        buttons = []
        for adm in admins:
            if adm != event.sender_id and adm not in ENV_ADMINS and not is_owner(adm): 
                buttons.append([Button.inline(f"❌ {adm}", f"rm_adm_{adm}")])
        buttons.append([Button.inline("🔙 Back", b"admin_menu")])
        await event.edit("Select Admin to Remove:", buttons=buttons)

    elif data.startswith("rm_adm_"):
        adm_id = int(data.split("_")[2])
        await db.remove_admin(adm_id)
        await refresh_global_config()
        await event.answer(f"Removed Admin {adm_id}", alert=True)
        return await process_callback_data(event, "admin_menu")

    # --- BOT MANAGEMENT ---
    elif data == "add_menu":
        await event.edit("➕ **𝗔𝗱𝗱 𝗡𝗲𝘄 𝗕𝗼𝘁**\nSelect login method:", buttons=[
            [Button.inline("📱 Phone Number", b"add_phone"), Button.inline("📝 Session", b"add_string")],
            [Button.inline("🔙 Back", b"menu_bots")]
        ])

    elif data == "add_phone":
        login_states[chat_id] = {"step": "PHONE"}
        await event.edit("📱 **Enter Phone Number**\nEx: `+1234567890`", buttons=[[Button.inline("❌ Cancel", b"menu_bots")]])

    elif data == "add_string":
        login_states[chat_id] = {"step": "STRING"}
        await event.edit("📝 **Paste Pyrogram/Telethon Session String**", buttons=[[Button.inline("❌ Cancel", b"menu_bots")]])

    elif data == "list":
        try:
            await event.delete()
            sessions = await db.get_all_sessions()
            text = "📜 **𝗥𝗲𝗴𝗶𝘀𝘁𝗲𝗿𝗲𝗱 𝗔𝗰𝗰𝗼𝘂𝗻𝘁𝘀:**\n\n"
            for s in sessions:
                status = "🟢" if s['user_id'] in active_userbots else "🔴"
                text += f"{status} **{s.get('name', 'Unknown')}** (`{s['user_id']}`)\n"
            
            if len(text) > 4000:
                text = text[:4000] + "\n...(truncated due to limit)"
                
            await event.respond(text, buttons=[[Button.inline("🔙 Back", b"menu_bots")]])
        except Exception as e:
            logger.error(f"List Menu Error: {e}")

    elif data == "remove_menu":
        sessions = await db.get_all_sessions()
        buttons = [[Button.inline(f"❌ {s.get('name','User')} ({s['user_id']})", f"rm_{s['user_id']}")] for s in sessions]
        buttons.append([Button.inline("🔙 Back", b"menu_bots")])
        await event.edit("🗑️ **Select account to remove:**", buttons=buttons)

    elif data.startswith("rm_"):
        uid = int(data.split("_")[1])
        await db.remove_session(uid)
        if uid in active_userbots:
            client = active_userbots[uid]
            client._vc_stop_flag = True
            try: await client.disconnect()
            except: pass
            del active_userbots[uid]
        await event.answer("Account removed!", alert=True)
        return await process_callback_data(event, "menu_bots")

    elif data == "clean_bots":
        msg = await event.edit("🧹 **Scanning bots for dead sessions...**\n__Please wait...__")
        dead_count = 0
        for uid, client in list(active_userbots.items()):
            try:
                if not await client.is_user_authorized():
                    raise Exception("Unauthorized")
                await client.get_me() 
            except Exception:
                dead_count += 1
                name = client.me.first_name if getattr(client, 'me', None) else "Unknown"
                await mark_bot_banned(uid, name, "Cleaned Manually")
                    
        await msg.edit(f"✅ **Cleanup Complete!**\n\n🗑️ Removed `{dead_count}` dead/banned bots.", buttons=[[Button.inline("🔙 Back", b"menu_bots")]])

    elif data == "reload":
        msg = await event.edit("🔄 **Reloading system...**")
        count = await reload_userbots()
        await msg.edit(f"✅ **Reload Complete!**\n\nActive Bots: {count}", buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]])


@bot.on(events.NewMessage)
async def wizard_handler(event):
    if not is_admin(event.sender_id) or not event.text:
        return

    chat_id = event.chat_id
    text = event.text.strip()

    if text == "/cancel":
        if chat_id in login_states:
            if 'client' in login_states[chat_id]:
                try: 
                    login_states[chat_id]['client']._vc_stop_flag = True
                    await login_states[chat_id]['client'].disconnect()
                except: pass
            login_states.pop(chat_id, None)
        await event.respond("🚫 Operation Cancelled.", buttons=[[Button.inline("🏠 Main Menu", b"main_menu")]])
        return

    if chat_id not in login_states: return

    state = login_states[chat_id]
    step = state['step']

    try:
        if step == "ONETIME_VIEW_LINK":
            state['link'] = text
            state['step'] = "ONETIME_VIEW_COUNT"
            await event.respond(f"🔢 **Step 2/2:** How many bots should view this?\n*(Available active bots: {len(active_userbots)})*",
                                buttons=[[Button.inline("❌ Cancel", b"views_menu")]])
            
        elif step == "ONETIME_VIEW_COUNT":
            try:
                count = int(text)
                link = state['link']
                login_states.pop(chat_id, None)
                
                msg = await event.respond("⏳ **Processing Views...** *(Batched mode)*")
                await process_one_time_engagement(link, count, "view")
                await msg.edit("✅ Background queuing complete.", buttons=[[Button.inline("👁️ Back to Views Menu", b"views_menu")]])
            except ValueError:
                await event.respond("❌ Please enter a valid number.")

        elif step == "ONETIME_REACT_LINK":
            state['link'] = text
            state['step'] = "ONETIME_REACT_COUNT"
            await event.respond(f"🔢 **Step 2/3:** How many bots should react to this?\n*(Available active bots: {len(active_userbots)})*",
                                buttons=[[Button.inline("❌ Cancel", b"reacts_menu")]])
            
        elif step == "ONETIME_REACT_COUNT":
            try:
                state['count'] = int(text)
                state['step'] = "ONETIME_REACT_EMOJI_UI"
                state['selected_emojis'] = []
                await event.respond(
                    "🎭 **Select Emoji(s):**\nClick to toggle. Select multiple to divide reactions among bots!",
                    buttons=build_emoji_keyboard([], "one")
                )
            except ValueError:
                await event.respond("❌ Please enter a valid number.")

        elif step == "ONETIME_REACT_EMOJI_UI":
            emoji = text if text != "/random" else None
            link = state['link']
            count = state['count']
            login_states.pop(chat_id, None)
            
            msg = await event.respond("⏳ **Processing Reactions...** *(Batched mode)*")
            await process_one_time_engagement(link, count, "react", [emoji] if emoji else None)
            await msg.edit("✅ Background queuing complete.", buttons=[[Button.inline("❤️ Back to Reacts Menu", b"reacts_menu")]])

        elif step.startswith("SET_LIMIT_"):
            try:
                limit = int(text)
                if step == "SET_LIMIT_VIEWS": conf_key = "auto_views_limit"
                elif step == "SET_LIMIT_REACTS": conf_key = "auto_reacts_limit"
                else: conf_key = "auto_join_limit"

                await db.update_config(conf_key, limit)
                await refresh_global_config()
                login_states.pop(chat_id, None)
                
                limit_str = "ALL" if limit == 0 else str(limit)
                await event.respond(f"✅ Auto limit updated to **{limit_str}** bots per post.",
                                    buttons=[[Button.inline("🎯 Back to Targets", b"target_menu")]])
            except ValueError:
                await event.respond("❌ Please enter a valid number.")

        elif step.startswith("SET_SPECIFIC_"):
            try:
                limit = int(text)
                target_id = state['target_id']
                if step == "SET_SPECIFIC_VIEW": setting_key = "view_limit"
                elif step == "SET_SPECIFIC_REACT": setting_key = "react_limit"
                else: setting_key = "join_limit"
                
                await db.update_target_settings(str(target_id), setting_key, limit)
                await refresh_global_config()
                login_states.pop(chat_id, None)
                
                disp = "Global Defaults" if limit == -1 else f"{limit} Bots"
                await event.respond(f"✅ **Settings Updated!**\nTarget `{target_id}` set to: **{disp}**",
                                    buttons=[[Button.inline("🔙 Back to Chat Settings", f"t_menu_{target_id}")],
                                             [Button.inline("📋 Target List", b"manage_targets_list")]])
            except ValueError:
                await event.respond("❌ Please enter a valid number (-1, 0, or >0).")

        elif step == "ADD_ADMIN":
            if not is_owner(event.sender_id):
                login_states.pop(chat_id, None)
                return await event.respond("⛔ Owner Only Action: You cannot add admins.")

            try:
                new_admin_id = int(text)
                await db.add_admin(new_admin_id)
                await refresh_global_config()
                login_states.pop(chat_id, None)
                await event.respond(f"✅ **Admin Added:** `{new_admin_id}`", buttons=[[Button.inline("👥 Admin Menu", b"admin_menu")]])
            except ValueError:
                await event.respond("❌ Invalid ID. Send a numeric User ID.")

        elif step == "JOIN_LINK":
            msg = await event.respond(f"🔗 **Processing Join...**\n`{text}`")
            login_states.pop(chat_id, None)
            results = []
            for uid, client in active_userbots.items():
                status, rmsg = await join_channel_via_link(client, text)
                icon = "✅" if status else "❌"
                results.append(f"{icon} **{client.me.first_name}**: {rmsg}")
                await asyncio.sleep(random.uniform(1.0, 2.5))
            
            report = f"📝 **Join Report**\n\n" + "\n".join(results)
            if len(report) > 4000: report = report[:4000] + "\n...(truncated)"
            await msg.edit(report, buttons=[[Button.inline("🎙️ Back to Tools", b"menu_tools")]])

        elif step == "JOIN_VC_LINK":
            msg = await event.respond(f"🎙️ **Processing VC Join...**\nLink: `{text}`")
            login_states.pop(chat_id, None)
            results = []
            for uid, client in active_userbots.items():
                j_status, j_msg = await join_channel_via_link(client, text)
                if not j_status and "Already" not in j_msg:
                    results.append(f"❌ **{client.me.first_name}**: Join Chat Failed ({j_msg})")
                    continue
                try:
                    entity = None
                    if '+' in text or 'joinchat' in text:
                        results.append(f"⚠️ **{client.me.first_name}**: Add target ID for private links.")
                        continue
                    else:
                        username = text.split('/')[-1]
                        entity = await client.get_entity(username)
                    if entity:
                        v_status, v_msg = await join_channel_live(client, entity.id)
                        icon = "✅" if v_status else "❌"
                        results.append(f"{icon} **{client.me.first_name}**: {v_msg}")
                    else:
                        results.append(f"❌ **{client.me.first_name}**: Resolve Failed")
                except Exception as e:
                    results.append(f"❌ **{client.me.first_name}**: Error {str(e)[:30]}")
                await asyncio.sleep(random.uniform(1.0, 2.5))

            report = f"📝 **VC Join Report**\n\n" + "\n".join(results)
            if len(report) > 4000: report = report[:4000] + "\n...(truncated)"
            await msg.edit(report, buttons=[[Button.inline("🎙️ Back to Tools", b"menu_tools")]])

        elif step == "LEAVE_LINK":
            msg = await event.respond(f"🚪 **Processing Mass Leave...**\n`{text}`")
            login_states.pop(chat_id, None)
            results = []
            for uid, client in active_userbots.items():
                try:
                    if '+' in text or 'joinchat' in text:
                        results.append(f"⚠️ **{client.me.first_name}**: Cannot leave via private invite link.")
                        continue
                        
                    username = text.split('/')[-1]
                    entity = await client.get_entity(username)
                    await client(LeaveChannelRequest(entity))
                    results.append(f"✅ **{client.me.first_name}**: Successfully Left")
                except Exception as e:
                    results.append(f"❌ **{client.me.first_name}**: Error ({str(e)[:25]})")
                await asyncio.sleep(random.uniform(1.0, 2.5))
                
            report = f"📝 **Leave Chat Report**\n\n" + "\n".join(results)
            if len(report) > 4000: report = report[:4000] + "\n...(truncated)"
            await msg.edit(report, buttons=[[Button.inline("🎙️ Back to Tools", b"menu_tools")]])

        elif step == "ADD_TARGET":
            try:
                target_id = int(text)
                await db.add_target_chat(target_id, event.sender_id) 
                await refresh_global_config()
                login_states.pop(chat_id, None)
                
                chat_name = "Unknown Chat"
                if active_userbots:
                    client = list(active_userbots.values())[0]
                    try:
                        entity = await client.get_entity(target_id)
                        chat_name = getattr(entity, 'title', "Unknown Chat")
                    except: pass

                await event.respond(f"✅ **Target Added:**\n📢 **{chat_name}** (`{target_id}`)", buttons=[[Button.inline("🎯 Target Menu", b"target_menu")]])
            except ValueError:
                await event.respond("❌ Invalid ID.")

        elif step == "STRING":
            try:
                temp_client = TelegramClient(StringSession(text), API_ID, API_HASH)
                await temp_client.connect()
                if await temp_client.is_user_authorized():
                    user = await temp_client.get_me()
                    await db.add_session(user.id, user.first_name, text)
                    await temp_client.disconnect()
                    login_states.pop(chat_id, None)
                    await event.respond(f"✅ **Success!** Added: {user.first_name}", buttons=[[Button.inline("🤖 Back to Bots", b"menu_bots")]])
                    await reload_userbots()
                else:
                    await event.respond("❌ Invalid Session.")
                    await temp_client.disconnect()
            except Exception as e:
                await event.respond(f"❌ Error: {e}")

        elif step == "PHONE":
            temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
            await temp_client.connect()
            try:
                await temp_client.send_code_request(text)
                state['client'] = temp_client
                state['phone'] = text
                state['step'] = "CODE"
                await event.respond("📩 **Enter Code:**", buttons=[[Button.inline("❌ Cancel", b"menu_bots")]])
            except Exception as e:
                await event.respond(f"❌ Error: {e}")
                await temp_client.disconnect()

        elif step == "CODE":
            temp_client = state['client']
            phone = state['phone']
            try:
                code = text.replace(' ', '')
                await temp_client.sign_in(phone, code)
                user = await temp_client.get_me()
                session_str = StringSession.save(temp_client.session)
                await db.add_session(user.id, user.first_name, session_str)
                await temp_client.disconnect()
                login_states.pop(chat_id, None)
                await event.respond(f"✅ **Login Success!**\nAdded: {user.first_name}", buttons=[[Button.inline("🤖 Back to Bots", b"menu_bots")]])
                await reload_userbots()
            except SessionPasswordNeededError:
                state['step'] = "PASSWORD"
                await event.respond("🔐 **2FA Password Required:**", buttons=[[Button.inline("❌ Cancel", b"menu_bots")]])
            except Exception as e:
                await event.respond(f"❌ Error: {e}")
                await temp_client.disconnect()

        elif step == "PASSWORD":
            temp_client = state['client']
            try:
                await temp_client.sign_in(password=text)
                user = await temp_client.get_me()
                session_str = StringSession.save(temp_client.session)
                await db.add_session(user.id, user.first_name, session_str)
                await temp_client.disconnect()
                login_states.pop(chat_id, None)
                await event.respond(f"✅ **Login Success!**\nAdded: {user.first_name}", buttons=[[Button.inline("🤖 Back to Bots", b"menu_bots")]])
                await reload_userbots()
            except Exception as e:
                await event.respond(f"❌ Error: {e}")
                await temp_client.disconnect()

    except Exception as e:
        logger.error(traceback.format_exc())
        await event.respond("❌ Error occurred. Check logs.")

@bot.on(events.CallbackQuery(pattern="react_random"))
async def handle_random_emoji(event):
    if not is_admin(event.sender_id): return
    chat_id = event.chat_id
    if chat_id in login_states and login_states[chat_id].get('step') == "ONETIME_REACT_EMOJI":
        state = login_states[chat_id]
        link = state['link']
        count = state['count']
        login_states.pop(chat_id, None)
        msg = await event.edit("⏳ **Processing Random Reactions...** *(Batched mode)*")
        await process_one_time_engagement(link, count, "react", EMOJIS)
        await msg.edit("✅ Background queuing complete.", buttons=[[Button.inline("❤️ Back to Reacts Menu", b"reacts_menu")]])

async def shutdown(sig, loop):
    logger.info(f"🛑 Received graceful exit signal {sig.name}. Disconnecting all bots...")
    tasks = []
    for client in active_userbots.values():
        client._vc_stop_flag = True
        tasks.append(client.disconnect())
    
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        
    try: await bot.disconnect()
    except: pass
    logger.info("🛑 Clean shutdown complete.")
    os._exit(0)
    
def global_exception_handler(loop, context):
    msg = context.get("message", "")
    if "Task was destroyed but it is pending" in msg: return
    if "coroutine ignored GeneratorExit" in str(context.get("exception", "")): return
    loop.default_exception_handler(context)

async def main():
    print("--- Manager Bot V3 Starting ---")
    print("⏳ Waiting 15s for old Heroku dynos to terminate to prevent IP-Ban overlaps...")
    await asyncio.sleep(15)
    
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(global_exception_handler)
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, loop)))
        
    await bot.start()
    await refresh_global_config()
    print(f"👑 Owner ID: {OWNER_ID}")
    
    count = await reload_userbots()
    print(f"✅ Loaded {count} Userbots")
    
    asyncio.create_task(global_vc_manager())
    asyncio.create_task(ban_notifier_engine())
    
    await log_to_channel(f"🚀 **System Online**\nBots: `{count}`\nTargets: `{len(GLOBAL_CONF.get('target_chats', []))}`")
    
    try:
        await bot.run_until_disconnected()
    finally:
        for client in active_userbots.values():
            client._vc_stop_flag = True
            try: await client.disconnect() 
            except: pass

if __name__ == '__main__':
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n⚠️ Bot stopped manually")
