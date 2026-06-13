import re
import ipaddress
from typing import Optional
from modules.config import config

class Utils:
    """Вспомогательные утилиты"""
    
    # Запрещённые IP для SSRF защиты
    FORBIDDEN_NETWORKS = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("0.0.0.0/8"),
    ]
    
    @classmethod
    def sanitize_input(cls, text: str, max_length: int = config.MAX_MESSAGE_LEN) -> str:
        """Очистка пользовательского ввода"""
        if not text:
            return ""
        # Удаляем опасные символы
        text = re.sub(r'[<>{}]', '', text)
        # Ограничиваем длину
        if len(text) > max_length:
            text = text[:max_length]
        return text.strip()
    
    @classmethod
    def is_safe_url(cls, url: str) -> bool:
        """Проверка URL на безопасность (защита от SSRF)"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                return False
            if host in ['localhost', '127.0.0.1', '0.0.0.0', '::1']:
                return False
            try:
                ip = ipaddress.ip_address(host)
                for network in cls.FORBIDDEN_NETWORKS:
                    if ip in network:
                        return False
            except ValueError:
                pass
            return True
        except:
            return False
    
    @classmethod
    def validate_email(cls, email: str) -> bool:
        """Проверка email"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    @classmethod
    def truncate_text(cls, text: str, max_length: int = 100) -> str:
        """Обрезка текста"""
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."

utils = Utils()
