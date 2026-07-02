import asyncio
import logging
import html
import time
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import (
    ChatJoinRequest, ChatMemberUpdated, InlineKeyboardMarkup,
    InlineKeyboardButton, Message, CallbackQuery,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from pyrogram.errors import FloodWait, SessionPasswordNeeded, MessageNotModified

# Raw API imports to bypass PEER_ID_INVALID
from pyrogram.raw.functions.users import GetUsers
from pyrogram.raw.types import InputUser

from config import API_ID, API_HASH, BOT_TOKEN, ADMINS
from database import db

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Premium Emoji Constants
E_TICK = '<emoji id="6120898777046847624">✅</emoji>'
E_COOL = '<emoji id="6118484116368264657">😎</emoji>'
E_SHOCK = '<emoji id="6224138623927717923">😱</emoji>'
E_FLASH = '<emoji id="5938206901387924708">⚡️</emoji>'
E_BULLHORN = '<emoji id="6068631133883471225">📢</emoji>'
E_CHART = '<emoji id="5938352659693056349">📈</emoji>'
E_DOWN = '<emoji id="5470177992950946662">👇</emoji>'
E_PARTY = '<emoji id="6224161941305169199">🎉</emoji>'

app = Client("join-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=ParseMode.HTML)
userbot = None  # Global Userbot instance
admin_states = {}

# Cooldown trackers for Admin Alerts
last_flood_warning = 0
last_voice_warning = 0

# Cache bot username for verification button
BOT_USERNAME = None

async def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await app.get_me()
        BOT_USERNAME = me.username
    return BOT_USERNAME

# --- ADVANCED ERROR HANDLER & ADMIN ALERTER ---
async def handle_userbot_error(client: Client, e: Exception, context_info: str):
    global last_flood_warning, last_voice_warning
    err_str = str(e)
    current_time = time.time()
    
    if "VOICE_MESSAGES_FORBIDDEN" in err_str:
        logger.error(f"❌ Userbot Blocked ({context_info}): Target restricts Voice/Video messages.")
        if current_time - last_voice_warning > 3600: # 1 Hour Cooldown
            warning_text = (
                "⚠️ <b>USERBOT MEDIA ALERT</b> ⚠️\n\n"
                "Your Userbot failed to send a sequence message. The joining user has privacy settings that block Voice/Video messages from non-contacts.\n\n"
                "<b>Recommended Fixes:</b>\n"
                "1. Remove voice/video notes from the Userbot Sequence.\n"
                "2. Upgrade the Userbot account to Telegram Premium to bypass some privacy restrictions.\n\n"
                "<i>(This alert is muted for 1 hour)</i>"
            )
            for admin in ADMINS:
                try:
                    await client.send_message(admin, warning_text, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            last_voice_warning = current_time

    elif "PEER_FLOOD" in err_str:
        logger.error(f"❌ Userbot Limited ({context_info}): PEER_FLOOD! Account restricted by Telegram.")
        if current_time - last_flood_warning > 900: # 15 Minute Cooldown
            warning_text = (
                "🚨 <b>USERBOT CRITICAL ALERT</b> 🚨\n\n"
                "Telegram has restricted your Userbot account with a <b>PEER_FLOOD</b> error. It is currently blocked from messaging new users due to spam limits.\n\n"
                "<b>Recommended Actions:</b>\n"
                "1. <b>Stop Userbot:</b> Go to your Admin Panel and disable Userbot features to prevent permanent bans.\n"
                "2. <b>Upgrade:</b> Switch to an older, more trusted account, or upgrade the account to Telegram Premium.\n"
                "3. <b>Wait:</b> Telegram usually lifts this temporary restriction in 12-24 hours.\n\n"
                "<i>(This alert is muted for 15 minutes)</i>"
            )
            for admin in ADMINS:
                try:
                    await client.send_message(admin, warning_text, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            last_flood_warning = current_time

    else:
        # Generic error fallback
        logger.error(f"❌ Userbot Error ({context_info}): {err_str}")

# --- DYNAMIC STARTUP FOR USERBOT ---
async def start_userbot():
    global userbot
    settings = await db.get_settings()
    session = settings.get("userbot_session")
    if session:
        if userbot and userbot.is_connected:
            await userbot.stop()
        try:
            userbot = Client("userbot", session_string=session, api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await userbot.start()
            logger.info("✅ Userbot started successfully.")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to start userbot: {e}")
            userbot = None
    return False

# --- TGDNA DASHBOARD FETCHER ---
async def fetch_tgdna_info(client: Client):
    try:
        await client.send_message("@TGDNAbot", "/start")
        await asyncio.sleep(2.5)
        async for msg in client.get_chat_history("@TGDNAbot", limit=2):
            if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == "tgdnabot" and msg.text:
                lines = msg.text.split('\n')
                formatted = []
                for line in lines:
                    if "Username:" in line: continue
                    if "ID:" in line: line = f"🆔 {line}"
                    elif "Name:" in line: line = f"👤 {line}"
                    elif "DC:" in line: line = f"🌍 {line}"
                    elif "Premium:" in line: line = f"⭐️ {line}"
                    elif "Language:" in line: line = f"🗣 {line}"
                    elif "Account Age:" in line: line = f"⏳ {line}"
                    elif "Date:" in line: line = f"📅 {line}"
                    elif "Status:" in line: line = f"📡 {line}"
                    elif "Scam" in line or "Fake" in line: line = f"⚠️ {line}"
                    formatted.append(line)
                return "\n".join(formatted)
        return "❌ No response received from @TGDNAbot."
    except Exception as e:
        return f"❌ Error fetching details: {e}"

# --- ADMIN UI GENERATORS ---
async def get_main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Bot Configurations", callback_data="bot_settings")],
        [InlineKeyboardButton("🤖 Userbot Module", callback_data="userbot_menu")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="ask_broadcast"), InlineKeyboardButton("📊 Statistics", callback_data="view_stats")]
    ])

async def get_bot_settings_kb():
    settings = await db.get_settings()
    auto_app_text = "🟢 Auto-Approve" if settings.get("auto_approve", True) else "🔴 Auto-Approve"
    acc_msg_text = "🟢 Accept Msg" if settings.get("send_acceptance_msg", True) else "🔴 Accept Msg"
    leave_msg_text = "🟢 Leave Msg" if settings.get("send_leave_msg", True) else "🔴 Leave Msg"
    bot_seq_text = "🟢 Welcome Sequence" if settings.get("bot_sequence_enabled", True) else "🔴 Welcome Sequence"
    seq_count = len(settings.get("welcome_sequence", []))
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(auto_app_text, callback_data="toggle_auto_approve")],
        [
            InlineKeyboardButton(bot_seq_text, callback_data="toggle_bot_seq"),
            InlineKeyboardButton(f"🛠 Edit ({seq_count} msgs)", callback_data="ask_welcome_sequence")
        ],
        [
            InlineKeyboardButton(acc_msg_text, callback_data="toggle_acc_msg"),
            InlineKeyboardButton("📝 Edit Text", callback_data="ask_accept_msg")
        ],
        [
            InlineKeyboardButton(leave_msg_text, callback_data="toggle_leave_msg"),
            InlineKeyboardButton("📝 Edit Text", callback_data="ask_leave_msg")
        ],
        [InlineKeyboardButton("🔗 Edit Rejoin Link", callback_data="ask_rejoin_link"), InlineKeyboardButton("🗑️ Clear Seq", callback_data="clear_sequence")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="admin_panel")]
    ])

