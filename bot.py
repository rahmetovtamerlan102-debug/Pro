#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
import random
import math
import ast
import operator
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
import hnswlib
import numpy as np
from sentence_transformers import SentenceTransformer

# ==================== HTTP health check сервер ====================
from aiohttp import web

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("BOT_TOKEN и OPENROUTER_API_KEY обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
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
        fact TEXT,
        confidence REAL,
        last_updated TIMESTAMP,
        PRIMARY KEY (user_id, fact)
    )''')
    conn.commit()
    conn.close()

init_db()

# ==================== ЛЁГКИЙ ЭМБЕДДЕР ====================
print("Загрузка лёгкой модели эмбеддингов (paraphrase-MiniLM-L3-v2)...")
embedder = SentenceTransformer('paraphrase-MiniLM-L3-v2')
dim = 384

index = None
metadata = []
hnsw_index_file = "hnsw_index.bin"
metadata_file = "metadata.json"

def load_hnsw():
    global index, metadata
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    if os.path.exists(hnsw_index_file) and len(metadata) > 0:
        index = hnswlib.Index(space='cosine', dim=dim)
        index.load_index(hnsw_index_file)
        print(f"HNSWLIB загружен: {index.get_current_count()} векторов")
    else:
        index = hnswlib.Index(space='cosine', dim=dim)
        index.init_index(max_elements=100000, ef_construction=200, M=16)
        print("HNSWLIB создан заново")

def save_hnsw():
    if index is not None and index.get_current_count() > 0:
        index.save_index(hnsw_index_file)
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

load_hnsw()

def add_embedding(user_id, text, role):
    if not text or len(text) < 5:
        return
    vec = embedder.encode(text).astype(np.float32)
    labels = np.array([len(metadata)])
    index.add_items(vec.reshape(1, -1), labels)
    metadata.append({
        "user_id": user_id,
        "text": text[:1000],
        "role": role,
        "timestamp": datetime.now().isoformat()
    })
    if len(metadata) % 10 == 0:
        save_hnsw()

def find_similar_context(user_id, query, top_k=3):
    if index is None or index.get_current_count() == 0:
        return []
    qvec = embedder.encode(query).astype(np.float32)
    labels, distances = index.knn_query(qvec.reshape(1, -1), k=min(top_k, index.get_current_count()))
    results = []
    for label, dist in zip(labels[0], distances[0]):
        idx = int(label)
        if idx >= 0 and idx < len(metadata) and metadata[idx]["user_id"] == user_id:
            similarity = 1 - dist
            if similarity > 0.6:
                results.append(metadata[idx]["text"])
    return results

# ==================== КОНФИГ МОДЕЛЕЙ ====================
MODELS = {
    "deepseek_chat": "deepseek/deepseek-chat:free",
    "deepseek_r1": "deepseek/deepseek-r1:free",
    "llama31": "meta-llama/llama-3.1-8b-instruct:free",
    "llama33": "meta-llama/llama-3.3-70b-instruct:free",
    "qwen_coder": "qwen/qwen-2.5-coder-7b:free",
    "phi3": "microsoft/phi-3-mini-128k-instruct:free",
    "gemma2": "google/gemma-2-9b-it:free"
}

MODEL_NAMES = {
    "deepseek_chat": "🧠 DeepSeek Chat",
    "deepseek_r1": "⚡ DeepSeek R1",
    "llama31": "🦙 Llama 3.1 8B",
    "llama33": "🦙 Llama 3.3 70B",
    "qwen_coder": "💻 Qwen Coder",
    "phi3": "🧪 Phi-3 Mini",
    "gemma2": "🧬 Gemma 2 9B"
}

INTENT_MODEL_MATRIX = {
    "code": ["qwen_coder", "deepseek_chat", "llama31"],
    "reasoning": ["deepseek_r1", "llama33", "gemma2"],
    "chat": ["llama31", "gemma2", "phi3"],
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
request_queue = asyncio.Queue()
queue_workers = 2   # уменьшил количество воркеров для экономии памяти

# ==================== РАБОТА С БД ====================
def get_user_model(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT model FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_MODEL

def update_routing_stats(intent, model_id, success):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO routing_stats (intent, model_id, wins, attempts, last_used) VALUES (?, ?, ?, ?, ?) "
              "ON CONFLICT(intent, model_id) DO UPDATE SET "
              "wins = wins + ?, attempts = attempts + ?, last_used = ?",
              (intent, model_id, 1 if success else 0, 1, datetime.now(),
               1 if success else 0, 1, datetime.now()))
    conn.commit()
    conn.close()

def ucb1_select_model(intent):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT model_id, wins, attempts FROM routing_stats WHERE intent = ?", (intent,))
    rows = c.fetchall()
    conn.close()
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

def update_model_score(model_id, delta):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO model_scores (model_id, score, last_used) VALUES (?, ?, ?) ON CONFLICT(model_id) DO UPDATE SET score = score + ?, last_used = ?",
              (model_id, delta, datetime.now(), delta, datetime.now()))
    conn.commit()
    conn.close()

async def judge_response(user_query, model_response, is_code_request):
    # Упрощённая оценка без LLM-судьи (чтобы экономить память)
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

# ==================== АСИНХРОННАЯ ОЧЕРЕДЬ ====================
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
    if is_code_request:
        system_msg = {"role": "system", "content": "Ты — профессиональный разработчик. Пиши ТОЛЬКО рабочий код. Полные импорты, обработка ошибок, комментарии."}
        api_messages = [system_msg] + messages
    else:
        api_messages = messages
    payload = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": 2000
    }
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status == 429:
            raise Exception("rate_limit")
        if resp.status != 200:
            error_text = await resp.text()
            return f"❌ Ошибка API: {resp.status}\n{error_text[:200]}"
        data = await resp.json()
        return data["choices"][0]["message"]["content"]

async def ask_ai(model, messages, is_code_request):
    return await ask_ai_with_queue(model, messages, is_code_request)

async def ask_with_fallback(messages, is_code_request, intent, complexity=0):
    best_model = ucb1_select_model(intent)
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
    if 'sql' in text.lower():
        return 'sql'
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

async def stream_edit_message(original_message, full_text, delay=0.03):
    msg = await original_message.answer("...")
    for i in range(0, len(full_text), 20):
        chunk = full_text[:i+20]
        try:
            await msg.edit_text(chunk)
        except:
            pass
        await asyncio.sleep(delay)
    return msg

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

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, model, created_at) VALUES (?, ?, ?)",
              (user_id, DEFAULT_MODEL, datetime.now()))
    conn.commit()
    conn.close()
    await message.answer(
        f"🤖 *AI Router Lite*\n\n"
        f"🧠 *Лёгкая память* (без переранжирования)\n"
        f"⚡ *UCB1 routing* – самообучение\n"
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
            conn = sqlite3.connect("bot.db")
            c = conn.cursor()
            c.execute("UPDATE users SET model = ? WHERE user_id = ?", (model_key, user_id))
            conn.commit()
            conn.close()
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
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await callback.message.edit_text("🗑 *История очищена*", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
        hist_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM cache")
        cache_count = c.fetchone()[0]
        c.execute("SELECT model_id, score, uses FROM model_scores ORDER BY score DESC LIMIT 5")
        top_models = c.fetchall()
        conn.close()
        stats_text = f"📊 *Статистика*\n\n• Сообщений: {hist_count}\n• Кэш: {cache_count}\n\n🏆 *Лучшие модели:*\n"
        for m in top_models:
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
        add_embedding(user_id, user_text, "user")
        similar = find_similar_context(user_id, user_text)
        extra = "\n\nПохожие случаи:\n" + "\n".join(similar) if similar else ""

        intent = await classify_intent(user_text)
        is_code = (intent == "code")
        lang = detect_language(user_text) if is_code else None
        complexity = len(user_text) + user_text.count('?') * 2

        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 8", (user_id,))
        rows = c.fetchall()
        history = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                  (user_id, "user", user_text, datetime.now()))
        conn.commit()
        history.append({"role": "user", "content": user_text})
        if extra:
            history.append({"role": "system", "content": extra})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        user_model_setting = get_user_model(user_id)
        if user_model_setting == "auto":
            model_to_use = ucb1_select_model(intent)
            model_display = "🤖 Автовыбор"
        else:
            model_to_use = MODELS.get(user_model_setting, "openrouter/free")
            model_display = MODEL_NAMES.get(user_model_setting, "AI")

        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        c_cache = conn.cursor()
        c_cache.execute("SELECT response FROM cache WHERE key = ?", (cache_key,))
        cached = c_cache.fetchone()
        if cached:
            await message.answer(cached[0], reply_markup=main_keyboard())
            conn.close()
            return

        status_msg = await message.answer(f"⏳ *{model_display}* думает...", parse_mode="Markdown")
        reply = await ask_with_fallback(messages_api, is_code, intent, complexity)

        judge = await judge_response(user_text, reply, is_code)
        delta = judge - 5
        if delta > 5: delta = 5
        if delta < -5: delta = -5
        update_model_score(model_to_use, delta)
        update_routing_stats(intent, model_to_use, success=(judge >= 7))

        if is_code and not reply.startswith('```'):
            reply = format_code_response(reply, lang)

        c.execute("INSERT OR REPLACE INTO cache (key, response, created_at) VALUES (?, ?, ?)",
                  (cache_key, reply[:1500], datetime.now()))
        c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                  (user_id, "assistant", reply[:2000], datetime.now()))
        conn.commit()
        add_embedding(user_id, reply, "assistant")
        conn.close()

        try:
            await status_msg.delete()
        except:
            pass
        if len(reply) > 300:
            stream_msg = await stream_edit_message(message, reply)
            await stream_msg.edit_text(reply, reply_markup=main_keyboard())
        else:
            parts = split_long_message(reply)
            for i, part in enumerate(parts):
                if i == len(parts)-1:
                    await message.answer(part, reply_markup=main_keyboard())
                else:
                    await message.answer(part)

# ==================== HEALTH CHECK ====================
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Health check на порту {PORT}")

async def on_shutdown():
    global session
    if session and not session.closed:
        await session.close()
    save_hnsw()

async def main():
    global session
    session = aiohttp.ClientSession()
    await start_web_server()
    for i in range(queue_workers):
        asyncio.create_task(worker(i))
    print("🤖 AI Router Lite запущен")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
