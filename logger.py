import logging
import os
from datetime import datetime

# Флаг, что логирование уже настроено
_logging_configured = False


def setup_logging():
    """Простая настройка логирования. Вызови эту функцию ТОЛЬКО в main.py"""
    global _logging_configured

    if _logging_configured:
        return logging.getLogger("StarvellBot")

    # Создаем директорию для логов
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    # Получаем корневой логгер
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Очищаем старые обработчики если есть
    logger.handlers.clear()

    # Форматтер
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )

    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Файловый обработчик
    file_handler = logging.FileHandler(
        f"{log_dir}/starvell_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _logging_configured = True

    # Возвращаем именованный логгер
    app_logger = logging.getLogger("StarvellBot")
    app_logger.info("✅ Логирование настроено")

    return app_logger