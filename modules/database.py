# modules/database.py
import asyncpg
from asyncpg.pool import Pool
import logging
import random
import string
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from modules.config import config

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.pool: Optional[Pool] = None
    
    async def init(self) -> None:
        self.pool = await asyncpg.create_pool(
            config.DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
            max_queries=50000
        )
        await self._create_tables()
        logger.info("PostgreSQL подключён")
    
    async def _create_tables(self) -> None:
        async with self.pool.acquire() as conn:
            # ========== ОСНОВНЫЕ ТАБЛИЦЫ ==========
            
            # Таблица пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    model TEXT DEFAULT 'meta-llama/llama-4-scout-17b-16e-instruct',
                    tier TEXT DEFAULT 'free',
                    is_banned BOOLEAN DEFAULT FALSE,
                    ban_reason TEXT,
                    referrer_id BIGINT,
                    referral_code TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица истории сообщений
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    role TEXT,
                    content TEXT,
                    tokens_used INT DEFAULT 0,
                    model TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица использования (лимиты)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS usage (
                    user_id BIGINT,
                    date DATE,
                    count INT DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            ''')
            
            # Таблица рефералов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    user_id BIGINT PRIMARY KEY,
                    invited_count INT DEFAULT 0,
                    bonus_balance INT DEFAULT 0,
                    total_earned INT DEFAULT 0
                )
            ''')
            
            # Таблица платежей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount DECIMAL(10,2),
                    tier TEXT,
                    status TEXT DEFAULT 'pending',
                    payment_id TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP
                )
            ''')
            
            # ========== НОВЫЕ ТАБЛИЦЫ ==========
            
            # Таблица обратной связи
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    rating INT CHECK (rating >= 1 AND rating <= 5),
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица сессий пользователей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    user_agent TEXT,
                    ip_address TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP DEFAULT NOW() + INTERVAL '7 days'
                )
            ''')
            
            # Таблица статистики моделей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS model_stats (
                    id SERIAL PRIMARY KEY,
                    model TEXT,
                    date DATE,
                    requests INT DEFAULT 0,
                    avg_time FLOAT DEFAULT 0,
                    errors INT DEFAULT 0,
                    UNIQUE(model, date)
                )
            ''')
            
            # Таблица уведомлений
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    title TEXT,
                    message TEXT,
                    is_read BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Таблица промптов (сохранённые запросы)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS saved_prompts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    title TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # ========== ИНДЕКСЫ ==========
            
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id, timestamp DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON feedback(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON user_sessions(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id, is_read)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_model_stats_date ON model_stats(date)')
            
            logger.info("Все таблицы созданы/проверены")
    
    # ========== ОСНОВНЫЕ МЕТОДЫ ==========
    
    async def create_user(self, user_id: int, referral_code: str = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
            await conn.execute('INSERT INTO referrals (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
            
            if referral_code:
                referrer = await conn.fetchrow('SELECT user_id FROM users WHERE referral_code = $1', referral_code)
                if referrer and referrer['user_id'] != user_id:
                    existing = await conn.fetchval('SELECT referrer_id FROM users WHERE user_id = $1', user_id)
                    if not existing:
                        await conn.execute('UPDATE users SET referrer_id = $1 WHERE user_id = $2', referrer['user_id'], user_id)
                        await conn.execute('UPDATE referrals SET bonus_balance = bonus_balance + $1 WHERE user_id = $2', 
                                          config.REFERRAL_BONUS, referrer['user_id'])
                        await conn.execute('UPDATE referrals SET invited_count = invited_count + 1 WHERE user_id = $1', referrer['user_id'])
    
    async def get_user_model(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT model FROM users WHERE user_id = $1', user_id)
            return row[0] if row else config.MODEL_LIST[0]
    
    async def update_user_model(self, user_id: int, model: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET model = $1, updated_at = NOW() WHERE user_id = $2', model, user_id)
    
    async def add_history(self, user_id: int, role: str, content: str, model: str = None, tokens: int = 0) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO history (user_id, role, content, model, tokens_used) VALUES ($1, $2, $3, $4, $5)',
                              user_id, role, content[:4000], model, tokens)
            await conn.execute('DELETE FROM history WHERE id IN (SELECT id FROM history WHERE user_id = $1 ORDER BY timestamp DESC OFFSET $2)',
                              user_id, config.MAX_HISTORY)
    
    async def get_history(self, user_id: int, limit: int = 20) -> List[Dict[str, str]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT role, content FROM history WHERE user_id = $1 ORDER BY timestamp ASC LIMIT $2', user_id, limit)
            return [{"role": r[0], "content": r[1]} for r in rows]
    
    async def clear_history(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE user_id = $1', user_id)
    
    async def get_history_count(self, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM history WHERE user_id = $1', user_id)
            return row[0] if row else 0
    
    async def get_user_tier(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            return row[0] if row else "free"
    
    async def set_user_tier(self, user_id: int, tier: str) -> None:
        if tier not in config.TIER_LIMITS:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET tier = $1 WHERE user_id = $2', tier, user_id)
    
    async def get_usage_stats(self, user_id: int) -> Tuple[str, int, int, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
            used = row[0] if row else 0
            return tier, used, max(0, limit - used), limit
    
    async def increment_usage(self, user_id: int) -> Tuple[bool, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
            current = row[0] if row else 0
            
            if current >= limit:
                return False, 0
            
            if row:
                await conn.execute('UPDATE usage SET count = count + 1 WHERE user_id = $1 AND date = $2', user_id, today)
            else:
                await conn.execute('INSERT INTO usage (user_id, date, count) VALUES ($1, $2, 1)', user_id, today)
            
            return True, limit - (current + 1)
    
    async def is_banned(self, user_id: int) -> Tuple[bool, str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT is_banned, ban_reason FROM users WHERE user_id = $1', user_id)
            if row and row['is_banned']:
                return True, row['ban_reason'] or "Нарушение правил"
            return False, ""
    
    async def ban_user(self, user_id: int, reason: str = "Нарушение правил") -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = TRUE, ban_reason = $1 WHERE user_id = $2', reason, user_id)
        logger.info(f"Пользователь {user_id} забанен: {reason}")
    
    async def unban_user(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = FALSE, ban_reason = NULL WHERE user_id = $1', user_id)
        logger.info(f"Пользователь {user_id} разбанен")
    
    async def get_total_users(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0] if row else 0
    
    async def get_today_requests(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(count) FROM usage WHERE date = $1', today)
            return row[0] if row else 0
    
    async def get_or_create_referral_code(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT referral_code FROM users WHERE user_id = $1', user_id)
            if row and row['referral_code']:
                return row['referral_code']
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            await conn.execute('UPDATE users SET referral_code = $1 WHERE user_id = $2', code, user_id)
            return code
    
    async def get_referral_info(self, user_id: int) -> Tuple[int, int]:
        async with self.pool.acquire() as conn:
            invited = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referrer_id = $1', user_id) or 0
            bonus = await conn.fetchval('SELECT bonus_balance FROM referrals WHERE user_id = $1', user_id) or 0
            return invited, bonus
    
    async def cleanup_old_history(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE timestamp < NOW() - INTERVAL \'30 days\'')
            logger.info("Очистка старой истории выполнена")
    
    async def get_users_list(self, limit: int = 100, offset: int = 0) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT user_id, tier, is_banned, created_at FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2', limit, offset)
            return [dict(row) for row in rows]
    
    async def get_banned_count(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users WHERE is_banned = TRUE')
            return row[0] if row else 0
    
    # ========== НОВЫЕ МЕТОДЫ ==========
    
    async def save_feedback(self, user_id: int, rating: int, comment: str = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO feedback (user_id, rating, comment) VALUES ($1, $2, $3)', user_id, rating, comment)
    
    async def get_feedback_stats(self) -> dict:
        async with self.pool.acquire() as conn:
            avg = await conn.fetchval('SELECT AVG(rating) FROM feedback')
            total = await conn.fetchval('SELECT COUNT(*) FROM feedback')
            return {"avg_rating": round(float(avg), 2) if avg else 0, "total": total}
    
    async def save_session(self, session_id: str, user_id: int, user_agent: str = None, ip: str = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_sessions (session_id, user_id, user_agent, ip_address) 
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (session_id) DO UPDATE SET user_id = $2
            ''', session_id, user_id, user_agent, ip)
    
    async def get_session_user(self, session_id: str) -> Optional[int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT user_id FROM user_sessions WHERE session_id = $1 AND expires_at > NOW()', session_id)
            return row['user_id'] if row else None
    
    async def update_model_stats(self, model: str, duration: float, success: bool):
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO model_stats (model, date, requests, avg_time, errors) 
                VALUES ($1, $2, 1, $3, $4)
                ON CONFLICT (model, date) DO UPDATE SET 
                    requests = model_stats.requests + 1,
                    avg_time = (model_stats.avg_time * model_stats.requests + $3) / (model_stats.requests + 1),
                    errors = model_stats.errors + $4
            ''', model, today, duration, 0 if success else 1)
    
    async def add_notification(self, user_id: int, title: str, message: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO notifications (user_id, title, message) VALUES ($1, $2, $3)', user_id, title, message)
    
    async def get_notifications(self, user_id: int, limit: int = 10) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2', user_id, limit)
            return [dict(row) for row in rows]
    
    async def mark_notification_read(self, notification_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE notifications SET is_read = TRUE WHERE id = $1', notification_id)
    
    async def save_prompt(self, user_id: int, title: str, content: str) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('INSERT INTO saved_prompts (user_id, title, content) VALUES ($1, $2, $3) RETURNING id', user_id, title, content)
            return row['id']
    
    async def get_saved_prompts(self, user_id: int) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT id, title, content, created_at FROM saved_prompts WHERE user_id = $1 ORDER BY created_at DESC', user_id)
            return [dict(row) for row in rows]
    
    async def delete_saved_prompt(self, prompt_id: int, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute('DELETE FROM saved_prompts WHERE id = $1 AND user_id = $2', prompt_id, user_id)
            return result == "DELETE 1"
    
    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL пул закрыт")

db = DatabaseManager()
