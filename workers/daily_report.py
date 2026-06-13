import asyncio
from datetime import datetime, timedelta
from modules.database import db
from modules.config import config
from aiogram import Bot

async def send_daily_report():
    """Отправка ежедневного отчёта админам"""
    
    # Собираем статистику
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    total_users = await db.get_total_users()
    new_users = await db.get_new_users_count(today)
    active_users = await db.get_today_active_users()
    total_requests = await db.get_today_requests()
    
    # Статистика по моделям
    top_model = await db.get_top_model()
    
    # Доход
    revenue = await db.get_today_revenue()
    
    text = f"""
📊 **Ежедневный отчёт** ({datetime.now().strftime('%d.%m.%Y')})

━━━━━━━━━━━━━━━━━━━
👥 **Пользователи**
━━━━━━━━━━━━━━━━━━━
Всего: {total_users}
➕ Новых: +{new_users}
👤 Активных сегодня: {active_users}

━━━━━━━━━━━━━━━━━━━
📈 **Запросы**
━━━━━━━━━━━━━━━━━━━
Всего сегодня: {total_requests}
🤖 Популярная модель: {top_model}

━━━━━━━━━━━━━━━━━━━
💰 **Доход**
━━━━━━━━━━━━━━━━━━━
Сегодня: ${revenue:.2f}
"""

    # Отправляем всем админам
    bot = Bot(token=config.BOT_TOKEN)
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
        except:
            pass
    await bot.session.close()
