# -*- coding: utf-8 -*-
"""
Главный файл запуска StarvellBot с Telegram управлением
"""

import asyncio
import logging
import sys
from pathlib import Path
import traceback
from aiogram import Bot, Dispatcher

from logger import setup_logging
from service_manager import ServiceManager
from config_manager import ConfigManager
from stars_api_giver import StarsAPIGiver
from telegram_manager import router

import config as gi
logger = setup_logging()

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
        
        # Импортируем StarvellBot только после генерации config.py
        try:
            from starvell_bot import StarvellBot
        except ImportError as e:
            logger.error(f"❌ Ошибка импорта StarvellBot: {e}")
            logger.error("Убедитесь что все зависимости установлены")
            return

        bot = Bot(token=BOT_TOKEN)
        dp = Dispatcher()
        dp.include_router(router)

        gi.bot = bot
        gi.dp = dp

        logger.info("🚀 Запуск Telegram Manager...")

        # Создаём экземпляр бота
        logger.info("🤖 Инициализация StarvellBot...")
        starvell_bot = StarvellBot(ADMIN_IDS[0])
        
        # Менеджер служб
        logger.info("⚙️ Инициализация Service Manager...")
        service_manager = ServiceManager()
        service_manager.set_bot_instance(starvell_bot)
        
        # Привязываем service_manager к telegram_manager
        gi.service_manager = service_manager
        gi.config_manager = config_manager
        
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

        await dp.start_polling(bot)
    
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
                await gi.service_manager.stop_all()
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
