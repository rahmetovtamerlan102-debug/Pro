import asyncio
import os
import re
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("BOT_TOKEN и OPENROUTER_API_KEY обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== ТОЛЬКО БЕСПЛАТНЫЕ МОДЕЛИ (без авто) ====================
MODELS = {
    "hy3": "tencent/hy3-preview:free",
    "qwen36": "qwen/qwen3.6-plus-preview:free",
    "nemotron_super": "nvidia/nemotron-3-super:free",
    "nemotron_ultra": "nvidia/nemotron-3-ultra:free",
    "gpt_oss": "openai/gpt-oss-120b:free",
    "gemma4": "google/gemma-4-31b-it:free",
    "kimi": "moonshotai/kimi-k2.6:free",
    "grok": "x-ai/grok-build-0.1:free",
    "claude_opus": "anthropic/claude-4.8-opus:free"
}

MODEL_NAMES = {
    "hy3": "🧠 Hy3 Preview (Tencent)",
    "qwen36": "📚 Qwen 3.6 Plus (1M ctx)",
    "nemotron_super": "⚡ Nemotron 3 Super (NVIDIA)",
    "nemotron_ultra": "🔥 Nemotron 3 Ultra (NVIDIA)",
    "gpt_oss": "💡 GPT-OSS 120B (OpenAI)",
    "gemma4": "🌟 Gemma 4 31B (Google)",
    "kimi": "🤖 Kimi K2.6 (Moonshot)",
    "grok": "🎯 Grok Build (xAI)",
    "claude_opus": "🧬 Claude Opus 4.8 (Anthropic)"
}

CODE_KEYWORDS = re.compile(
    r'(напиши|сделай|дай|нужен|нужна|требуется|покажи|сгенерируй|создай)\s*(код|скрипт|программу|функцию|класс|бота|приложение|парсер|сканер)',
    re.IGNORECASE
)

DEFAULT_MODEL = "gpt_oss"  # быстрая модель по умолчанию
user_model = {}
user_history = {}

# ==================== КЛАВИАТУРЫ ====================
def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="🗑 Очистить диалог", callback_data="clear")]
    ])

