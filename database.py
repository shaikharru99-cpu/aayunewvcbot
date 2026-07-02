import motor.motor_asyncio
from config import MONGO_URI

class Database:
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.users = self.db.users
        self.settings = self.db.settings

    # ---- SETTINGS ----
    async def get_settings(self):
        settings = await self.settings.find_one({"_id": "config"})
        if not settings:
            # Default strings include Premium Emoji IDs
            default = {
                "_id": "config",
                "auto_approve": True,
                "send_acceptance_msg": True,
                "send_leave_msg": True,
                "rejoin_link": None,
                "welcome_sequence": [],
                "accept_msg_text": "<emoji id=\"6120898777046847624\">✅</emoji> <b>Hello {name}!</b>\n\nYour request to join {tag} has been accepted. <emoji id=\"6224161941305169199\">🎉</emoji>",
                "leave_msg_text": "<emoji id=\"6224138623927717923\">😱</emoji> <b>Oh no {name}!</b> We noticed you left {tag}.\n\nIf it was a mistake or you want to return and keep earning, you can quickly rejoin using the button below! <emoji id=\"5470177992950946662\">👇</emoji>",
                "userbot_session": None,
                "bot_sequence_enabled": True,
                "userbot_msg_enabled": False,
                "userbot_msg_text": "Hey {name}! 👋",
                "userbot_seq_enabled": False,
                "userbot_sequence": [],
                "ub_source_channel": None
            }
            await self.settings.insert_one(default)
            return default
        return settings

    async def update_settings(self, key, value):
        await self.settings.update_one(
            {"_id": "config"},
            {"$set": {key: value}},
            upsert=True
        )

    # ---- USERS ----
    async def add_user(self, user_id: int) -> None:
        """Add a new user with verified=False by default."""
        await self.users.update_one(
            {"_id": user_id},
            {"$setOnInsert": {"verified": False}},
            upsert=True
        )

    async def verify_user(self, user_id: int) -> None:
        """Mark a user as verified."""
        await self.users.update_one(
            {"_id": user_id},
            {"$set": {"verified": True}},
            upsert=True
        )

    async def is_verified(self, user_id: int) -> bool:
        """Check if a user is verified."""
        user = await self.users.find_one({"_id": user_id})
        return user.get("verified", False) if user else False

    async def get_all_users(self):
        """Return cursor for all users."""
        return self.users.find({})

    async def get_all_verified_users(self):
        """Return cursor for verified users only."""
        return self.users.find({"verified": True})

    async def total_users(self) -> int:
        """Total number of users in the database."""
        return await self.users.count_documents({})

    async def total_verified_users(self) -> int:
        """Number of verified users."""
        return await self.users.count_documents({"verified": True})

# Initialize database instance
db = Database(MONGO_URI, "JoinBotDB7777")
