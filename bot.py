#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("BOT_TOKEN и OPENROUTER_API_KEY обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== БАЗА ДАННЫХ (Упрощённая) ====================
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
            return row[0] if row else "llama31"

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
                      (user_id, "llama31", datetime.now()))
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

# ==================== 20 БЕСПЛАТНЫХ МОДЕЛЕЙ ====================
MODELS = {
    "llama31": "meta-llama/llama-3.1-8b-instruct:free",
    "llama33": "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek_chat": "deepseek/deepseek-chat:free",
    "deepseek_r1": "deepseek/deepseek-r1:free",
    "qwen_coder": "qwen/qwen-2.5-coder-7b:free",
    "qwen72b": "qwen/qwen-2.5-72b-instruct:free",
    "qwen3_coder": "qwen/qwen3-coder:free",
    "qwen3_next": "qwen/qwen3-next-80b-a3b-instruct:free",
    "phi3": "microsoft/phi-3-mini-128k-instruct:free",
    "mistral7b": "mistralai/mistral-7b-instruct:free",
    "gemma2": "google/gemma-2-9b-it:free",
    "gemma4_31b": "google/gemma-4-31b-it:free",
    "gemma4_26b": "google/gemma-4-26b-a4b-it:free",
    "gpt_oss_120b": "openai/gpt-oss-120b:free",
    "gpt_oss_20b": "openai/gpt-oss-20b:free",
    "codellama_34b": "meta-llama/codellama-34b-instruct:free",
    "liquid_12b": "liquid/lfm-12b:free",
    "glm_4_5_air": "z-ai/glm-4.5-air:free",
    "llama_4_8b": "meta-llama/llama-4-8b-instruct:free",
    "owl_alpha": "openrouter/owl-alpha:free",
}

MODEL_NAMES = {
    "llama31": "Llama 3.1 8B",
    "llama33": "Llama 3.3 70B",
    "deepseek_chat": "DeepSeek Chat",
    "deepseek_r1": "DeepSeek R1",
    "qwen_coder": "Qwen Coder 2.5",
    "qwen72b": "Qwen 2.5 72B",
    "qwen3_coder": "Qwen3 Coder",
    "qwen3_next": "Qwen3 Next 80B",
    "phi3": "Phi-3 Mini",
    "mistral7b": "Mistral 7B",
    "gemma2": "Gemma 2 9B",
    "gemma4_31b": "Gemma 4 31B",
    "gemma4_26b": "Gemma 4 26B",
    "gpt_oss_120b": "GPT-OSS 120B",
    "gpt_oss_20b": "GPT-OSS 20B",
    "codellama_34b": "CodeLlama 34B",
    "liquid_12b": "Liquid 12B",
    "glm_4_5_air": "GLM 4.5 Air",
    "llama_4_8b": "Llama 4 8B",
    "owl_alpha": "Owl Alpha",
}

DEFAULT_MODEL = "llama31"   # быстрая модель по умолчанию

user_semaphores = {}
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2

# ==================== API ЗАПРОСЫ ====================
async def worker(worker_id):
    while True:
        future, model, messages = await request_queue.get()
        try:
            result = await ask_ai_raw(model, messages)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_ai_with_queue(model, messages):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, model, messages))
    return await future

async def ask_ai_raw(model, messages):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    system_prompt = {"role": "system", "content": "Ты краткий, точный ассистент. Не повторяйся."}
    api_messages = [system_prompt] + messages
    payload = {
        "model": model,
        "messages": api_messages,
        "temperature": 0.7,
        "max_tokens": 2000
    }
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()
    for attempt in range(3):
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status != 200:
                error_text = await resp.text()
                return f"Ошибка API: {resp.status}\n{error_text[:200]}"
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    return "Слишком много запросов, попробуйте позже."

async def ask_ai(model, messages):
    return await ask_ai_with_queue(model, messages)

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

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="Статистика", callback_data="stats")]
    ])

def model_keyboard():
    buttons = []
    row = []
    for key, name in MODEL_NAMES.items():
        row.append(InlineKeyboardButton(text=name[:15], callback_data=f"model_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    await message.answer(
        f"AI Router | 20 бесплатных моделей\n\n"
        f"Доступно {len(MODELS)} моделей. Текущая: {MODEL_NAMES[DEFAULT_MODEL]}\n"
        f"Напиши сообщение или выбери модель в меню.",
        reply_markup=main_keyboard()
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_key = data.replace("model_", "")
        if model_key in MODELS:
            await db_update_user_model(user_id, model_key)
            await callback.message.edit_text(
                f"Модель изменена: {MODEL_NAMES[model_key]}",
                reply_markup=main_keyboard()
            )
        await callback.answer()
        return

    if data == "show_models":
        text = "Доступные бесплатные модели:\n\n" + "\n".join([f"- {n}" for n in MODEL_NAMES.values()])
        await callback.message.edit_text(text, reply_markup=model_keyboard())
        await callback.answer()
        return

    if data == "back_to_main":
        await callback.message.edit_text("Главное меню", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "clear":
        await db_clear_history(user_id)
        await callback.message.edit_text("История диалога очищена", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        hist, cache = await db_get_stats(user_id)
        stats_text = f"Статистика\n\nСообщений в истории: {hist}\nРазмер кэша: {cache} записей"
        await callback.message.edit_text(stats_text, reply_markup=main_keyboard())
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
        # Проверка на код (очень простая)
        is_code = bool(re.search(r'(код|скрипт|программу|функцию)', user_text, re.I))
        lang = detect_language(user_text) if is_code else None

        # История
        history = await db_get_history(user_id, limit=8)
        await db_add_history(user_id, "user", user_text)
        history.append({"role": "user", "content": user_text})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        user_model_key = await db_get_user_model(user_id)
        model_to_use = MODELS.get(user_model_key, MODELS[DEFAULT_MODEL])
        model_display = MODEL_NAMES.get(user_model_key, MODEL_NAMES[DEFAULT_MODEL])

        # Кэш
        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        cached = await db_cache_get(cache_key)
        if cached:
            await message.answer(cached, reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"{model_display} анализирует...")
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

    print("Bot started, health server reachable at /health")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
