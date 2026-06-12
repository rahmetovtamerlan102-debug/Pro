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
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("BOT_TOKEN и OPENROUTER_API_KEY обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== 1. БАЗА ДАННЫХ ====================
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
        timestamp TIMESTAMP,
        summary TEXT,
        keywords TEXT
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
    c.execute('''CREATE TABLE IF NOT EXISTS user_profile (
        user_id INTEGER PRIMARY KEY,
        preferred_model TEXT,
        coding_level TEXT,
        language TEXT,
        avg_response_length INTEGER,
        detail_level TEXT DEFAULT 'normal'
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

# ==================== 2. ЭМБЕДДИНГИ И FAISS ====================
print("Загрузка модели эмбеддингов...")
embedder = SentenceTransformer('all-MiniLM-L6-v2')
dim = 384
index = faiss.IndexFlatIP(dim)          # косинусное сходство после нормализации
metadata = []                            # список словарей: {user_id, text, role, timestamp}
faiss_index_file = "faiss_index.bin"
metadata_file = "metadata.json"

def load_faiss():
    global index, metadata
    if os.path.exists(faiss_index_file):
        index = faiss.read_index(faiss_index_file)
    if os.path.exists(metadata_file):
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    print(f"FAISS загружен: {index.ntotal} векторов, {len(metadata)} записей")

def save_faiss():
    faiss.write_index(index, faiss_index_file)
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

load_faiss()

def add_embedding(user_id, text, role):
    if not text or len(text) < 5:
        return
    vec = embedder.encode(text).astype(np.float32)
    faiss.normalize_L2(vec.reshape(1, -1))
    index.add(vec.reshape(1, -1))
    metadata.append({
        "user_id": user_id,
        "text": text[:1000],
        "role": role,
        "timestamp": datetime.now().isoformat()
    })
    if len(metadata) % 10 == 0:
        save_faiss()

def find_similar_context(user_id, query, top_k=10):
    if index.ntotal == 0:
        return []
    qvec = embedder.encode(query).astype(np.float32)
    faiss.normalize_L2(qvec.reshape(1, -1))
    scores, idxs = index.search(qvec.reshape(1, -1), min(top_k, index.ntotal))
    results = []
    for score, idx in zip(scores[0], idxs[0]):
        if idx >= 0 and idx < len(metadata) and metadata[idx]["user_id"] == user_id and score > 0.5:
            results.append((score, metadata[idx]["text"]))
    return results

# ==================== 3. CROSS-ENCODER RERANKER ====================
print("Загрузка cross-encoder для переранжирования...")
cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')

def find_similar_context_reranked(user_id, query, top_k=2):
    candidates = find_similar_context(user_id, query, top_k=10)
    if not candidates:
        return []
    texts = [text for score, text in candidates]
    pairs = [(query, text) for text in texts]
    scores = cross_encoder.predict(pairs)
    scored = sorted(zip(texts, scores), key=lambda x: x[1], reverse=True)
    return [text for text, score in scored[:top_k] if score > 0.5]

# ==================== 4. КОНФИГ И МОДЕЛИ ====================
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
    "math": ["phi3", "deepseek_r1", "llama31"],
    "translate": ["llama31", "gemma2"],
    "other": ["llama31", "phi3"]
}

FALLBACK_CHAIN = [
    "openrouter/free",
    "deepseek/deepseek-chat:free",
    "qwen/qwen-2.5-coder-7b:free",
    "meta-llama/llama-3.1-8b-instruct:free"
]

DEFAULT_MODEL = "auto"
user_semaphores = {}
session = None
request_queue = asyncio.Queue()
queue_workers = 3

# ==================== 5. РАБОТА С БД (ВСПОМОГАТЕЛЬНЫЕ) ====================
def get_user_model(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT model FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_MODEL

def update_user_profile(user_id, response_len, detail_pref=None):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO user_profile (user_id, avg_response_length) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET avg_response_length = (avg_response_length + ?)/2",
              (user_id, response_len, response_len))
    if detail_pref:
        c.execute("UPDATE user_profile SET detail_level = ? WHERE user_id = ?", (detail_pref, user_id))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT detail_level, coding_level FROM user_profile WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return {"detail_level": row[0] if row else "normal", "coding_level": row[1] if row else "beginner"}

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

def ucb1_select_model(intent, total_rounds_override=None):
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
    c.execute("UPDATE model_scores SET uses = uses + 1 WHERE model_id = ?", (model_id,))
    conn.commit()
    conn.close()

def get_lowest_score_model():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT model_id FROM model_scores ORDER BY score ASC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def add_fact(user_id, fact, confidence=0.9):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_facts (user_id, fact, confidence, last_updated) VALUES (?, ?, ?, ?)",
              (user_id, fact, confidence, datetime.now()))
    conn.commit()
    conn.close()

def get_user_facts_context(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT fact FROM user_facts WHERE user_id = ? AND confidence > 0.6 ORDER BY last_updated DESC LIMIT 15", (user_id,))
    rows = c.fetchall()
    conn.close()
    if rows:
        facts = [row[0] for row in rows]
        return "\nИзвестные факты о пользователе:\n" + "\n".join(f"- {fact}" for fact in facts)
    return ""

async def extract_facts(user_id, user_text, assistant_response):
    prompt = f"""Из диалога выдели факты о пользователе (предпочтения, уровень, интересы). 
Верни список строк в формате JSON, например: ["любит Python", "новичок", "хочет краткие ответы"].
Не добавляй лишних слов.
Диалог:
Пользователь: {user_text}
Ассистент: {assistant_response}
Факты:"""
    try:
        reply = await ask_ai("microsoft/phi-3-mini-128k-instruct:free", [{"role": "user", "content": prompt}], False)
        reply = reply.strip()
        # Извлечение JSON из ответа
        match = re.search(r'\[.*\]', reply, re.DOTALL)
        if match:
            facts = json.loads(match.group(0))
            if isinstance(facts, list):
                for fact in facts[:5]:
                    add_fact(user_id, fact[:200], 0.8)
    except Exception as e:
        print(f"Extract facts error: {e}")

# ==================== 6. LLM-JUDGE И ЭВРИСТИКИ ====================
async def judge_response(user_query, model_response, is_code_request):
    prompt = f"""Ты — строгий эксперт. Оцени ответ по трём критериям (каждый от 1 до 10):
- Точность: ответ соответствует вопросу, нет галлюцинаций.
- Полезность: даёт конкретное решение, объяснение, код.
- Корректность: код синтаксически верен (если запрос на код), нет ошибок.

Запрос пользователя: {user_query}
Ответ модели: {model_response}

Выведи только одно целое число – средний балл от 1 до 10.
"""
    try:
        judge_reply = await ask_ai("microsoft/phi-3-mini-128k-instruct:free", [{"role": "user", "content": prompt}], is_code_request=False)
        match = re.search(r'\b([1-9]|10)\b', judge_reply)
        if match:
            score = int(match.group(1))
            return max(1, min(10, score))
        else:
            return 5
    except:
        # fallback на эвристику
        return rate_response_fallback(user_query, model_response, is_code_request)

def rate_response_fallback(user_query, response, is_code_request):
    score = 5
    if len(response) > 200:
        score += 1
    if len(response) < 50:
        score -= 2
    if is_code_request and "```" in response:
        score += 2
    if "код" in user_query.lower() and "```" not in response:
        score -= 1
    return max(1, min(10, score))

# ==================== 7. БЕЗОПАСНЫЙ КАЛЬКУЛЯТОР ====================
def safe_calc(expr):
    allowed_ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv
    }
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        elif isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op = allowed_ops.get(type(node.op))
            if op is None:
                raise ValueError(f"Операция не разрешена: {type(node.op).__name__}")
            return op(left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            elif isinstance(node.op, ast.USub):
                return -operand
            else:
                raise ValueError(f"Унарная операция не разрешена")
        else:
            raise ValueError(f"Неразрешённый элемент: {type(node).__name__}")
    tree = ast.parse(expr, mode='eval')
    return _eval(tree.body)

# ==================== 8. АСИНХРОННАЯ ОЧЕРЕДЬ + API ====================
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
        "max_tokens": 3000
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

# ==================== 9. FALLBACK + TOOL CALLING ====================
async def ask_with_fallback(messages, is_code_request, intent, complexity=0):
    # 1. UCB1 выбор модели
    best_model = ucb1_select_model(intent)
    if best_model:
        try:
            reply = await ask_ai(best_model, messages, is_code_request)
            if reply and not reply.startswith("❌"):
                return reply
        except:
            pass
    # 2. Для сложных запросов: параллельный турнир двух лучших из статической матрицы
    if intent in ["code", "reasoning"] and complexity > 50:
        models_to_try = INTENT_MODEL_MATRIX.get(intent, ["llama31", "phi3"])[:2]
        model_ids = [MODELS.get(m, m) for m in models_to_try if m in MODELS or m in MODELS.values()]
        if len(model_ids) >= 2:
            results = await asyncio.gather(*[ask_ai(m, messages, is_code_request) for m in model_ids], return_exceptions=True)
            valid = [r for r in results if isinstance(r, str) and not r.startswith("❌")]
            if valid:
                best = max(valid, key=len)
                update_model_score(model_ids[0], +1)
                return best
    # 3. Классический fallback
    for model in FALLBACK_CHAIN:
        try:
            reply = await ask_ai(model, messages, is_code_request)
            if reply and not reply.startswith("❌"):
                return reply
        except:
            continue
    return "❌ Все модели временно недоступны"

# Обработка tool calls (калькулятор)
async def process_tool_calls(text, messages, original_messages):
    tool_pattern = r'\[TOOL_CALL\]\s*(.*?)\s*\[/TOOL_CALL\]'
    match = re.search(tool_pattern, text, re.DOTALL)
    if not match:
        return text
    try:
        tool_data = json.loads(match.group(1))
        if tool_data.get("tool") == "calculator":
            expr = tool_data.get("input", "")
            try:
                result = safe_calc(expr)
                tool_result = f"[TOOL_RESULT] {result} [/TOOL_RESULT]"
                # Добавляем результат в историю и делаем второй запрос
                new_messages = original_messages + [{"role": "assistant", "content": text}, {"role": "user", "content": tool_result}]
                final_reply = await ask_ai("deepseek/deepseek-chat:free", new_messages, False)
                return final_reply
            except Exception as e:
                return f"❌ Ошибка вычисления: {e}"
    except:
        pass
    return text

# ==================== 10. КЛАССИФИКАЦИЯ ИНТЕНТА, СЖАТИЕ, УТИЛИТЫ ====================
async def classify_intent(text):
    prompt = f"""Определи тип запроса (только одно слово из списка):
- code
- reasoning
- math
- translate
- chat
Запрос: {text}
Ответ:"""
    try:
        reply = await ask_ai("microsoft/phi-3-mini-128k-instruct:free", [{"role": "user", "content": prompt}], False)
        intent = reply.strip().lower()
        if intent in INTENT_MODEL_MATRIX:
            return intent
        return "chat"
    except:
        if re.search(r'(напиши|сделай|дай|код|скрипт|программу)', text, re.I):
            return "code"
        if re.search(r'(объясни|почему|как работает|докажи|рассуди)', text, re.I):
            return "reasoning"
        return "chat"

async def summarize_with_keywords(old_messages):
    if not old_messages:
        return None, None
    old_text = "\n".join([f"{m['role']}: {m['content'][:150]}" for m in old_messages])
    prompt = f"""Кратко (1-2 предложения) опиши суть этого диалога. Также выдели 3-5 ключевых слов через запятую.

Диалог:
{old_text}

Формат ответа:
Резюме: ...
Ключевые слова: слово1, слово2, слово3"""
    try:
        reply = await ask_ai("meta-llama/llama-3.1-8b-instruct:free", [{"role": "user", "content": prompt}], False)
        lines = reply.split("\n")
        summary = ""
        keywords = ""
        for line in lines:
            if line.startswith("Резюме:"):
                summary = line.replace("Резюме:", "").strip()
            if line.startswith("Ключевые слова:"):
                keywords = line.replace("Ключевые слова:", "").strip()
        return summary[:300], keywords[:200]
    except:
        return "Предыдущий диалог (сжато)", None

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
        btn_text = name.split()[0] + " " + (name.split()[1] if len(name.split()) > 1 else "")
        row.append(InlineKeyboardButton(text=btn_text[:20], callback_data=f"model_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== 11. ОСНОВНОЙ ОБРАБОТЧИК ====================
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
        f"🤖 *AI Router v7 | Агентная платформа*\n\n"
        f"🧠 *UCB1 routing* – самообучающийся выбор модели\n"
        f"📚 *Фактологическая память* – запоминает предпочтения\n"
        f"🔍 *RAG с переранжированием* – точный поиск\n"
        f"🧮 *Калькулятор* – точные вычисления\n"
        f"⚡ *Асинхронная очередь* – надёжность\n\n"
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
        c.execute("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await callback.message.edit_text("🗑 *История и факты очищены*", reply_markup=main_keyboard())
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
        c.execute("SELECT intent, model_id, wins, attempts FROM routing_stats ORDER BY wins*1.0/attempts DESC LIMIT 5")
        routing = c.fetchall()
        c.execute("SELECT fact FROM user_facts WHERE user_id = ? LIMIT 10", (user_id,))
        facts = c.fetchall()
        conn.close()
        stats_text = f"📊 *Статистика*\n\n• Сообщений: {hist_count}\n• Кэш: {cache_count}\n\n🏆 *Лучшие модели (score):*\n"
        for m in top_models:
            model_name = next((name for key, name in MODEL_NAMES.items() if MODELS.get(key) == m[0]), m[0])
            stats_text += f"• {model_name}: {m[1]} очков ({m[2]} раз)\n"
        stats_text += "\n🧭 *Лучшие маршруты (intent→model):*\n"
        for intent, model_id, wins, attempts in routing:
            stats_text += f"• {intent} → {model_id[:20]}: {wins}/{attempts}\n"
        if facts:
            stats_text += "\n📝 *Факты о вас:*\n" + "\n".join(f"• {f[0][:80]}" for f in facts[:5])
        await callback.message.edit_text(stats_text[:4000], reply_markup=main_keyboard(), parse_mode="Markdown")
        await callback.answer()
        return

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    # Rate limiter per user (не более 2 одновременных)
    if user_id not in user_semaphores:
        user_semaphores[user_id] = asyncio.Semaphore(2)
    async with user_semaphores[user_id]:
        # 1. Индексируем запрос в FAISS
        add_embedding(user_id, user_text, "user")

        # 2. Поиск семантически похожих контекстов с переранжированием
        similar_texts = find_similar_context_reranked(user_id, user_text, top_k=2)
        extra_context = ""
        if similar_texts:
            extra_context = "\n\nПохожие случаи из прошлого:\n" + "\n".join(similar_texts)

        # 3. Получаем факты о пользователе
        user_facts_context = get_user_facts_context(user_id)

        # 4. Определяем интент и сложность
        intent = await classify_intent(user_text)
        is_code_request = (intent == "code")
        lang = detect_language(user_text) if is_code_request else None
        complexity = len(user_text) + user_text.count('?') * 2 + user_text.count('почему') * 3

        # 5. Загружаем историю из SQLite
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("SELECT role, content, summary, keywords FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20", (user_id,))
        rows = c.fetchall()
        history = [{"role": r[0], "content": r[1], "summary": r[2], "keywords": r[3]} for r in reversed(rows)]
        # Сжатие, если много
        if len(history) > 8:
            old = history[:-4]
            recent = history[-4:]
            summary, keywords = await summarize_with_keywords(old)
            if summary:
                c.execute("INSERT INTO history (user_id, role, content, timestamp, summary, keywords) VALUES (?, ?, ?, ?, ?, ?)",
                          (user_id, "system", summary, datetime.now(), summary, keywords))
                conn.commit()
                history = recent
        # Добавляем текущее сообщение
        c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                  (user_id, "user", user_text, datetime.now()))
        conn.commit()
        history.append({"role": "user", "content": user_text})
        # Добавляем семантический контекст и факты
        if extra_context:
            history.append({"role": "system", "content": extra_context})
        if user_facts_context:
            history.append({"role": "system", "content": user_facts_context})

        messages_for_api = [{"role": h["role"], "content": h["content"]} for h in history]

        # 6. Выбор модели
        user_model_setting = get_user_model(user_id)
        profile = get_user_profile(user_id)
        if user_model_setting == "auto":
            model_to_use = ucb1_select_model(intent)
            # Адаптация под сложность
            if complexity > 80:
                model_to_use = MODELS.get("llama33", model_to_use)
            elif complexity < 20 and intent == "chat":
                model_to_use = MODELS.get("phi3", model_to_use)
            model_display = "🤖 Автовыбор (UCB1)"
        else:
            model_to_use = MODELS.get(user_model_setting, "openrouter/free")
            model_display = MODEL_NAMES.get(user_model_setting, "AI")

        # 7. Кэш
        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_for_api[-3:], sort_keys=True)).encode()).hexdigest()
        c_cache = conn.cursor()
        c_cache.execute("SELECT response FROM cache WHERE key = ?", (cache_key,))
        cached = c_cache.fetchone()
        if cached:
            await message.answer(cached[0], reply_markup=main_keyboard())
            conn.close()
            return

        # 8. Отправляем запрос
        status_msg = await message.answer(f"⏳ *{model_display}* анализирует...", parse_mode="Markdown")
        reply = await ask_with_fallback(messages_for_api, is_code_request, intent, complexity)

        # 9. Обработка tool calls (калькулятор)
        original_messages = messages_for_api.copy()
        reply = await process_tool_calls(reply, messages_for_api, original_messages)

        # 10. Оценка ответа с помощью LLM-Judge
        judge_score = await judge_response(user_text, reply, is_code_request)
        delta = judge_score - 5
        if delta > 5: delta = 5
        if delta < -5: delta = -5
        update_model_score(model_to_use, delta)
        update_routing_stats(intent, model_to_use, success=(judge_score >= 7))

        # 11. Извлечение фактов о пользователе (асинхронно, не блокируем)
        asyncio.create_task(extract_facts(user_id, user_text, reply))

        # 12. Форматируем код
        if is_code_request and not reply.startswith('```'):
            reply = format_code_response(reply, lang)

        # 13. Сохраняем в кэш и историю
        c.execute("INSERT OR REPLACE INTO cache (key, response, created_at) VALUES (?, ?, ?)",
                  (cache_key, reply[:2000], datetime.now()))
        c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                  (user_id, "assistant", reply[:3000], datetime.now()))
        conn.commit()
        add_embedding(user_id, reply, "assistant")
        conn.close()

        # 14. Обновляем профиль
        update_user_profile(user_id, len(reply))

        # 15. Отправляем ответ
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

# ==================== 12. ЗАПУСК ====================
async def on_shutdown():
    global session
    if session and not session.closed:
        await session.close()
    save_faiss()

async def main():
    global session
    session = aiohttp.ClientSession()
    for i in range(queue_workers):
        asyncio.create_task(worker(i))
    print("🤖 AI Router v7 запущен (UCB1, факты, reranker, калькулятор)")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