def model_keyboard():
    buttons = []
    row = []
    for key, name in MODEL_NAMES.items():
        # Берём первые 2 слова (до 20 символов)
        btn_text = name.split()[0] + " " + (name.split()[1] if len(name.split()) > 1 else "")
        row.append(InlineKeyboardButton(text=btn_text[:20], callback_data=f"model_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОПРЕДЕЛЕНИЕ ЯЗЫКА КОДА ====================
def detect_language(text):
    text_lower = text.lower()
    if 'python' in text_lower or 'питон' in text_lower:
        return 'python'
    if 'javascript' in text_lower or 'js' in text_lower:
        return 'javascript'
    if 'bash' in text_lower or 'shell' in text_lower:
        return 'bash'
    if 'sql' in text_lower:
        return 'sql'
    if 'html' in text_lower:
        return 'html'
    if 'css' in text_lower:
        return 'css'
    if 'go' in text_lower or 'golang' in text_lower:
        return 'go'
    if 'rust' in text_lower:
        return 'rust'
    if 'c++' in text_lower or 'cpp' in text_lower:
        return 'cpp'
    if 'java' in text_lower:
        return 'java'
    return 'python'

# ==================== ЗАПРОС К OPENROUTER ====================
async def ask_ai(model_id, messages, is_code_request=False):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    temperature = 0.8 if is_code_request else 0.7
    model = MODELS.get(model_id)
    
    if not model:
        return "❌ Модель не найдена. Выберите другую через кнопку."
    
    if is_code_request:
        system_message = {
            "role": "system",
            "content": (
                "Ты — профессиональный разработчик. Пиши ТОЛЬКО рабочий код без лишних объяснений. "
                "Код должен быть готов к продакшену: полные импорты, обработка ошибок, комментарии на русском. "
                "Используй формат с тройными бэктиками и указанием языка. "
                "Если код больше 100 строк, разбивай на логические блоки. "
                "Добавляй пример использования в конце."
            )
        }
        messages.insert(0, system_message)
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 3000
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                if resp.status == 402:
                    return "❌ Ошибка: эта модель требует оплаты. Выберите другую бесплатную модель (все в списке имеют :free)."
                return f"❌ Ошибка API: {resp.status}\n{error_text[:200]}"
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

# ==================== ФОРМАТИРОВАНИЕ ====================
def format_code_response(text, lang):
    if '```' in text:
        return text
    return f"```{lang}\n{text}\n```"

def split_long_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    lines = text.split('\n')
    current_part = ""
    for line in lines:
        if len(current_part) + len(line) + 1 > max_len:
            parts.append(current_part)
            current_part = line + '\n'
        else:
            current_part += line + '\n'
    if current_part:
        parts.append(current_part)
    return parts

# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_model[user_id] = DEFAULT_MODEL
    user_history[user_id] = []
    await message.answer(
        f"🤖 *AI Multi-Model Bot (Только бесплатные)*\n\n"
        f"Доступно *{len(MODELS)}* нейросетей\n"
        f"📌 *Текущая модель:* `{MODEL_NAMES[DEFAULT_MODEL]}`\n\n"
        f"💰 *Стоимость:* 0 рублей\n"
        f"🆓 Все модели официально бесплатные на OpenRouter\n\n"
        f"🔥 *Особенности:*\n"
        f"• Пиши код — дам готовый продакшн-скрипт\n"
        f"• История диалога запоминается\n"
        f"• Меняй модель кнопками\n\n"
        f"Просто напиши сообщение 👇",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

# ==================== КОЛБЭКИ ====================
@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_key = data.replace("model_", "")
        if model_key in MODELS:
            user_model[user_id] = model_key
            await callback.message.edit_text(
                f"✅ *Модель изменена:*\n`{MODEL_NAMES[model_key]}`",
                reply_markup=main_keyboard(),
                parse_mode="Markdown"
            )
        else:
            await callback.answer("❌ Модель не найдена")
        await callback.answer()
        return

    if data == "show_models":
        text = "🧠 *Доступные бесплатные модели:*\n\n"
        for name in MODEL_NAMES.values():
            text += f"• {name}\n"
        await callback.message.edit_text(
            text[:4000],
            reply_markup=model_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "back_to_main":
        current = user_model.get(user_id, DEFAULT_MODEL)
        await callback.message.edit_text(
            f"🤖 *Главное меню*\n📌 Текущая модель: `{MODEL_NAMES[current]}`\n💰 Бесплатно",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "clear":
        user_history[user_id] = []
        await callback.message.edit_text(
            "🗑 *История диалога очищена*",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

# ==================== ОБЫЧНЫЕ СООБЩЕНИЯ ====================
@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    if user_id not in user_model:
        user_model[user_id] = DEFAULT_MODEL
    if user_id not in user_history:
        user_history[user_id] = []

    is_code_request = bool(CODE_KEYWORDS.search(user_text))
    lang = detect_language(user_text) if is_code_request else None

    user_history[user_id].append(("user", user_text))
    if len(user_history[user_id]) > 20:
        user_history[user_id] = user_history[user_id][-20:]

    messages_for_api = [{"role": r, "content": c} for r, c in user_history[user_id]]

    current_model = user_model[user_id]
    thinking = f"⏳ *{MODEL_NAMES[current_model]}* {'пишет код...' if is_code_request else 'думает...'}"
    status_msg = await message.answer(thinking, parse_mode="Markdown")

    try:
        reply = await ask_ai(current_model, messages_for_api, is_code_request)
        
        if is_code_request and not reply.startswith('```'):
            reply = format_code_response(reply, lang)
        
        parts = split_long_message(reply)
        await status_msg.delete()
        
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                await message.answer(part, reply_markup=main_keyboard())
            else:
                await message.answer(part)
        
        user_history[user_id].append(("assistant", reply))
    except Exception as e:
        await status_msg.edit_text(f"❌ *Ошибка:* `{str(e)[:200]}`", reply_markup=main_keyboard(), parse_mode="Markdown")

# ==================== ЗАПУСК ====================
async def main():
    print("🤖 Бот запущен (только бесплатные модели, без автовыбора)")
    print(f"Моделей: {len(MODELS)}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
