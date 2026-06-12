#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
import math
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

# ==================== БАЗА ДАННЫХ ====================
db_lock = asyncio.Lock()

def init_db():
    with sqlite3.connect("bot.db", timeout=30) as conn:
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            model TEXT DEFAULT 'auto',
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
        c.execute('''CREATE TABLE IF NOT EXISTS model_scores (
            model_id TEXT PRIMARY KEY,
            score INTEGER DEFAULT 0,
            uses INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            last_used TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS routing_stats (
            intent TEXT,
            model_id TEXT,
            wins INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            last_used TIMESTAMP,
            PRIMARY KEY (intent, model_id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_facts (
            user_id INTEGER,
            key TEXT,
            value TEXT,
            confidence REAL,
            updated_at TIMESTAMP,
            PRIMARY KEY (user_id, key)
        )''')
        conn.commit()

init_db()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ БД ====================
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
            return row[0] if row else "auto"

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
                      (user_id, "auto", datetime.now()))
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
async def db_update_model_score(model_id, delta):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO model_scores (model_id, score, last_used) VALUES (?, ?, ?) ON CONFLICT(model_id) DO UPDATE SET score = score + ?, last_used = ?",
                      (model_id, delta, datetime.now(), delta, datetime.now()))
            c.execute("UPDATE model_scores SET uses = uses + 1 WHERE model_id = ?", (model_id,))
            conn.commit()

@db_retry
async def db_update_routing_stats(intent, model_id, success):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO routing_stats (intent, model_id, wins, attempts, last_used) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT(intent, model_id) DO UPDATE SET "
                      "wins = wins + ?, attempts = attempts + ?, last_used = ?",
                      (intent, model_id, 1 if success else 0, 1, datetime.now(),
                       1 if success else 0, 1, datetime.now()))
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
            c.execute("SELECT model_id, score, uses FROM model_scores ORDER BY score DESC LIMIT 5")
            top = c.fetchall()
            return hist_count, cache_count, top

@db_retry
async def db_add_fact(user_id, key, value, confidence=0.8):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO user_facts (user_id, key, value, confidence, updated_at) VALUES (?, ?, ?, ?, ?)",
                      (user_id, key, value, confidence, datetime.now()))
            conn.commit()

@db_retry
async def db_get_facts(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT key, value FROM user_facts WHERE user_id = ? AND confidence > 0.6", (user_id,))
            rows = c.fetchall()
            return {k: v for k, v in rows}

# ==================== КОНФИГ МОДЕЛЕЙ ====================
MODELS = {
    "deepseek_chat": "deepseek/deepseek-chat:free",
    "deepseek_r1": "deepseek/deepseek-r1:free",
    "llama31": "meta-llama/llama-3.1-8b-instruct:free",
    "qwen_coder": "qwen/qwen-2.5-coder-7b:free",
    "phi3": "microsoft/phi-3-mini-128k-instruct:free",
}

MODEL_NAMES = {
    "deepseek_chat": "🧠 DeepSeek Chat",
    "deepseek_r1": "⚡ DeepSeek R1",
    "llama31": "🦙 Llama 3.1 8B",
    "qwen_coder": "💻 Qwen Coder",
    "phi3": "🧪 Phi-3 Mini",
}

INTENT_MODEL_MATRIX = {
    "code": ["qwen_coder", "deepseek_chat", "llama31"],
    "reasoning": ["deepseek_r1", "llama31"],
    "chat": ["llama31", "phi3"],
    "other": ["llama31", "phi3"]
}

FALLBACK_CHAIN = [
    "openrouter/free",
    "deepseek/deepseek-chat:free",
    "meta-llama/llama-3.1-8b-instruct:free"
]

DEFAULT_MODEL = "auto"
user_semaphores = {}
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2

# ==================== UCB1 РОУТИНГ ====================
async def ucb1_select_model(intent):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT model_id, wins, attempts FROM routing_stats WHERE intent = ?", (intent,))
            rows = c.fetchall()
    if not rows:
        static = INTENT_MODEL_MATRIX.get(intent, ["llama31"])
        model_key = static[0]
        return MODELS.get(model_key, model_key)
    total_attempts = sum(row[2] for row in rows)
    best_score = -float('inf')
    best_model = None
    for model_id, wins, attempts in rows:
        if attempts == 0:
            ucb = float('inf')
        else:
            avg = wins / attempts
            exploration = math.sqrt(2 * math.log(total_attempts + 1) / attempts)
            ucb = avg + exploration
        if ucb > best_score:
            best_score = ucb
            best_model = model_id
    return best_model if best_model else MODELS.get("llama31")

# ==================== ОЦЕНКА ОТВЕТА ====================
async def judge_response(user_query, model_response, is_code_request):
    score = 5
    if len(model_response) > 200:
        score += 1
    if len(model_response) < 50:
        score -= 2
    if is_code_request and "```" in model_response:
        score += 2
    if "код" in user_query.lower() and "```" not in model_response:
        score -= 1
    return max(1, min(10, score))

# ==================== API ОЧЕРЕДЬ И ЗАПРОСЫ ====================
async def worker(worker_id):
    while True:
        future, model, messages, is_code_request = await request_queue.get()
        try:
            result = await ask_ai_raw(model, messages, is_code_request)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_ai_with_queue(model, messages, is_code_request):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, model, messages, is_code_request))
    return await future