async def get_userbot_settings_kb():
    settings = await db.get_settings()
    if settings.get("userbot_session"):
        msg_status = "🟢 Text DM" if settings.get("userbot_msg_enabled", False) else "🔴 Text DM"
        seq_status = "🟢 Sequence" if settings.get("userbot_seq_enabled", False) else "🔴 Sequence"
        ub_seq_count = len(settings.get("userbot_sequence", []))
        
        source_chan = settings.get("ub_source_channel")
        source_text = "🔗 Source: Set" if source_chan else "🔗 Set Source Channel"
        
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Account Dashboard", callback_data="userbot_dashboard")],
            [
                InlineKeyboardButton(msg_status, callback_data="toggle_ub_msg"),
                InlineKeyboardButton("💬 Edit Msg", callback_data="ask_ub_msg")
            ],
            [
                InlineKeyboardButton(seq_status, callback_data="toggle_ub_seq"),
                InlineKeyboardButton(f"🛠 Manual Seq ({ub_seq_count})", callback_data="ask_ub_seq")
            ],
            [
                InlineKeyboardButton(source_text, callback_data="ask_ub_source_channel"),
                InlineKeyboardButton("🔄 Refresh Channel", callback_data="refresh_ub_source")
            ],
            [InlineKeyboardButton("🗑️ Clear Sequence", callback_data="clear_ub_seq")],
            [InlineKeyboardButton("🗑️ Remove Acc", callback_data="remove_ub_account"), InlineKeyboardButton("🚪 Secure Logout", callback_data="logout_ub_account")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="admin_panel")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Login via Phone Number", callback_data="ub_login_phone")],
            [InlineKeyboardButton("🔑 Login via Session String", callback_data="ub_login_session")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="admin_panel")]
        ])

# --- COMMANDS ---
@app.on_message(filters.command(["start", "admin"]) & filters.private)
async def start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    args = message.command[1:] if len(message.command) > 1 else []

    # Verification flow: user clicked "Verify Me" button
    if args and args[0] == "verify":
        await db.verify_user(user_id)
        await message.reply(
            f"{E_TICK} <b>You are now verified!</b>\n\n"
            "You will receive future broadcasts and announcements.",
            parse_mode=ParseMode.HTML
        )
        return

    if user_id in ADMINS:
        await message.reply(
            f"🎛 <b>Admin Control Panel</b>\n\nWelcome to the dashboard. Please select a module below to configure your bot.",
            reply_markup=await get_main_menu_kb(),
            parse_mode=ParseMode.HTML
        )
    else:
        await db.add_user(user_id)
        await message.reply("Hello! I am a Join Request Manager bot. Please request to join the channel to continue.")

