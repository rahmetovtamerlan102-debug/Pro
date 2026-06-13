from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
import logging
from modules.config import config
from modules.database import db
from modules.redis_client import redis_manager
from services.auth_service import AuthService
from services.user_service import UserService
from services.message_router import MessageRouter
from modules.keyboards import kb
from aiohttp import web

logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
auth_service = AuthService()
user_service = UserService()
message_router = MessageRouter()

# ==================== HANDLERS ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    ref_code = args[1] if len(args) > 1 else None
    await db.create_user(user_id, ref_code)
    
    text = f"🤖 LLM Hub Bot v{__version__}\n\nДобро пожаловать!"
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ Access denied")
        return
    await message.answer("👑 Admin Panel", reply_markup=kb.admin())

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user_id = message.from_user.id
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    model = await db.get_user_model(user_id)
    
    text = (
        f"👤 Profile\n\n"
        f"Tier: {config.TIER_NAMES[tier]}\n"
        f"Today: {used}/{limit}\n"
        f"Remaining: {remaining}\n"
        f"Model: {config.MODEL_NAMES[model]}"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.profile())

@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    
    # Rate limit
    key = f"rl:{user_id}"
    count = await redis_manager.incr(key, config.RATE_LIMIT_PERIOD)
    if count > config.RATE_LIMIT_REQUESTS:
        await message.answer("Too fast! Please wait 1 second.")
        return
    
    # Check ban
    banned, reason = await db.is_banned(user_id)
    if banned:
        await message.answer(f"Banned: {reason}")
        return
    
    # Check limits
    allowed, remaining = await db.increment_usage(user_id)
    if not allowed:
        tier = await db.get_user_tier(user_id)
        await message.answer(f"Limit for {config.TIER_NAMES[tier]} exhausted")
        return
    
    # Route to message router
    await message_router.process(user_id, message.text)

# ==================== WEBHOOK ====================
async def health_check(request):
    return web.Response(text="OK")

async def start_api_gateway():
    app = web.Application()
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    
    logger.info(f"API Gateway started on port {config.PORT}")
    await dp.start_polling(bot)
