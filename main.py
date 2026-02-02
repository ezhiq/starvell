# -*- coding: utf-8 -*-
"""
SOFT BY @ezhiqq / @xezzq

ПО ВОПРОСАМ И ЗАКАЗАМ ОБРАЩАЙТЕСЬ
"""
"""
SOFT BY @ezhiqq / @xezzq

ПО ВОПРОСАМ И ЗАКАЗАМ ОБРАЩАЙТЕСЬ 
"""
"""
SOFT BY @ezhiqq / @xezzq

ПО ВОПРОСАМ И ЗАКАЗАМ ОБРАЩАЙТЕСЬ 
"""

import asyncio
import logging
import sys
from pathlib import Path

from telegram_manager import TelegramManager
from service_manager import ServiceManager
from config_manager import ConfigManager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска"""
    
    logger.info("=" * 60)
    logger.info("🚀 StarvellBot Manager Starting...")
    logger.info("=" * 60)
    
    # ========================================
    # НАСТРОЙКИ TELEGRAM БОТА
    # ========================================
    
    # Токен бота (получить у @BotFather)
    BOT_TOKEN = "8298689795:AAEuT7KxgcxPPKfYBE2JFkup_yNj-0gfH6k"
    
    # ID администраторов (получить у @userinfobot)
    ADMIN_IDS = [1094682920]  # Замените на ваши Telegram ID
    
    # ========================================
    # ПРОВЕРКА НАСТРОЕК
    # ========================================
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN не установлен!")
        logger.error("Получите токен у @BotFather и укажите его в main.py")
        return
    
    if ADMIN_IDS == [123456789]:
        logger.warning("⚠️ ADMIN_IDS не настроены!")
        logger.warning("Получите ваш ID у @userinfobot и укажите его в main.py")
        return
    
    # ========================================
    # ИНИЦИАЛИЗАЦИЯ КОМПОНЕНТОВ
    # ========================================
    
    try:
        # Менеджер конфигурации
        logger.info("📋 Загрузка конфигурации...")
        config_manager = ConfigManager()
        
        # Генерируем config.py для совместимости со старым кодом
        config_manager.generate_config_py()
        
        # Импортируем StarvellBot только после генерации config.py
        try:
            from starvell_bot import StarvellBot
        except ImportError as e:
            logger.error(f"❌ Ошибка импорта StarvellBot: {e}")
            logger.error("Убедитесь что все зависимости установлены")
            return

        # Telegram Manager
        logger.info("📱 Инициализация Telegram Manager...")
        telegram_manager = TelegramManager(
            bot_token=BOT_TOKEN,
            admin_ids=ADMIN_IDS
        )

        # Создаём экземпляр бота
        logger.info("🤖 Инициализация StarvellBot...")
        starvell_bot = StarvellBot(telegram_manager, ADMIN_IDS[0])
        
        # Менеджер служб
        logger.info("⚙️ Инициализация Service Manager...")
        service_manager = ServiceManager()
        service_manager.set_bot_instance(starvell_bot)
        
        # Привязываем service_manager к telegram_manager
        telegram_manager.service_manager = service_manager
        telegram_manager.config_manager = config_manager
        
        logger.info("=" * 60)
        logger.info("✅ Все компоненты инициализированы успешно!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("📱 Откройте Telegram бота для управления")
        logger.info("🎛️ Доступные команды:")
        logger.info("   /start - начать работу")
        logger.info("   /services - управление службами")
        logger.info("   /config - редактирование настроек")
        logger.info("   /status - статус служб")
        logger.info("   /help - справка")
        logger.info("")
        logger.info("=" * 60)
        
        # Запускаем Telegram Manager
        await telegram_manager.start()
    
    except KeyboardInterrupt:
        logger.info("\n⏸ Получен сигнал остановки...")
    
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        import traceback

        traceback.print_exc()
    
    finally:
        logger.info("👋 Завершение работы...")
        
        # Останавливаем все службы если они запущены
        try:
            if 'service_manager' in locals():
                await service_manager.stop_all()
        except Exception as e:
            logger.error(f"Ошибка при остановке служб: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏸ Остановка по Ctrl+C")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