# --- 1. JOIN REQUEST HANDLER ---
@app.on_chat_join_request()
async def join_request_handler(client: Client, request: ChatJoinRequest):
    user_id = request.from_user.id
    name = request.from_user.first_name or "User"
    chat = request.chat

    await db.add_user(user_id)
    settings = await db.get_settings()
    safe_name = html.escape(name)
    safe_title = html.escape(chat.title)
    link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else "")
    tag = f"<a href='{link}'>{safe_title}</a>" if link else f"<b>{safe_title}</b>"

    # Build verification button (URL to bot with /start verify)
    bot_username = await get_bot_username()
    verify_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Verify Me", url=f"https://t.me/{bot_username}?start=verify")]
    ])

    try:
        if settings.get("auto_approve", True):
            await request.approve()

            # Bot Acceptance Message
            if settings.get("send_acceptance_msg", True):
                default_accept = f"{E_TICK} <b>Hello {{name}}!</b>\n\nYour request to join {{tag}} has been accepted. {E_PARTY}"
                raw_text = settings.get("accept_msg_text", default_accept)
                accept_text = raw_text.replace("{name}", safe_name).replace("{tag}", tag)

                try:
                    await client.send_message(
                        chat_id=user_id,
                        text=accept_text,
                        disable_web_page_preview=True,
                        parse_mode=ParseMode.HTML,
                        reply_markup=verify_kb
                    )
                except Exception as e:
                    logger.error(f"❌ Failed sending accept msg: {e}")
                
                await asyncio.sleep(0.5)

        # Userbot DM Section
        if userbot and userbot.is_connected:
            target = user_id
            
            if request.from_user.username:
                target = request.from_user.username
            else:
                # --- FIX: PEER_ID_INVALID Cache Warmup ---
                await asyncio.sleep(2.0)
                
                if settings.get("auto_approve", True):
                    try:
                        await userbot.get_chat_member(chat.id, user_id)
                    except Exception:
                        pass
                
                try:
                    bot_peer = await client.resolve_peer(user_id)
                    if hasattr(bot_peer, "access_hash"):
                        input_user = InputUser(user_id=bot_peer.user_id, access_hash=bot_peer.access_hash)
                        raw_users = await client.invoke(GetUsers(id=[input_user]))
                        if raw_users:
                            await userbot.storage.update_peers(raw_users)
                except Exception as peer_e:
                    pass

            # Userbot Custom Text Message
            if settings.get("userbot_msg_enabled", False):
                ub_raw = settings.get("userbot_msg_text", "Hey {name}! 👋")
                ub_text = ub_raw.replace("{name}", safe_name).replace("{tag}", tag)
                try:
                    await userbot.send_message(
                        chat_id=target,
                        text=ub_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=verify_kb
                    )
                    await asyncio.sleep(0.5)
                except Exception as e:
                    await handle_userbot_error(client, e, f"Text DM to {user_id}")

            # Userbot Welcome Sequence
            if settings.get("userbot_seq_enabled", False):
                ub_seq = settings.get("userbot_sequence", [])
                for item in ub_seq:
                    try:
                        await userbot.copy_message(chat_id=target, from_chat_id="me", message_id=item["msg_id"])
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        await handle_userbot_error(client, e, f"Seq Msg {item['msg_id']} to {user_id}")

        # Welcome sequence (Bot)
        if settings.get("bot_sequence_enabled", True):
            sequence = settings.get("welcome_sequence", [])
            for item in sequence:
                try:
                    await client.copy_message(chat_id=user_id, from_chat_id=item["chat_id"], message_id=item["msg_id"])
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"❌ Bot failed to copy msg {item['msg_id']}: {e}")

    except Exception as e:
        logger.error(f"❌ Failed processing user {user_id}: {e}")