async def ask_ai_raw(model, messages, is_code_request):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    temperature = 0.8 if is_code_request else 0.7
    system_prompt = {"role": "system", "content": "Ты краткий, точный ассистент. Не повторяйся."}
    api_messages = [system_prompt] + messages
    if is_code_request:
        api_messages.insert(1, {"role": "system", "content": "Ты — профессиональный разработчик. Пиши ТОЛЬКО рабочий код."})
    payload = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
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
                return f"❌ Ошибка API: {resp.status}\n{error_text[:200]}"
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    return "❌ Слишком много запросов, попробуйте позже."

async def ask_ai(model, messages, is_code_request):
    return await ask_ai_with_queue(model, messages, is_code_request)

async def ask_with_fallback(messages, is_code_request, intent, complexity=0):
    best_model = await ucb1_select_model(intent)
    if best_model:
        try:
            reply = await ask_ai(best_model, messages, is_code_request)
            if reply and not reply.startswith("❌"):
                return reply
        except:
            pass
    for model in FALLBACK_CHAIN:
        try:
            reply = await ask_ai(model, messages, is_code_request)
            if reply and not reply.startswith("❌"):
                return reply
        except:
            continue
    return "❌ Все модели временно недоступны"

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def classify_intent(text):
    if re.search(r'(напиши|сделай|дай|код|скрипт|программу)', text, re.I):
        return "code"
    if re.search(r'(объясни|почему|как работает|докажи|рассуди)', text, re.I):
        return "reasoning"
    return "chat"

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
        [InlineKeyboardButton(text="🧠 Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="🗑 Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])

def model_keyboard():
    buttons = []
    row = []
    for key, name in MODEL_NAMES.items():
        btn_text = name.split()[0][:8]
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"model_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ИЗВЛЕЧЕНИЕ ФАКТОВ (без LLM) ====================
async def extract_facts(user_id, user_text, assistant_response):
    text = user_text.lower()
    facts = {}
    if "python" in text:
        facts["fav_lang"] = "Python"
    if "javascript" in text or "js" in text:
        facts["fav_lang"] = "JavaScript"
    if "новичок" in text or "начинающий" in text:
        facts["level"] = "beginner"
    if "профессионал" in text or "опытный" in text:
        facts["level"] = "advanced"
    for k, v in facts.items():
        await db_add_fact(user_id, k, v)

