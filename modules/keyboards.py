from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from modules.config import config

class Keyboards:
    @staticmethod
    def main() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="👤 Личный кабинет", callback_data="profile")
        builder.button(text="🤖 Сменить модель", callback_data="show_models")
        builder.button(text="📊 Статистика", callback_data="stats")
        builder.button(text="🔗 Рефералы", callback_data="referral")
        builder.button(text="❓ Помощь", callback_data="help")
        if config.CRYPTOBOT_TOKEN:
            builder.button(text="💎 Купить премиум", callback_data="premium")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def models() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for model_id in config.MODEL_LIST:
            builder.button(text=config.MODEL_NAMES[model_id], callback_data=f"model_{model_id}")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(2)
        return builder.as_markup()
    
    @staticmethod
    def profile() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="💎 Купить премиум", callback_data="premium")
        builder.button(text="🔗 Реферальная ссылка", callback_data="show_ref")
        builder.button(text="📊 Мои запросы", callback_data="my_stats")
        builder.button(text="🗑 Очистить историю", callback_data="clear_history")
        builder.button(text="📤 Экспорт диалога", callback_data="export")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def admin() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="📊 Статистика", callback_data="admin_stats")
        builder.button(text="👥 Пользователи", callback_data="admin_users")
        builder.button(text="✉️ Рассылка", callback_data="admin_broadcast")
        builder.button(text="🤖 Метрики", callback_data="admin_metrics")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(2)
        return builder.as_markup()
    
    @staticmethod
    def export() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="📄 TXT", callback_data="export_txt")
        builder.button(text="📦 JSON", callback_data="export_json")
        builder.button(text="◀️ Назад", callback_data="back_to_profile")
        builder.adjust(2)
        return builder.as_markup()

kb = Keyboards()