# --- 2. LEAVE TRACKER ---
@app.on_chat_member_updated()
async def track_leaves(client: Client, update: ChatMemberUpdated):
    old = update.old_chat_member
    new = update.new_chat_member
    user = new.user if new else (old.user if old else None)
    
    if not user or user.is_self:
        return

    old_status = old.status if old else None
    new_status = new.status if new else None

    was_in = old_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]
    is_out = new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, None]

    if was_in and is_out:
        user_id = user.id
        name = user.first_name or "User"
        chat = update.chat
        
        settings = await db.get_settings()
        if not settings.get("send_leave_msg", True):
            return
            
        custom_link = settings.get("rejoin_link")
        final_link = None
        
        if custom_link and custom_link.upper() != "NONE":
            final_link = custom_link
        else:
            final_link = chat.invite_link
            if not final_link:
                try:
                    invite_obj = await client.create_chat_invite_link(chat.id)
                    final_link = invite_obj.invite_link
                    await db.update_settings("rejoin_link", final_link)
                except Exception:
                    final_link = ""

        safe_name = html.escape(name)
        safe_title = html.escape(chat.title)
        tag = f"<a href='{final_link}'>{safe_title}</a>" if final_link else f"<b>{safe_title}</b>"
        
        default_leave = f"{E_SHOCK} <b>Oh no {{name}}!</b> We noticed you left {{tag}}.\n\nIf it was a mistake or you want to return and keep earning, you can quickly rejoin using the button below! {E_DOWN}"
        raw_text = settings.get("leave_msg_text", default_leave)
        text = raw_text.replace("{name}", safe_name).replace("{tag}", tag)
        
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Join Again", url=final_link)]]) if final_link else None
        
        try:
            await client.send_message(chat_id=user_id, text=text, disable_web_page_preview=True, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await client.send_message(chat_id=user_id, text=text, disable_web_page_preview=True, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception:
            pass

# --- ADMIN PANEL CALLBACKS ---
@app.on_callback_query(filters.user(ADMINS))
async def admin_callbacks(client: Client, call: CallbackQuery):
    global userbot
    user_id = call.from_user.id
    data = call.data

    if data == "admin_panel":
        if user_id in admin_states and "temp_client" in admin_states[user_id]:
            await admin_states[user_id]["temp_client"].disconnect()
        admin_states.pop(user_id, None)
        try:
            await call.message.edit_text(f"🎛 <b>Admin Control Panel</b>\n\nWelcome to the dashboard. Please select a module below to configure your bot.", reply_markup=await get_main_menu_kb(), parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    elif data == "bot_settings":
        try:
            await call.message.edit_text(f"⚙️ <b>Bot Configurations</b>\nManage auto-approvals, welcome sequences, and leave messages.", reply_markup=await get_bot_settings_kb(), parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    elif data == "toggle_auto_approve":
        settings = await db.get_settings()
        await db.update_settings("auto_approve", not settings.get("auto_approve", True))
        try:
            await call.message.edit_reply_markup(await get_bot_settings_kb())
        except MessageNotModified:
            pass
        await call.answer("Setting updated!")
        
    elif data == "toggle_bot_seq":
        settings = await db.get_settings()
        await db.update_settings("bot_sequence_enabled", not settings.get("bot_sequence_enabled", True))
        try:
            await call.message.edit_reply_markup(await get_bot_settings_kb())
        except MessageNotModified:
            pass
        await call.answer("Setting updated!")
        
    elif data == "toggle_acc_msg":
        settings = await db.get_settings()
        await db.update_settings("send_acceptance_msg", not settings.get("send_acceptance_msg", True))
        try:
            await call.message.edit_reply_markup(await get_bot_settings_kb())
        except MessageNotModified:
            pass
        await call.answer("Setting updated!")
        
    elif data == "toggle_leave_msg":
        settings = await db.get_settings()
        await db.update_settings("send_leave_msg", not settings.get("send_leave_msg", True))
        try:
            await call.message.edit_reply_markup(await get_bot_settings_kb())
        except MessageNotModified:
            pass
        await call.answer("Setting updated!")
    
    # --- USERBOT CALLBACKS ---
    elif data == "userbot_menu":
        settings = await db.get_settings()
        try:
            if settings.get("userbot_session"):
                await call.message.edit_text("🤖 <b>Userbot Module</b>\nManage your connected account and setup DMs.", reply_markup=await get_userbot_settings_kb(), parse_mode=ParseMode.HTML)
            else:
                await call.message.edit_text("🤖 <b>Add Userbot Account</b>\nChoose a method to connect an account:", reply_markup=await get_userbot_settings_kb(), parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    elif data == "toggle_ub_msg":
        settings = await db.get_settings()
        await db.update_settings("userbot_msg_enabled", not settings.get("userbot_msg_enabled", False))
        try:
            await call.message.edit_reply_markup(await get_userbot_settings_kb())
        except MessageNotModified:
            pass
        await call.answer("Setting updated!")

    elif data == "toggle_ub_seq":
        settings = await db.get_settings()
        await db.update_settings("userbot_seq_enabled", not settings.get("userbot_seq_enabled", False))
        try:
            await call.message.edit_reply_markup(await get_userbot_settings_kb())
        except MessageNotModified:
            pass
        await call.answer("Setting updated!")

    elif data == "ask_ub_msg":
        admin_states[user_id] = {"state": "WAITING_UB_MSG"}
        await call.message.edit_text(
            "💬 <b>Set Userbot Welcome Message</b>\n\nSend the text you want the Userbot to DM upon joining. Emojis supported.\n\nPlaceholders: <code>{name}</code>, <code>{tag}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="userbot_menu")]]), parse_mode=ParseMode.HTML
        )
        await call.answer()
        
    elif data == "ask_ub_seq":
        admin_states[user_id] = {"state": "WAITING_UB_SEQ", "msgs": []}
        await client.send_message(
            user_id,
            f"<b>🛠 Setup Userbot Sequence</b>\n\n"
            f"Send or forward <b>all the messages</b> you want the Userbot to send (Premium Emojis supported!).\n"
            f"{E_DOWN} Send files now. Click <b>✅ Finish Setup</b> when done.",
            reply_markup=ReplyKeyboardMarkup([["✅ Finish Setup"]], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        await call.message.delete()

    elif data == "ask_ub_source_channel":
        admin_states[user_id] = {"state": "WAITING_UB_SOURCE"}
        await call.message.edit_text(
            "🔗 <b>Set Source Channel for Userbot</b>\n\n"
            "Please send the Channel ID (e.g., <code>-1001234567890</code>) or Username (e.g., <code>@MyChannel</code>).\n\n"
            "<i>Note: Make sure the Userbot account is an Admin or joined in this channel!</i>\n"
            "To clear it, send <code>NONE</code>.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="userbot_menu")]]),
            parse_mode=ParseMode.HTML
        )
        await call.answer()
        
    elif data == "refresh_ub_source":
        settings = await db.get_settings()
        source_chan = settings.get("ub_source_channel")
        
        if not source_chan:
            return await call.answer("❌ No source channel set! Set it first.", show_alert=True)
            
        if not userbot or not userbot.is_connected:
            return await call.answer("❌ Userbot is not connected!", show_alert=True)
            
        try:
            await call.message.edit_text("⏳ <i>Fetching messages from source channel and caching to Userbot...</i>", parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass
        
        try:
            try:
                chat_id = int(source_chan)
            except ValueError:
                chat_id = source_chan

            if isinstance(chat_id, int):
                try:
                    await userbot.get_chat(chat_id)
                except Exception as e:
                    if "Peer id invalid" in str(e) or "PEER_ID_INVALID" in str(e):
                        try:
                            await call.message.edit_text("⏳ <i>Syncing Userbot cache for private channel... (This may take a moment)</i>", parse_mode=ParseMode.HTML)
                        except MessageNotModified:
                            pass
                        async for _ in userbot.get_dialogs():
                            pass
            
            messages = []
            async for msg in userbot.get_chat_history(chat_id, limit=30):
                if not msg.service:
                    messages.append(msg)
            
            if not messages:
                return await call.message.edit_text(
                    "❌ No valid messages found in the channel.", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="userbot_menu")]])
                )
            
            messages.reverse() 
            cached_msgs = []
            for msg in messages:
                try:
                    saved_msg = await msg.copy(chat_id="me")
                    cached_msgs.append({"msg_id": saved_msg.id})
                    await asyncio.sleep(1) 
                except Exception as e:
                    logger.error(f"Cache error: {e}")
            
            await db.update_settings("userbot_sequence", cached_msgs)
            try:
                await call.message.edit_text(
                    f"✅ <b>Sequence Refreshed!</b>\n\nSuccessfully fetched and cached {len(cached_msgs)} messages from the source channel.", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="userbot_menu")]]), 
                    parse_mode=ParseMode.HTML
                )
            except MessageNotModified:
                pass
            
        except Exception as e:
            try:
                await call.message.edit_text(
                    f"❌ <b>Error fetching channel:</b>\n<code>{e}</code>\n\nMake sure the Userbot is an admin or member of the channel.", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="userbot_menu")]]), 
                    parse_mode=ParseMode.HTML
                )
            except MessageNotModified:
                pass
        
    elif data == "clear_ub_seq":
        await db.update_settings("userbot_sequence", [])
        await call.answer("Userbot sequence cleared!", show_alert=True)
        try:
            await call.message.edit_reply_markup(await get_userbot_settings_kb())
        except MessageNotModified:
            pass

    elif data == "remove_ub_account":
        await db.update_settings("userbot_session", None)
        await db.update_settings("userbot_msg_enabled", False)
        await db.update_settings("userbot_seq_enabled", False)
        
        if userbot and userbot.is_connected:
            await userbot.stop()
        userbot = None
        await call.answer("Account removed successfully!", show_alert=True)
        try:
            await call.message.edit_text("🤖 <b>Add Userbot Account</b>\nChoose a method to connect an account:", reply_markup=await get_userbot_settings_kb(), parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    elif data == "logout_ub_account":
        try:
            await call.message.edit_text("⏳ <i>Securely logging out from Telegram servers...</i>", parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

        if userbot and userbot.is_connected:
            try:
                await userbot.log_out()
            except Exception as e:
                logger.error(f"Error logging out userbot: {e}")
        userbot = None
        
        await db.update_settings("userbot_session", None)
        await db.update_settings("userbot_msg_enabled", False)
        await db.update_settings("userbot_seq_enabled", False)
        
        await call.answer("Session securely terminated and logged out!", show_alert=True)
        try:
            await call.message.edit_text("🤖 <b>Add Userbot Account</b>\nChoose a method to connect an account:", reply_markup=await get_userbot_settings_kb(), parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    elif data == "ub_login_phone":
        admin_states[user_id] = {"state": "WAITING_UB_PHONE"}
        await call.message.edit_text("📱 <b>Phone Login</b>\n\nPlease send the phone number with country code (e.g., +1234567890).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="userbot_menu")]]), parse_mode=ParseMode.HTML)
        await call.answer()

    elif data == "ub_login_session":
        admin_states[user_id] = {"state": "WAITING_UB_SESSION"}
        await call.message.edit_text("🔑 <b>Session String Login</b>\n\nPlease send your Pyrogram Session String.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="userbot_menu")]]), parse_mode=ParseMode.HTML)
        await call.answer()

    elif data == "userbot_dashboard":
        try:
            await call.message.edit_text("⏳ <b>Fetching details from @TGDNAbot...</b>\nThis might take a few seconds.", parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

        if userbot and userbot.is_connected:
            info = await fetch_tgdna_info(userbot)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="userbot_dashboard")],
                [InlineKeyboardButton("🔙 Back to Settings", callback_data="userbot_menu")]
            ])
            try:
                await call.message.edit_text(f"🤖 <b>Userbot Dashboard</b>\n\n{info}", reply_markup=kb, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass
            await call.answer()
        else:
            await call.message.edit_text("❌ Userbot is not connected. Try reconnecting.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="userbot_menu")]]))

    # Standard Config Calls
    elif data == "ask_accept_msg":
        admin_states[user_id] = {"state": "WAITING_ACCEPT_MSG"}
        await call.message.edit_text(
            f"📝 <b>Set Custom Acceptance Message</b>\n\n"
            f"Send the new text. <b>You can include Premium Emojis!</b> {E_FLASH}\n\n"
            f"Available placeholders:\n"
            f"<code>{{name}}</code> - User's name\n"
            f"<code>{{tag}}</code> - Channel Name & Link\n\n"
            f"(Send <code>DEFAULT</code> to reset to original)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="bot_settings")]]),
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data == "ask_leave_msg":
        admin_states[user_id] = {"state": "WAITING_LEAVE_MSG"}
        await call.message.edit_text(
            f"📝 <b>Set Custom Leave Message</b>\n\n"
            f"Send the new text. <b>You can include Premium Emojis!</b> {E_FLASH}\n\n"
            f"Available placeholders:\n"
            f"<code>{{name}}</code> - User's name\n"
            f"<code>{{tag}}</code> - Channel Name & Link\n\n"
            f"(Send <code>DEFAULT</code> to reset to original)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="bot_settings")]]),
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data == "ask_rejoin_link":
        admin_states[user_id] = {"state": "WAITING_REJOIN_LINK"}
        await call.message.edit_text(
            "🔗 <b>Set Custom Rejoin Link</b>\n\n"
            "Send the invite link you want users to use when they leave the channel.\n"
            "(Send <code>NONE</code> to revert to the default channel invite link)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="bot_settings")]]),
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data == "ask_welcome_sequence":
        admin_states[user_id] = {"state": "WAITING_SEQ", "msgs": []}
        await client.send_message(
            user_id,
            f"<b>🛠 Setup Bot Sequence</b>\n\n"
            f"Send or forward <b>all the messages</b> you want the BOT to send (Includes Premium Emojis! {E_FLASH}).\n"
            f"{E_DOWN} Send files now. Click <b>✅ Finish Setup</b> when done.",
            reply_markup=ReplyKeyboardMarkup([["✅ Finish Setup"]], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        await call.message.delete()

    elif data == "clear_sequence":
        await db.update_settings("welcome_sequence", [])
        await call.answer("Bot sequence cleared!", show_alert=True)
        try:
            await call.message.edit_reply_markup(await get_bot_settings_kb())
        except MessageNotModified:
            pass

    elif data == "ask_broadcast":
        admin_states[user_id] = {"state": "WAITING_FOR_BROADCAST"}
        await call.message.edit_text(
            f"{E_BULLHORN} <b>Broadcast Mode</b>\n\nSend the message you want to broadcast (Premium Emojis supported!).\n\n<i>Note: Only verified users will receive this broadcast.</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")]]),
            parse_mode=ParseMode.HTML
        )
        await call.answer()

    elif data == "view_stats":
        total = await db.total_users()
        verified = await db.users.count_documents({"verified": True})
        await call.answer(f"📊 Total: {total} | Verified: {verified}", show_alert=True)

    elif data == "confirm_broadcast":
        state = admin_states.get(user_id, {})
        msg_id = state.get("broadcast_msg_id")
        if not msg_id:
            return await call.answer("Session expired. Try again.", show_alert=True)
        try:
            await call.message.edit_text(f"⏳ <b>Broadcast started...</b>", parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass
        asyncio.create_task(run_broadcast(client, user_id, call.message.chat.id, msg_id, call.message.id))
        admin_states.pop(user_id, None)
        await call.answer()

    elif data == "cancel_broadcast":
        admin_states.pop(user_id, None)
        await call.message.edit_text("❌ Broadcast cancelled.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="admin_panel")]]))
        await call.answer()

# --- ADMIN INPUT CAPTURE (FSM) ---
@app.on_message(filters.private & filters.user(ADMINS) & ~filters.command(["start", "admin"]))
async def admin_input_handler(client: Client, message: Message):
    user_id = message.from_user.id
    state_data = admin_states.get(user_id)

    if not state_data:
        return

    state = state_data.get("state")
    back_bot = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Bot Settings", callback_data="bot_settings")]])
    back_ub = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Userbot Settings", callback_data="userbot_menu")]])

    # --- USERBOT CONFIGURATIONS ---
    if state == "WAITING_UB_MSG":
        html_text = message.text.html if message.text else (message.caption.html if message.caption else "")
        if html_text:
            await db.update_settings("userbot_msg_text", html_text)
            await message.reply("✅ Userbot message saved!", reply_markup=back_ub)
            admin_states.pop(user_id, None)
        else:
            await message.reply("❌ Please send text.", reply_markup=back_ub)

    elif state == "WAITING_UB_SOURCE":
        text = message.text.strip()
        if text.upper() == "NONE":
            await db.update_settings("ub_source_channel", None)
            await message.reply(f"{E_TICK} Source channel cleared.", reply_markup=back_ub, parse_mode=ParseMode.HTML)
        else:
            await db.update_settings("ub_source_channel", text)
            await message.reply(f"{E_TICK} Source channel saved!\n\nNow click <b>'🔄 Refresh Channel'</b> in the Userbot Menu to pull the messages.", reply_markup=back_ub, parse_mode=ParseMode.HTML)
        admin_states.pop(user_id, None)

    # --- USERBOT LOGIN FLOW ---
    elif state == "WAITING_UB_SESSION":
        session_string = message.text.strip()
        msg = await message.reply("⏳ Testing session...")
        try:
            test_client = Client("test", session_string=session_string, api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await test_client.start()
            await test_client.stop()
            await db.update_settings("userbot_session", session_string)
            await start_userbot()
            await msg.edit_text("✅ Account connected successfully!", reply_markup=back_ub)
            admin_states.pop(user_id, None)
        except Exception as e:
            await msg.edit_text(f"❌ Invalid session: {e}", reply_markup=back_ub)

    elif state == "WAITING_UB_PHONE":
        phone = message.text.strip()
        msg = await message.reply("⏳ Sending OTP...")
        try:
            temp_client = Client(f"temp_{user_id}", in_memory=True, api_id=API_ID, api_hash=API_HASH)
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone)
            admin_states[user_id] = {
                "state": "WAITING_UB_OTP",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "temp_client": temp_client
            }
            await msg.edit_text("📩 <b>OTP Sent!</b>\n\nPlease enter the code you received.\n(If it has numbers and letters, just send the numbers. Add a space between numbers if Telegram blocks you, e.g. `1 2 3 4 5`)", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="userbot_menu")]]))
        except Exception as e:
            await msg.edit_text(f"❌ Error sending code: {e}", reply_markup=back_ub)
            if "temp_client" in locals(): await temp_client.disconnect()
            admin_states.pop(user_id, None)

    elif state == "WAITING_UB_OTP":
        otp = message.text.replace(" ", "")
        temp_client = state_data["temp_client"]
        msg = await message.reply("⏳ Verifying OTP...")
        try:
            await temp_client.sign_in(state_data["phone"], state_data["phone_code_hash"], otp)
            session_string = await temp_client.export_session_string()
            await db.update_settings("userbot_session", session_string)
            await temp_client.disconnect()
            await start_userbot()
            await msg.edit_text("✅ Logged in successfully!", reply_markup=back_ub)
            admin_states.pop(user_id, None)
        except SessionPasswordNeeded:
            admin_states[user_id]["state"] = "WAITING_UB_2FA"
            await msg.edit_text("🔐 <b>2-Step Verification required.</b>\n\nPlease send your 2FA password.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="userbot_menu")]]))
        except Exception as e:
            await msg.edit_text(f"❌ Error verifying code: {e}\nTry again.", reply_markup=back_ub)

    elif state == "WAITING_UB_2FA":
        pwd = message.text.strip()
        temp_client = state_data["temp_client"]
        msg = await message.reply("⏳ Verifying Password...")
        try:
            await temp_client.check_password(pwd)
            session_string = await temp_client.export_session_string()
            await db.update_settings("userbot_session", session_string)
            await temp_client.disconnect()
            await start_userbot()
            await msg.edit_text("✅ Logged in successfully!", reply_markup=back_ub)
            admin_states.pop(user_id, None)
        except Exception as e:
            await msg.edit_text(f"❌ Incorrect password: {e}\nTry again.", reply_markup=back_ub)

    # Standard Config Messages
    elif state == "WAITING_ACCEPT_MSG":
        if message.text and message.text.strip().upper() == "DEFAULT":
            await db.update_settings("accept_msg_text", f"{E_TICK} <b>Hello {{name}}!</b>\n\nYour request to join {{tag}} has been accepted. {E_PARTY}")
            await message.reply(f"{E_TICK} Acceptance message reset to default!", reply_markup=back_bot, parse_mode=ParseMode.HTML)
        else:
            html_text = message.text.html if message.text else (message.caption.html if message.caption else "")
            if html_text:
                await db.update_settings("accept_msg_text", html_text)
                await message.reply(f"{E_TICK} Custom Acceptance message saved (Premium Emojis captured)!", reply_markup=back_bot, parse_mode=ParseMode.HTML)
            else:
                await message.reply("❌ Please send text.", reply_markup=back_bot)
        admin_states.pop(user_id, None)

    elif state == "WAITING_LEAVE_MSG":
        if message.text and message.text.strip().upper() == "DEFAULT":
            await db.update_settings("leave_msg_text", f"{E_SHOCK} <b>Oh no {{name}}!</b> We noticed you left {{tag}}.\n\nIf it was a mistake or you want to return and keep earning, you can quickly rejoin using the button below! {E_DOWN}")
            await message.reply(f"{E_TICK} Leave message reset to default!", reply_markup=back_bot, parse_mode=ParseMode.HTML)
        else:
            html_text = message.text.html if message.text else (message.caption.html if message.caption else "")
            if html_text:
                await db.update_settings("leave_msg_text", html_text)
                await message.reply(f"{E_TICK} Custom Leave message saved (Premium Emojis captured)!", reply_markup=back_bot, parse_mode=ParseMode.HTML)
            else:
                await message.reply("❌ Please send text.", reply_markup=back_bot)
        admin_states.pop(user_id, None)

    elif state == "WAITING_REJOIN_LINK":
        text = message.text.strip()
        if text.upper() == "NONE":
            await db.update_settings("rejoin_link", None)
            await message.reply(f"{E_TICK} Rejoin link cleared. I will use the default channel link.", reply_markup=back_bot, parse_mode=ParseMode.HTML)
        else:
            await db.update_settings("rejoin_link", text)
            await message.reply(f"{E_TICK} Custom Rejoin link saved!\nLink: {text}", reply_markup=back_bot, parse_mode=ParseMode.HTML)
        admin_states.pop(user_id, None)

    elif state == "WAITING_SEQ":
        if message.text == "✅ Finish Setup":
            sequence = state_data["msgs"]
            await db.update_settings("welcome_sequence", sequence)
            admin_states.pop(user_id, None)
            await message.reply(f"{E_TICK} <b>Bot Sequence Saved!</b> ({len(sequence)} msgs)", reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML)
            await message.reply(f"⚙️ <b>Bot Configurations</b>", reply_markup=await get_bot_settings_kb(), parse_mode=ParseMode.HTML)
        else:
            state_data["msgs"].append({"chat_id": message.chat.id, "msg_id": message.id})

    elif state == "WAITING_UB_SEQ":
        if message.text == "✅ Finish Setup":
            sequence = state_data["msgs"]
            await db.update_settings("userbot_sequence", sequence)
            admin_states.pop(user_id, None)
            await message.reply(f"{E_TICK} <b>Userbot Sequence Saved!</b> ({len(sequence)} msgs)\n\n<i>Note: Messages are securely cached in your Userbot's 'Saved Messages'.</i>", reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML)
            await message.reply(f"🤖 <b>Userbot Module</b>", reply_markup=await get_userbot_settings_kb(), parse_mode=ParseMode.HTML)
        else:
            if not userbot or not userbot.is_connected:
                await message.reply("❌ Userbot is not connected. Please connect it first to configure its sequence.", reply_markup=ReplyKeyboardRemove())
                admin_states.pop(user_id, None)
                return
                
            msg = await message.reply("⏳ <i>Processing and caching media...</i>", parse_mode=ParseMode.HTML)
            try:
                bot_me = await client.get_me()
                ub_me = await userbot.get_me()

                try:
                    await userbot.resolve_peer(bot_me.username)
                except Exception as e:
                    pass

                await message.copy(chat_id=ub_me.id)
                await asyncio.sleep(1.5)
                
                ub_msg_id = None
                async for ub_msg in userbot.get_chat_history(bot_me.id, limit=1):
                    ub_msg_id = ub_msg.id
                    break
                    
                if ub_msg_id:
                    saved_msg = await userbot.copy_message(chat_id="me", from_chat_id=bot_me.id, message_id=ub_msg_id)
                    state_data["msgs"].append({"msg_id": saved_msg.id})
                    
                    await userbot.delete_messages(bot_me.id, ub_msg_id)
                    await msg.delete()
                else:
                    await msg.edit_text("❌ Failed to cache the media. History fetch failed.")
            except Exception as e:
                await msg.edit_text(f"❌ Error during caching: {e}")

    elif state == "WAITING_FOR_BROADCAST":
        state_data["broadcast_msg_id"] = message.id
        confirm_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Send to Everyone", callback_data="confirm_broadcast")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")]
        ])
        await message.copy(chat_id=user_id, reply_markup=message.reply_markup)
        await message.reply("⚠️ <b>BROADCAST PREVIEW</b>\nIs this exactly what you want to send?\n\n<i>This will only be sent to verified users.</i>", reply_markup=confirm_kb, parse_mode=ParseMode.HTML)