# ==================== СУММАРИЗАЦИЯ КОНТЕКСТА ====================
async def summarize_history(history):
    if len(history) <= 8:
        return history
    old = history[:-6]
    recent = history[-6:]
    text_for_summary = "\n".join([f"{m['role']}: {m['content'][:200]}" for m in old])
    prompt = f"Сделай краткое резюме этого диалога (1-2 предложения):\n{text_for_summary}"
    try:
        summary = await ask_ai("meta-llama/llama-3.1-8b-instruct:free",
                               [{"role": "user", "content": prompt}],
                               is_code_request=False)
        summary = summary[:300]
        return [{"role": "system", "content": f"Краткий итог: {summary}"}] + recent
    except:
        return history

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    await message.answer(
        f"🤖 *AI Router Lite*\n\n"
        f"🧠 *UCB1 роутинг* – самообучающийся выбор модели\n"
        f"📚 *Память* – история, факты, кэш\n"
        f"👇 Просто напиши сообщение",
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
            await callback.message.edit_text(
                f"✅ *Режим:* `{MODEL_NAMES[model_key]}`",
                reply_markup=main_keyboard(),
                parse_mode="Markdown"
            )
        await callback.answer()
        return

    if data == "show_models":
        text = "🧠 *Доступные модели:*\n\n" + "\n".join([f"• {n}" for n in MODEL_NAMES.values()])
        await callback.message.edit_text(text, reply_markup=model_keyboard())
        await callback.answer()
        return

    if data == "back_to_main":
        await callback.message.edit_text("🤖 *Главное меню*", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "clear":
        await db_clear_history(user_id)
        await callback.message.edit_text("🗑 *История очищена*", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        hist, cache, top = await db_get_stats(user_id)
        stats_text = f"📊 *Статистика*\n\n• Сообщений: {hist}\n• Кэш: {cache}\n\n🏆 *Лучшие модели:*\n"
        for m in top:
            model_name = next((name for key, name in MODEL_NAMES.items() if MODELS.get(key) == m[0]), m[0])
            stats_text += f"• {model_name}: {m[1]} очков\n"
        await callback.message.edit_text(stats_text[:4000], reply_markup=main_keyboard(), parse_mode="Markdown")
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
        intent = await classify_intent(user_text)
        is_code = (intent == "code")
        lang = detect_language(user_text) if is_code else None
        complexity = len(user_text) + user_text.count('?') * 2

        # История
        history = await db_get_history(user_id, limit=20)
        facts = await db_get_facts(user_id)
        fact_context = "\n".join([f"Факт: {k} = {v}" for k, v in facts.items()]) if facts else ""

        # Суммаризация
        history = await summarize_history(history)

        await db_add_history(user_id, "user", user_text)
        history.append({"role": "user", "content": user_text})
        if fact_context:
            history.append({"role": "system", "content": fact_context})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        user_model_setting = await db_get_user_model(user_id)
        if user_model_setting == "auto":
            model_to_use = await ucb1_select_model(intent)
            model_display = "🤖 Автовыбор"
        else:
            model_to_use = MODELS.get(user_model_setting, "openrouter/free")
            model_display = MODEL_NAMES.get(user_model_setting, "AI")

        # Кэш
        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        cached = await db_cache_get(cache_key)
        if cached:
            await message.answer(cached, reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"⏳ *{model_display}* анализирует...", parse_mode="Markdown")
        reply = await ask_with_fallback(messages_api, is_code, intent, complexity)

        judge = await judge_response(user_text, reply, is_code)
        delta = judge - 5
        if delta > 5: delta = 5
        if delta < -5: delta = -5
        await db_update_model_score(model_to_use, delta)
        await db_update_routing_stats(intent, model_to_use, success=(judge >= 7))

        await extract_facts(user_id, user_text, reply)

        if is_code and not reply.startswith('```'):
            reply = format_code_response(reply, lang)

        await db_cache_set(cache_key, reply)
        await db_add_history(user_id, "assistant", reply[:2000])

        try:
            await status_msg.delete()
        except:
            pass
        await send_long_answer(message, reply, main_keyboard())

# ==================== HEALTH CHECK (ПРОСТЕЙШИЙ) ====================
async def handle_health(reader, writer):
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()

async def start_web_server():
    server = await asyncio.start_server(handle_health, "0.0.0.0", PORT)
    print(f"✅ Health check server listening on 0.0.0.0:{PORT}")
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

    # 1. Запускаем health check
    await start_web_server()

    # 2. Даём Render время на детект порта
    await asyncio.sleep(5)

    # 3. Инициализируем очередь и сессию
    session = aiohttp.ClientSession()
    request_queue = asyncio.Queue(maxsize=50)
    for i in range(queue_workers):
        asyncio.create_task(worker(i))

    print("🚀 Bot started, health server reachable at /health")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
