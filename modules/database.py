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
    
    async def init(self, dsn: str = None) -> None:
        """Инициализация пула соединений с PostgreSQL"""
        if dsn is None:
            dsn = config.DATABASE_URL
        
        self.pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
            max_queries=50000,
            max_inactive_connection_lifetime=300
        )
        await self._create_tables()
        logger.info("PostgreSQL подключён")
    
    async def _create_tables(self) -> None:
        """Создание всех необходимых таблиц с авто-исправлением"""
        async with self.pool.acquire() as conn:
            
            # ================================================================
            # 1. Таблица users (с авто-исправлением структуры)
            # ================================================================
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT,
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
            
            # Автоматическое исправление: проверяем и добавляем PRIMARY KEY
            await conn.execute('''
                DO $$
                BEGIN
                    -- Проверяем наличие PRIMARY KEY на user_id
                    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints 
                                   WHERE table_name='users' AND constraint_type='PRIMARY KEY') THEN
                        -- Удаляем возможные дубликаты
                        DELETE FROM users a USING users b 
                        WHERE a.user_id = b.user_id AND a.created_at < b.created_at;
                        -- Добавляем PRIMARY KEY
                        ALTER TABLE users ADD PRIMARY KEY (user_id);
                    END IF;
                END $$;
            ''')
            
            # Проверяем и добавляем колонки если их нет
            await conn.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='user_id') THEN
                        ALTER TABLE users ADD COLUMN user_id BIGINT;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='updated_at') THEN
                        ALTER TABLE users ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='referrer_id') THEN
                        ALTER TABLE users ADD COLUMN referrer_id BIGINT;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='referral_code') THEN
                        ALTER TABLE users ADD COLUMN referral_code TEXT UNIQUE;
                    END IF;
                END $$;
            ''')
            
            # ================================================================
            # 2. Таблица history
            # ================================================================
            
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
            
            # Добавляем недостающие колонки в history
            await conn.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='history' AND column_name='tokens_used') THEN
                        ALTER TABLE history ADD COLUMN tokens_used INT DEFAULT 0;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='history' AND column_name='model') THEN
                        ALTER TABLE history ADD COLUMN model TEXT;
                    END IF;
                END $$;
            ''')
            
            # ================================================================
            # 3. Таблица usage
            # ================================================================
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS usage (
                    user_id BIGINT,
                    date DATE,
                    count INT DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            ''')
            
            # ================================================================
            # 4. Таблица referrals
            # ================================================================
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    user_id BIGINT PRIMARY KEY,
                    invited_count INT DEFAULT 0,
                    bonus_balance INT DEFAULT 0,
                    total_earned INT DEFAULT 0
                )
            ''')
            
            # Добавляем недостающие колонки в referrals
            await conn.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='referrals' AND column_name='total_earned') THEN
                        ALTER TABLE referrals ADD COLUMN total_earned INT DEFAULT 0;
                    END IF;
                END $$;
            ''')
            
            # ================================================================
            # 5. Таблица payments
            # ================================================================
            
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
            
            # Добавляем недостающие колонки в payments
            await conn.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='payments' AND column_name='payment_id') THEN
                        ALTER TABLE payments ADD COLUMN payment_id TEXT;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='payments' AND column_name='completed_at') THEN
                        ALTER TABLE payments ADD COLUMN completed_at TIMESTAMP;
                    END IF;
                END $$;
            ''')
            
            # ================================================================
            # 6. Индексы для производительности
            # ================================================================
            
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id, timestamp DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp)')
            
            logger.info("Таблицы созданы/проверены")
    
    async def close(self) -> None:
        """Закрытие пула соединений"""
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL пул закрыт")
    
    # ================================================================
    # ОСНОВНЫЕ МЕТОДЫ
    # ================================================================
    
    async def create_user(self, user_id: int, referral_code: str = None) -> None:
        """Создание нового пользователя"""
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
    
    async def get_user(self, user_id: int) -> Optional[dict]:
        """Получение данных пользователя"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
    
    async def get_user_model(self, user_id: int) -> str:
        """Получение текущей модели пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT model FROM users WHERE user_id = $1', user_id)
            return row[0] if row else config.MODEL_LIST[0]
    
    async def update_user_model(self, user_id: int, model: str) -> None:
        """Обновление модели пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET model = $1, updated_at = NOW() WHERE user_id = $2', model, user_id)
    
    async def add_history(self, user_id: int, role: str, content: str, model: str = None, tokens: int = 0) -> None:
        """Добавление записи в историю"""
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO history (user_id, role, content, model, tokens_used) VALUES ($1, $2, $3, $4, $5)',
                              user_id, role, content[:4000], model, tokens)
            await conn.execute('DELETE FROM history WHERE id IN (SELECT id FROM history WHERE user_id = $1 ORDER BY timestamp DESC OFFSET $2)',
                              user_id, config.MAX_HISTORY)
    
    async def get_history(self, user_id: int, limit: int = 20) -> List[Dict[str, str]]:
        """Получение истории диалога"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT role, content FROM history WHERE user_id = $1 ORDER BY timestamp ASC LIMIT $2', user_id, limit)
            return [{"role": r[0], "content": r[1]} for r in rows]
    
    async def clear_history(self, user_id: int) -> None:
        """Очистка истории пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE user_id = $1', user_id)
    
    async def get_history_count(self, user_id: int) -> int:
        """Количество сообщений в истории"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM history WHERE user_id = $1', user_id)
            return row[0] if row else 0
    
    async def get_user_tier(self, user_id: int) -> str:
        """Получение тарифа пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            return row[0] if row else "free"
    
    async def set_user_tier(self, user_id: int, tier: str) -> None:
        """Установка тарифа пользователя"""
        if tier not in config.TIER_LIMITS:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET tier = $1 WHERE user_id = $2', tier, user_id)
    
    async def get_usage_stats(self, user_id: int) -> Tuple[str, int, int, int]:
        """Получение статистики использования"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
            used = row[0] if row else 0
            return tier, used, max(0, limit - used), limit
    
    async def increment_usage(self, user_id: int) -> Tuple[bool, int]:
        """Увеличение счётчика использования"""
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
        """Проверка бана пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT is_banned, ban_reason FROM users WHERE user_id = $1', user_id)
            if row and row['is_banned']:
                return True, row['ban_reason'] or "Нарушение правил"
            return False, ""
    
    async def ban_user(self, user_id: int, reason: str = "Нарушение правил") -> None:
        """Бан пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = TRUE, ban_reason = $1 WHERE user_id = $2', reason, user_id)
        logger.info(f"Пользователь {user_id} забанен: {reason}")
    
    async def unban_user(self, user_id: int) -> None:
        """Разбан пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = FALSE, ban_reason = NULL WHERE user_id = $1', user_id)
        logger.info(f"Пользователь {user_id} разбанен")
    
    async def get_total_users(self) -> int:
        """Общее количество пользователей"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0] if row else 0
    
    async def get_banned_count(self) -> int:
        """Количество забаненных пользователей"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users WHERE is_banned = TRUE')
            return row[0] if row else 0
    
    async def get_today_requests(self) -> int:
        """Количество запросов сегодня"""
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(count) FROM usage WHERE date = $1', today)
            return row[0] if row else 0
    
    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Получение или создание реферального кода"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT referral_code FROM users WHERE user_id = $1', user_id)
            if row and row['referral_code']:
                return row['referral_code']
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            await conn.execute('UPDATE users SET referral_code = $1 WHERE user_id = $2', code, user_id)
            return code
    
    async def get_referral_info(self, user_id: int) -> Tuple[int, int]:
        """Получение реферальной информации"""
        async with self.pool.acquire() as conn:
            invited = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referrer_id = $1', user_id) or 0
            bonus = await conn.fetchval('SELECT bonus_balance FROM referrals WHERE user_id = $1', user_id) or 0
            return invited, bonus
    
    async def cleanup_old_history(self) -> None:
        """Очистка старой истории (старше 30 дней)"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE timestamp < NOW() - INTERVAL \'30 days\'')
            logger.info("Очистка старой истории выполнена")
    
    async def get_users_list(self, limit: int = 100, offset: int = 0) -> List[dict]:
        """Получение списка пользователей (для админов)"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT user_id, tier, is_banned, created_at FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2', limit, offset)
            return [dict(row) for row in rows]

db = DatabaseManager()