# --- BACKGROUND BROADCAST LOGIC (only verified users) ---
async def run_broadcast(client: Client, admin_id: int, from_chat_id: int, message_id: int, progress_msg_id: int):
    # Get only verified users
    users = db.get_all_verified_users()
    total_users = await db.users.count_documents({"verified": True})
    success = 0
    failed = 0
    
    async for user in users:
        try:
            await client.copy_message(chat_id=user["_id"], from_chat_id=from_chat_id, message_id=message_id)
            success += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            await client.copy_message(chat_id=user["_id"], from_chat_id=from_chat_id, message_id=message_id)
            success += 1
        except Exception:
            failed += 1
            
        if (success + failed) % 20 == 0:
            try:
                await client.edit_message_text(admin_id, progress_msg_id, f"{E_FLASH} <b>Broadcasting...</b>\n{E_CHART} Total: {total_users}\n{E_TICK} Sent: {success}\n❌ Failed: {failed}", parse_mode=ParseMode.HTML)
            except Exception:
                pass

    try:
        await client.edit_message_text(admin_id, progress_msg_id, f"{E_TICK} <b>Broadcast Done!</b>\n{E_CHART} Total: {total_users}\n{E_TICK} Sent: {success}\n❌ Failed: {failed}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Main Menu", callback_data="admin_panel")]]), parse_mode=ParseMode.HTML)
    except MessageNotModified:
        pass

