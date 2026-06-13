import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from modules.config import config
from modules.database import db
from modules.redis_client import redis_manager
from modules.keyboards import kb
from modules.rate_limiter import rate_limiter
from modules.utils import utils
from services.message_router import message_router
from services.user_service import user_service
from services.llm_router import llm_router
from services.export_service import export_service

logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# ==================== КОМАНДЫ ====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    ref_code = args[1] if len(args) > 1 else None
    
    await db.create_user(user_id, ref_code)
    
    text = (
        f"🤖 **LLM Hub Bot**\n\n"
        f"Добро пожаловать! Я предоставляю доступ к передовым AI моделям.\n\n"
        f"**Доступные модели:**\n"
        f"• Llama 4 Scout\n"
        f"• Llama 3.3 70B\n"
        f"• Llama 3.1 8B\n"
        f"• Qwen 3 32B\n\n"
        f"**Тарифы:**\n"
        f"• Бесплатный: 30 запросов/день\n"
        f"• PRO: 500 запросов/день (3 USDT)\n"
        f"• ULTRA: 10000 запросов/день (10 USDT)\n\n"
        f"Просто напишите сообщение, и я отвечу!"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user_id = message.from_user.id
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    model = await db.get_user_model(user_id)
    history_count = await db.get_history_count(user_id)
    invited, bonus = await db.get_referral_info(user_id)
    
    text = (
        f"👤 **Личный кабинет**\n\n"
        f"💎 Тариф: {config.TIER_NAMES.get(tier, tier).upper()}\n"
        f"📊 Запросов сегодня: {used}/{limit}\n"
        f"✨ Осталось: {remaining}\n"
        f"💬 Всего сообщений: {history_count}\n"
        f"🤖 Модель: {config.MODEL_NAMES.get(model, model)}\n"
        f"👥 Рефералов: {invited} (бонусов: {bonus})"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.profile())

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    history_count = await db.get_history_count(user_id)
    
    text = (
        f"📊 **Ваша статистика**\n\n"
        f"💎 Тариф: {config.TIER_NAMES.get(tier, tier).upper()}\n"
        f"📈 Запросов сегодня: {used}/{limit}\n"
        f"✨ Осталось: {remaining}\n"
        f"💬 Всего сообщений: {history_count}\n"
        f"🔄 Сброс: 00:00 UTC"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    user_id = message.from_user.id
    await db.clear_history(user_id)
    await message.answer("✅ История диалога очищена", reply_markup=kb.main())

@dp.message(Command("export"))
async def cmd_export(message: Message):
    await message.answer("📤 **Выберите формат экспорта:**", parse_mode="Markdown", reply_markup=kb.export())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        f"❓ **Помощь**\n\n"
        f"**Команды:**\n"
        f"/start - Начать\n"
        f"/profile - Личный кабинет\n"
        f"/stats - Статистика\n"
        f"/clear - Очистить историю\n"
        f"/export - Экспорт диалога\n"
        f"/help - Помощь"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("models"))
async def cmd_models(message: Message):
    await message.answer("🤖 **Выберите модель:**", parse_mode="Markdown", reply_markup=kb.models())

# ==================== CALLBACKS ====================

@dp.callback_query()
async def handle_callback(call: CallbackQuery):
    user_id = call.from_user.id
    data = call.data
    
    if data.startswith("model_"):
        model = data.replace("model_", "")
        await db.update_user_model(user_id, model)
        await call.message.edit_text(f"✅ Модель изменена на: **{config.MODEL_NAMES.get(model, model)}**", parse_mode="Markdown", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "show_models":
        await call.message.edit_text("🤖 **Выберите модель:**", parse_mode="Markdown", reply_markup=kb.models())
        await call.answer()
        return
    
    if data == "back_to_main":
        await call.message.edit_text("🤖 **Главное меню**", parse_mode="Markdown", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "back_to_profile":
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        model = await db.get_user_model(user_id)
        history_count = await db.get_history_count(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        text = (
            f"👤 **Личный кабинет**\n\n"
            f"💎 Тариф: {config.TIER_NAMES.get(tier, tier).upper()}\n"
            f"📊 Запросов сегодня: {used}/{limit}\n"
            f"✨ Осталось: {remaining}\n"
            f"💬 Всего сообщений: {history_count}\n"
            f"🤖 Модель: {config.MODEL_NAMES.get(model, model)}\n"
            f"👥 Рефералов: {invited} (бонусов: {bonus})"
        )
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "show_ref":
        code = await db.get_or_create_referral_code(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        text = (
            f"🔗 **Реферальная программа**\n\n"
            f"👥 Приглашено друзей: {invited}\n"
            f"🎁 Бонусов на счету: {bonus}\n"
            f"💰 За каждого друга: +{config.REFERRAL_BONUS} запросов\n\n"
            f"**Ваша ссылка:**\n"
            f"<code>https://t.me/{config.BOT_USERNAME}?start={code}</code>"
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "clear_history":
        await db.clear_history(user_id)
        await call.message.edit_text("✅ История диалога очищена", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "export":
        await call.message.edit_text("📤 **Выберите формат экспорта:**", parse_mode="Markdown", reply_markup=kb.export())
        await call.answer()
        return
    
    if data == "export_txt":
        file = await export_service.export_txt(user_id)
        await call.message.answer_document(file, caption="📄 Ваш диалог")
        await call.message.edit_text("👤 Личный кабинет", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "export_json":
        file = await export_service.export_json(user_id)
        await call.message.answer_document(file, caption="📦 Ваш диалог в JSON")
        await call.message.edit_text("👤 Личный кабинет", reply_markup=kb.profile())
        await call.answer()
        return

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================

@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text or message.caption
    
    if not user_text:
        return
    
    # Rate limit через Redis (если Redis недоступен, пропускаем)
    if redis_manager.enabled:
        key = f"rl:{user_id}"
        count = await redis_manager.incr(key)
        if count == 1:
            await redis_manager.client.expire(key, 1)
        if count > 2:
            await message.answer("⏳ Слишком часто! Подождите 1 секунду.")
            return
    
    # Проверка бана
    banned, ban_reason = await db.is_banned(user_id)
    if banned:
        await message.answer(f"🚫 Вы забанены.\nПричина: {ban_reason}")
        return
    
    # Проверка лимитов
    allowed, remaining = await db.increment_usage(user_id)
    if not allowed:
        tier = await db.get_user_tier(user_id)
        await message.answer(f"❌ Лимит {config.TIER_NAMES.get(tier, tier)} исчерпан на сегодня.")
        return
    
    # Получаем историю
    history = await db.get_history(user_id, 20)
    await db.add_history(user_id, "user", user_text)
    history.append({"role": "user", "content": user_text})
    
    # Получаем модель пользователя
    model = await db.get_user_model(user_id)
    
    # Отправляем статус
    status_msg = await message.answer("🤔 Думаю...")
    
    try:
        # Отправляем запрос в LLM роутер
        result = await llm_router.route(model, history)
        reply = result.get("response", "Ошибка")
        used_model = result.get("model", "error")
        
        # Сохраняем ответ в историю
        await db.add_history(user_id, "assistant", reply, used_model)
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        reply = "❌ Произошла ошибка. Попробуйте позже."
    
    await status_msg.delete()
    await message.answer(reply[:4000], reply_markup=kb.main())
    
    if remaining <= 5:
        await message.answer(f"⚠️ Осталось запросов сегодня: {remaining}")

# ==================== HEALTH CHECK ====================

async def health_check(request):
    return web.Response(text="OK")

# ==================== ЗАПУСК ====================

async def start_api_gateway():
    # Удаляем старый вебхук и очищаем обновления (исправляет конфликт polling)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем веб-сервер для health check
    app = web.Application()
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    
    logger.info(f"API Gateway started on port {config.PORT}")
    
    # Запускаем polling
    await dp.start_polling(bot)
