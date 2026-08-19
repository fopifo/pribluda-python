"""
Telegram бот для оповещений о роботах
Работает через Telegram API (с IP для обхода DNS)
"""

import asyncio
import json
import ssl
from typing import Optional, Dict, List
from datetime import datetime
from pathlib import Path
import sys
import os
from dotenv import load_dotenv

# parent -> папка tg_bot/, ещё parent -> корень проекта. Верно только
# пока bot.py лежит именно в tg_bot/ прямо в корне проекта — если папку
# когда-нибудь вложат глубже, эту строку придётся поправить.
ROOT_DIR = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(ROOT_DIR))

from loguru import logger

# Загружаем .env
load_dotenv()

# Пытаемся импортировать aiohttp
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    logger.warning("⚠️ aiohttp не установлен. Telegram бот не будет работать")


class TelegramBot:
    """
    Telegram бот для отправки оповещений
    Работает через HTTP API (без VPN, использует IP для обхода DNS)
    """
    
    # IP адреса Telegram API (для обхода DNS)
    TELEGRAM_IPS = [
        "149.154.167.220",  # Telegram API IP
        "149.154.167.221",
        "149.154.167.222",
    ]
    
    def __init__(self):
        """Инициализация из переменных окружения"""
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.use_ip = os.getenv("TELEGRAM_USE_IP", "True").lower() == "true"
        self.enabled = (
            bool(self.token) and 
            bool(self.chat_id) and 
            HAS_AIOHTTP and
            os.getenv("TELEGRAM_ENABLED", "False").lower() == "true"
        )
        self.min_confidence = float(os.getenv("TELEGRAM_MIN_CONFIDENCE", 0.6))
        self._session: Optional[aiohttp.ClientSession] = None
        
        if self.enabled:
            logger.success("✅ Telegram бот инициализирован")
            logger.info(f"   Chat ID: {self.chat_id}")
            logger.info(f"   Min confidence: {self.min_confidence:.0%}")
            logger.info(f"   Use IP: {self.use_ip}")
        else:
            if not self.token:
                logger.debug("ℹ️ TELEGRAM_BOT_TOKEN не задан")
            if not self.chat_id:
                logger.debug("ℹ️ TELEGRAM_CHAT_ID не задан")
            if not HAS_AIOHTTP:
                logger.debug("ℹ️ aiohttp не установлен")
    
    async def __aenter__(self):
        if self.enabled:
            # Создаем SSL контекст
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self._session = aiohttp.ClientSession(connector=connector)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()
    
    async def _send_request(self, url: str, payload: Dict) -> bool:
        """Отправка запроса с обработкой ошибок"""
        try:
            if not self._session:
                self._session = aiohttp.ClientSession()
            
            headers = {
                "Content-Type": "application/json",
                "Host": "api.telegram.org",
                "User-Agent": "LiveScreener/1.0"
            }
            
            async with self._session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    logger.debug("📤 Сообщение отправлено в Telegram")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"❌ Ошибка отправки в Telegram: {response.status} - {error_text}")
                    return False
                    
        except aiohttp.ClientError as e:
            logger.error(f"❌ Ошибка соединения: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка Telegram: {e}")
            return False
    
    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Отправка сообщения в Telegram
        
        Args:
            text: Текст сообщения
            parse_mode: HTML / Markdown
        
        Returns:
            True если отправлено успешно
        """
        if not self.enabled:
            return False
        
        # Формируем payload
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        
        # Пробуем отправить через домен
        domain_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        result = await self._send_request(domain_url, payload)
        
        if result:
            return True
        
        # Если через домен не получилось, пробуем через IP
        if self.use_ip:
            logger.debug("🔄 Пробуем отправить через IP...")
            for ip in self.TELEGRAM_IPS:
                ip_url = f"https://{ip}/bot{self.token}/sendMessage"
                logger.debug(f"   Пробуем IP: {ip}")
                result = await self._send_request(ip_url, payload)
                if result:
                    logger.debug(f"✅ Отправлено через IP: {ip}")
                    return True
        
        return False
    
    async def send_robot_alert(self, robot_data: Dict) -> bool:
        """
        Отправка оповещения о роботе
        
        Args:
            robot_data: Данные о роботе
        """
        if not self.enabled:
            return False
        
        # Проверяем уверенность
        confidence = robot_data.get('confidence', 0)
        if confidence < self.min_confidence:
            logger.debug(f"⏭️ Пропуск: уверенность {confidence:.0%} < {self.min_confidence:.0%}")
            return False
        
        # Формируем сообщение
        ticker = robot_data.get('ticker', 'Unknown')
        direction = robot_data.get('direction', 'unknown')
        pattern_type = robot_data.get('pattern_type', 'equal_volume')
        pattern_name = robot_data.get('pattern_name', 'Классический')
        volume = robot_data.get('volume_lots', 0)
        hits = robot_data.get('hits_count', 0)
        confidence = robot_data.get('confidence', 0)
        interval = robot_data.get('avg_interval', 0)
        
        direction_emoji = "🟢" if direction == "buy" else "🔴"
        pattern_emojis = {
            "equal_volume": "🤖",
            "market_maker": "🔄",
            "volume_spike": "⚡",
            "price_ladder": "📈",
            "iceberg": "🧊",
            "whale": "🐋",
            "arbitrage": "⚖️",
            "market_maker_v2": "🔄"
        }
        pattern_emoji = pattern_emojis.get(pattern_type, "🤖")
        
        message = f"""
<b>🤖 ОБНАРУЖЕН РОБОТ</b>

{pattern_emoji} <b>{pattern_name}</b>
{direction_emoji} <b>{direction.upper()}</b>