# --- SHUTDOWN & EVENT LOOP ---
async def shutdown():
    logger.info("Initiating graceful shutdown (Timeout protected)...")
    if userbot and userbot.is_connected:
        try:
            await asyncio.wait_for(userbot.stop(), timeout=5.0)
            logger.info("Userbot stopped safely.")
        except Exception as e:
            logger.warning(f"Userbot stop timed out/errored: {e}")
            
    try:
        await asyncio.wait_for(app.stop(), timeout=5.0)
        logger.info("Main bot stopped safely.")
    except Exception as e:
        logger.warning(f"Main bot stop timed out/errored: {e}")

def global_exception_handler(loop, context):
    exception = context.get('exception')
    if isinstance(exception, ValueError) and "Peer id invalid" in str(exception):
        logger.debug(f"Suppressed Pyrogram core error: {exception}")
        return
    loop.default_exception_handler(context)

async def main_loop():
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(global_exception_handler)

    logger.info("Starting Bot Platform...")
    await app.start()
    await start_userbot()
    logger.info("Bot is active!")
    
    try:
        await idle()
    finally:
        await shutdown()

if __name__ == "__main__":
    loop = asyncio.get_event_loop_policy().get_event_loop()
    try:
        loop.run_until_complete(main_loop())
    except KeyboardInterrupt:
        pass
