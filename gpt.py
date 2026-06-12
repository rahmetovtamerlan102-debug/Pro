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

# ==================== МОДЕЛИ ====================
MODELS = {
    "chatgpt": "openai/gpt-3.5-turbo",
    "gpt4": "openai/gpt-4o",
    "deepseek": "deepseek/deepseek-chat-v3",
    "deepseek_r1": "deepseek/deepseek-r1-distill-qwen-32b",
    "qwen_coder": "qwen/qwen-2.5-coder-7b-instruct",
    "qwen72b": "qwen/qwen-2.5-72b-instruct",
    "claude3": "anthropic/claude-3-haiku",
    "claude35": "anthropic/claude-3.5-sonnet",
    "gemini_flash": "google/gemini-1.5-flash",
    "gemini_pro": "google/gemini-1.5-pro",
    "llama33": "meta-llama/llama-3.3-70b-instruct",
    "llama32": "meta-llama/llama-3.2-3b-instruct",
    "mistral7b": "mistralai/mistral-7b-instruct",
    "mixtral": "mistralai/mixtral-8x22b-instruct",
    "codellama": "meta-llama/codellama-34b-instruct"
}

MODEL_NAMES = {
    "chatgpt": "🤖 ChatGPT (GPT-3.5)",
    "gpt4": "⚡ ChatGPT (GPT-4o)",
    "deepseek": "🧠 DeepSeek-Coder V3",
    "deepseek_r1": "🧪 DeepSeek-R1",
    "qwen_coder": "💻 Qwen Coder 2.5 7B",
    "qwen72b": "🏆 Qwen 2.5 72B",
    "claude3": "🧬 Claude 3 Haiku",
    "claude35": "🔥 Claude 3.5 Sonnet",
    "gemini_flash": "✨ Gemini 1.5 Flash",
    "gemini_pro": "⭐ Gemini 1.5 Pro",
    "llama33": "🦙 Llama 3.3 70B",
    "llama32": "🦙 Llama 3.2 3B",
    "mistral7b": "🌪️ Mistral 7B",
    "mixtral": "🌀 Mixtral 8x22B",
    "codellama": "📟 CodeLlama 34B"
}

CODE_KEYWORDS = re.compile(
    r'(напиши|сделай|дай|нужен|требуется|покажи|сгенерируй|создай)\s*(код|скрипт|программу|функцию|класс|бота|приложение|парсер|сканер)',
    re.IGNORECASE
)

DEFAULT_MODEL = "deepseek"
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
        "model": MODELS[model_id],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 3500
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
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
        f"🤖 *AI Multi-Model Bot*\n\n"
        f"Доступно *{len(MODELS)}* нейросетей\n"
        f"📌 *Текущая модель:* `{MODEL_NAMES[DEFAULT_MODEL]}`\n\n"
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
        text = "🧠 *Доступные модели:*\n\n"
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
            f"🤖 *Главное меню*\n📌 Текущая модель: `{MODEL_NAMES[current]}`",
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
    print("🤖 Бот запущен")
    print(f"Моделей: {len(MODELS)}")
    print(f"Режим кода: включён")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