📊 <b>Тикер:</b> {ticker}
📦 <b>Объем:</b> {volume:,} лотов
🔄 <b>Повторов:</b> {hits}
⏱ <b>Интервал:</b> {interval:.2f}с
🎯 <b>Уверенность:</b> {confidence:.0%}

🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}
        """.strip()
        
        return await self.send_message(message)
    
    async def send_robot_disappeared(self, robot_data: Dict) -> bool:
        """Отправка оповещения об исчезновении робота"""
        if not self.enabled:
            return False
        
        ticker = robot_data.get('ticker', 'Unknown')
        direction = robot_data.get('direction', 'unknown')
        hits = robot_data.get('hits_count', 0)
        
        message = f"""
<b>💀 РОБОТ ИСЧЕЗ</b>

📊 <b>Тикер:</b> {ticker}
{direction.upper()}
🔄 <b>Повторов:</b> {hits}

🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}
        """.strip()
        
        return await self.send_message(message)

    async def send_arb_alert(self, arb_data: Dict) -> bool:
        """
        Отправка оповещения о расхождении арбитражной связки.

        Args:
            arb_data: pair_name, symbol_a, symbol_b, ratio, baseline,
                      deviation_pct
        """
        if not self.enabled:
            return False

        pair_name = arb_data.get('pair_name', 'Unknown')
        symbol_a = arb_data.get('symbol_a', '?')
        symbol_b = arb_data.get('symbol_b', '?')
        ratio = arb_data.get('ratio', 0.0)
        baseline = arb_data.get('baseline', 0.0)
        deviation_pct = arb_data.get('deviation_pct', 0.0)

        direction_emoji = "📈" if deviation_pct > 0 else "📉"

        message = f"""
<b>⚖️ АРБИТРАЖ: РАСХОЖДЕНИЕ</b>

🔗 <b>Связка:</b> {pair_name} ({symbol_a}/{symbol_b})
{direction_emoji} <b>Отклонение:</b> {deviation_pct:+.2f}%

📊 <b>Текущее отношение:</b> {ratio:.4f}
📏 <b>Обычное отношение:</b> {baseline:.4f}

🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}
        """.strip()

        return await self.send_message(message)

    async def send_status(self, stats: Dict) -> bool:
        """Отправка статуса"""
        if not self.enabled:
            return False
        
        message = f"""
<b>📊 СТАТУС СКРИНЕРА</b>

🤖 <b>Всего роботов:</b> {stats.get('total_robots', 0)}
🟢 <b>Активных:</b> {stats.get('active_robots', 0)}
💀 <b>Неактивных:</b> {stats.get('inactive_robots', 0)}
📈 <b>Тиков:</b> {stats.get('ticks', 0)}
⏱ <b>Uptime:</b> {stats.get('uptime', 0):.0f}с
🕐 <b>Время:</b> {datetime.now().strftime('%H:%M:%S')}
        """.strip()
        
        return await self.send_message(message)
    
    async def send_daily_report(self, stats: Dict) -> bool:
        """Отправка дневного отчета"""
        if not self.enabled:
            return False
        
        message = f"""
<b>📊 ДНЕВНОЙ ОТЧЕТ</b>

🤖 <b>Обнаружено роботов:</b> {stats.get('total', 0)}
🟢 <b>BUY:</b> {stats.get('buy', 0)}
🔴 <b>SELL:</b> {stats.get('sell', 0)}
🟡 <b>MIXED:</b> {stats.get('mixed', 0)}

📈 <b>Всего тиков:</b> {stats.get('ticks', 0)}
⏱ <b>Время работы:</b> {stats.get('uptime', 0):.0f}с

📌 <b>Топ-5 тикеров:</b>
{self._format_top_tickers(stats.get('top_tickers', []))}

🕐 <b>Дата:</b> {datetime.now().strftime('%Y-%m-%d')}
        """.strip()
        return await self.send_message(message)
    
    def _format_top_tickers(self, top_list: List) -> str:
        """Форматирует топ-5 тикеров"""
        if not top_list:
            return "Нет данных"
        return "\n".join([f"   {i+1}. {t['ticker']} — {t['count']} раз" for i, t in enumerate(top_list[:5])])


# Глобальный экземпляр
telegram_bot = TelegramBot()


async def test_bot():
    """Тест Telegram бота"""
    print("\n" + "=" * 60)
    print("📱 ТЕСТ TELEGRAM БОТА")
    print("=" * 60)
    
    bot = telegram_bot
    
    if not bot.enabled:
        print("\n❌ Telegram бот не настроен")
        print("\n📝 Текущие настройки:")
        print(f"   Token: {'✅' if bot.token else '❌'}")
        print(f"   Chat ID: {'✅' if bot.chat_id else '❌'}")
        print(f"   aiohttp: {'✅' if HAS_AIOHTTP else '❌'}")
        return
    
    print(f"\n✅ Бот настроен")
    print(f"   Chat ID: {bot.chat_id}")
    print(f"   Min confidence: {bot.min_confidence:.0%}")
    print(f"   Use IP: {bot.use_ip}")
    
    async with bot:
        print("\n⏳ Отправка тестового сообщения...")
        success = await bot.send_message("✅ <b>Тестовое сообщение</b>\nБот работает!")
        
        if success:
            print("✅ Сообщение отправлено!")
            print("   Проверьте Telegram!")
        else:
            print("❌ Ошибка отправки")
            print("\n🔧 Возможные решения:")
            print("   1. Проверьте интернет-соединение")
            print("   2. Используйте VPN (если доступен)")
            print("   3. Проверьте токен и chat_id")


if __name__ == "__main__":
    asyncio.run(test_bot())