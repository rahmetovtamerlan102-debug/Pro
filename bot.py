#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
import base64
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
import aiohttp
from io import BytesIO

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not CF_ACCOUNT_ID or not CF_API_TOKEN:
    raise ValueError("BOT_TOKEN, CF_ACCOUNT_ID и CF_API_TOKEN обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== БАЗА ДАННЫХ ====================
db_lock = asyncio.Lock()

def init_db():
    with sqlite3.connect("bot.db", timeout=30) as conn:
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            model TEXT DEFAULT 'llama31',
            created_at TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            response TEXT,
            created_at TIMESTAMP
        )''')
        conn.commit()

init_db()

# ==================== РАСШИРЕННЫЙ СПИСОК МОДЕЛЕЙ (23 шт) ====================
MODELS = {
    # LLM для чата и рассуждений
    "llama31": "@cf/meta/llama-3.1-8b-instruct",
    "llama33": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "llama4": "@cf/meta/llama-4-scout-17b-16e-instruct",
    "qwen72b": "@cf/qwen/qwen2.5-72b-instruct",
    "qwen32b": "@cf/qwen/qwen2.5-32b-instruct",
    "mistral7b": "@cf/mistral/mistral-7b-instruct-v0.1",
    "gemma2": "@cf/google/gemma-2-9b-it",
    "gemma4": "@cf/google/gemma-4-26b-a4b-it",
    "phi3": "@cf/microsoft/phi-3-mini-4k-instruct",
    "glm47": "@cf/zai-org/glm-4.7-flash",
    "kimi26": "@cf/moonshotai/kimi-k2.6",
    "deepseek_r1": "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    
    # Модели для кода (5 шт)
    "qwen_coder_7b": "@cf/qwen/qwen2.5-coder-7b-instruct",
    "qwen_coder_14b": "@cf/qwen/qwen2.5-coder-14b-instruct",
    "deepseek_coder": "@cf/deepseek-ai/deepseek-coder-6.7b-instruct",
    "codellama_7b": "@cf/meta/codellama-7b-instruct",
    "starcoder": "@cf/huggingface/starcoder2-7b",
    
    # Генерация изображений
    "flux_schnell": "@cf/black-forest-labs/flux-1-schnell",
    "dreamshaper": "@cf/lykon/dreamshaper-8-lcm",
    "lucid_origin": "@cf/leonardo/lucid-origin",
    "sdxl": "@cf/stabilityai/stable-diffusion-xl-base-1.0",
    
    # OpenAI открытые модели
    "gpt_oss_120b": "@cf/openai/gpt-oss-120b",
    "gpt_oss_20b": "@cf/openai/gpt-oss-20b",
}

MODEL_NAMES = {
    "llama31": "Llama 3.1 8B",
    "llama33": "Llama 3.3 70B",
    "llama4": "Llama 4 Scout 17B",
    "qwen72b": "Qwen 2.5 72B",
    "qwen32b": "Qwen 2.5 32B",
    "mistral7b": "Mistral 7B",
    "gemma2": "Gemma 2 9B",
    "gemma4": "Gemma 4 26B",
    "phi3": "Phi-3 Mini",
    "glm47": "GLM 4.7 Flash",
    "kimi26": "Kimi K2.6",
    "deepseek_r1": "DeepSeek R1",
    
    "qwen_coder_7b": "Qwen Coder 7B",
    "qwen_coder_14b": "Qwen Coder 14B",
    "deepseek_coder": "DeepSeek Coder 6.7B",
    "codellama_7b": "CodeLlama 7B",
    "starcoder": "StarCoder 7B",
    
    "flux_schnell": "Flux Schnell",
    "dreamshaper": "Dreamshaper 8",
    "lucid_origin": "Lucid Origin",
    "sdxl": "Stable Diffusion XL",
    
    "gpt_oss_120b": "GPT-OSS 120B",
    "gpt_oss_20b": "GPT-OSS 20B",
}

# Модели, которые генерируют изображения (а не текст)
IMAGE_MODELS = {
    "flux_schnell", "dreamshaper", "lucid_origin", "sdxl"
}

DEFAULT_MODEL = "llama31"

user_semaphores = {}
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2

# ==================== РАБОТА С БД ====================
def db_retry(func):
    async def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return await func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
                else:
                    raise
    return wrapper

@db_retry
async def db_get_user_model(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT model FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            return row[0] if row else DEFAULT_MODEL

@db_retry
async def db_update_user_model(user_id, model_key):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET model = ? WHERE user_id = ?", (model_key, user_id))
            conn.commit()

@db_retry
async def db_add_user(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, model, created_at) VALUES (?, ?, ?)",
                      (user_id, DEFAULT_MODEL, datetime.now()))
            conn.commit()

@db_retry
async def db_clear_history(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
            conn.commit()

@db_retry
async def db_add_history(user_id, role, content):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                      (user_id, role, content, datetime.now()))
            conn.commit()

@db_retry
async def db_get_history(user_id, limit=8):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit))
            rows = c.fetchall()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

@db_retry
async def db_cache_get(key):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT response FROM cache WHERE key = ?", (key,))
            row = c.fetchone()
            return row[0] if row else None

@db_retry
async def db_cache_set(key, response):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO cache (key, response, created_at) VALUES (?, ?, ?)",
                      (key, response[:1500], datetime.now()))
            conn.commit()

@db_retry
async def db_get_stats(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
            hist_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM cache")
            cache_count = c.fetchone()[0]
            return hist_count, cache_count

# ==================== API ЗАПРОС К CLOUDFLARE ====================
async def worker(worker_id):
    while True:
        future, model, messages, is_image = await request_queue.get()
        try:
            if is_image:
                result = await ask_cf_image_raw(model, messages)
            else:
                result = await ask_cf_ai_raw(model, messages)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_cf_with_queue(model, messages, is_image=False):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, model, messages, is_image))
    return await future

async def ask_cf_ai_raw(model, messages):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{model}"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    # Преобразуем историю в промпт (без system)
    prompt = ""
    for msg in messages[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            prompt += f"User: {content}\n"
        elif role == "assistant":
            prompt += f"Assistant: {content}\n"
    if not prompt:
        prompt = messages[-1].get("content", "")
    
    payload = {
        "prompt": prompt,
        "max_tokens": 2000,
        "temperature": 0.7
    }
    
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()
    
    for attempt in range(3):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status != 200:
                    error_text = await resp.text()
                    return f"Ошибка API: {resp.status}\n{error_text[:200]}"
                data = await resp.json()
                result = data.get("result", {}).get("response", "")
                if not result:
                    return "Ошибка: модель вернула пустой ответ"
                return result
        except asyncio.TimeoutError:
            await asyncio.sleep(2 ** attempt)
            continue
        except Exception:
            await asyncio.sleep(2 ** attempt)
            continue
    return "Слишком много запросов, попробуйте позже."

async def ask_cf_image_raw(model, messages):
    """Генерация изображения (возвращает фото или ссылку)"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{model}"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    # Берём последний промпт пользователя
    prompt = messages[-1].get("content", "") if messages else ""
    if not prompt:
        return "Напишите, что сгенерировать (например: /generate кот в космосе)"
    
    payload = {
        "prompt": prompt,
        "height": 512,
        "width": 512,
        "num_steps": 20
    }
    
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()
    
    for attempt in range(2):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return f"Ошибка генерации: {resp.status}\n{error_text[:200]}"
                data = await resp.json()
                result = data.get("result", {})
                # Формат ответа: изображение в base64
                if "image" in result:
                    return result["image"]  # base64 строка
                if "images" in result and result["images"]:
                    return result["images"][0]  # base64
                return "Ошибка: модель не вернула изображение"
        except asyncio.TimeoutError:
            await asyncio.sleep(2 ** attempt)
            continue
        except Exception:
            await asyncio.sleep(2 ** attempt)
            continue
    return "Не удалось сгенерировать изображение, попробуйте позже."

async def ask_ai(model, messages):
    return await ask_cf_with_queue(model, messages, is_image=False)

async def ask_image(model, messages):
    return await ask_cf_with_queue(model, messages, is_image=True)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def detect_language(text):
    if 'python' in text.lower() or 'питон' in text.lower():
        return 'python'
    if 'javascript' in text.lower() or 'js' in text.lower():
        return 'javascript'
    return 'python'

def format_code_response(text, lang):
    text = text.strip()
    if text.startswith('```'):
        return text
    return f"```{lang}\n{text}\n```"

def split_long_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    lines = text.split('\n')
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            parts.append(current)
            current = line + '\n'
        else:
            current += line + '\n'
    if current:
        parts.append(current)
    return parts

async def send_long_answer(message, text, keyboard):
    if len(text) > 800:
        msg = await message.answer("...")
        for i in range(0, len(text), 20):
            chunk = text[:i+20]
            try:
                await msg.edit_text(chunk, reply_markup=keyboard if i+20 >= len(text) else None)
            except:
                pass
            await asyncio.sleep(0.03)
        return msg
    else:
        return await message.answer(text, reply_markup=keyboard)

async def send_image(message, base64_data):
    """Отправляет изображение из base64"""
    try:
        # Убираем префикс, если есть
        if ',' in base64_data:
            base64_data = base64_data.split(',')[-1]
        image_bytes = base64.b64decode(base64_data)
        photo = BytesIO(image_bytes)
        photo.name = "image.png"
        await message.answer_photo(photo=photo)
    except Exception as e:
        await message.answer(f"Ошибка при отправке изображения: {str(e)[:100]}")

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="🗑 Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])

def model_keyboard():
    buttons = []
    row = []
    # Группируем модели по типам
    text_models = ["llama31", "llama33", "qwen72b", "mistral7b", "gemma2", "phi3", "kimi26", "deepseek_r1"]
    code_models = ["qwen_coder_7b", "qwen_coder_14b", "deepseek_coder", "codellama_7b", "starcoder"]
    image_models = ["flux_schnell", "dreamshaper", "lucid_origin", "sdxl"]
    gpt_models = ["gpt_oss_120b", "gpt_oss_20b"]
    
    for key in text_models + code_models + gpt_models:
        if key in MODEL_NAMES:
            row.append(InlineKeyboardButton(text=MODEL_NAMES[key][:12], callback_data=f"model_{key}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)
    
    # Отдельно модели для изображений (с эмодзи)
    image_row = []
    for key in image_models:
        if key in MODEL_NAMES:
            image_row.append(InlineKeyboardButton(text=f"🖼 {MODEL_NAMES[key][:10]}", callback_data=f"model_{key}"))
            if len(image_row) == 2:
                buttons.append(image_row)
                image_row = []
    if image_row:
        buttons.append(image_row)
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    await message.answer(
        f"🤖 *Cloudflare Workers AI | 23 бесплатные модели*\n\n"
        f"📌 *Доступно:*\n"
        f"• 12 LLM для чата и рассуждений\n"
        f"• 5 моделей для кода\n"
        f"• 4 модели для генерации изображений\n"
        f"• 2 модели GPT-OSS от OpenAI\n\n"
        f"⚡ *Лимит:* 10 000 нейронов/день\n"
        f"🎨 *Для генерации изображений* просто выберите модель и напишите запрос\n\n"
        f"👇 Выберите модель в меню",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_key = data.replace("model_", "")
        if model_key in MODELS:
            await db_update_user_model(user_id, model_key)
            model_type = "изображений" if model_key in IMAGE_MODELS else "текста"
            await callback.message.edit_text(
                f"✅ Модель изменена: *{MODEL_NAMES[model_key]}* ({model_type})",
                reply_markup=main_keyboard(),
                parse_mode="Markdown"
            )
        await callback.answer()
        return

    if data == "show_models":
        await callback.message.edit_text(
            "🧠 *Выберите модель:*\n\n"
            "📝 *Текстовые модели:* Llama, Qwen, Mistral, Gemma, Phi-3, GLM, Kimi, DeepSeek\n"
            "💻 *Кодовые модели:* Qwen Coder, DeepSeek Coder, CodeLlama, StarCoder\n"
            "🖼 *Генерация изображений:* Flux, Dreamshaper, Lucid, SDXL\n"
            "🤖 *OpenAI:* GPT-OSS 120B, GPT-OSS 20B",
            reply_markup=model_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "back_to_main":
        current = await db_get_user_model(user_id)
        current_name = MODEL_NAMES.get(current, "Llama 3.1 8B")
        await callback.message.edit_text(
            f"🤖 *Главное меню*\n📌 Текущая модель: {current_name}",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "clear":
        await db_clear_history(user_id)
        await callback.message.edit_text("🗑 История диалога очищена.", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        hist, cache = await db_get_stats(user_id)
        await callback.message.edit_text(
            f"📊 *Статистика*\n\nСообщений в истории: {hist}\nРазмер кэша: {cache} записей",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    if user_id not in user_semaphores:
        user_semaphores[user_id] = asyncio.Semaphore(2)
    async with user_semaphores[user_id]:
        # Определяем, код ли это
        is_code = bool(re.search(r'(код|скрипт|программу|функцию|напиши)', user_text, re.I))
        lang = detect_language(user_text) if is_code else None

        # Получаем текущую модель пользователя
        user_model_key = await db_get_user_model(user_id)
        model_to_use = MODELS.get(user_model_key, MODELS[DEFAULT_MODEL])
        model_display = MODEL_NAMES.get(user_model_key, MODEL_NAMES[DEFAULT_MODEL])
        is_image_model = user_model_key in IMAGE_MODELS

        # Для изображений не нужна история, просто генерируем
        if is_image_model:
            status_msg = await message.answer(f"🎨 *{model_display}* генерирует изображение...", parse_mode="Markdown")
            result = await ask_image(model_to_use, [{"role": "user", "content": user_text}])
            await status_msg.delete()
            if result.startswith("iVBOR") or (len(result) > 100 and "base64" in result.lower()):
                await send_image(message, result)
            else:
                await message.answer(result, reply_markup=main_keyboard())
            return

        # Текстовые модели: сохраняем историю
        history = await db_get_history(user_id, limit=8)
        await db_add_history(user_id, "user", user_text)
        history.append({"role": "user", "content": user_text})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        # Кэш
        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        cached = await db_cache_get(cache_key)
        if cached:
            await message.answer(cached, reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"⏳ *{model_display}* анализирует...", parse_mode="Markdown")
        reply = await ask_ai(model_to_use, messages_api)

        if is_code and not reply.startswith('```'):
            reply = format_code_response(reply, lang)

        await db_cache_set(cache_key, reply)
        await db_add_history(user_id, "assistant", reply[:2000])

        try:
            await status_msg.delete()
        except:
            pass
        await send_long_answer(message, reply, main_keyboard())

# ==================== HEALTH CHECK ====================
async def handle_health(reader, writer):
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()

async def start_web_server():
    server = await asyncio.start_server(handle_health, "0.0.0.0", PORT)
    print(f"Health check server listening on 0.0.0.0:{PORT}")
    asyncio.create_task(server.serve_forever())

async def on_shutdown():
    global session
    if session and not session.closed:
        await session.close()
        session = None

async def main():
    global session, request_queue
    print("=== MAIN START ===")
    print(f"PORT = {PORT}")

    await start_web_server()
    await asyncio.sleep(5)

    session = aiohttp.ClientSession()
    request_queue = asyncio.Queue(maxsize=50)
    for i in range(queue_workers):
        asyncio.create_task(worker(i))

    print("🚀 Cloudflare Workers AI Bot started | 23 models (text + code + image)")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
