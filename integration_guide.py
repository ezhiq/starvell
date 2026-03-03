# -*- coding: utf-8 -*-
"""
=============================================================================
ПОЛНАЯ ИНТЕГРАЦИЯ FRAGMENT API В STARVELLBOT
=============================================================================

Этот файл показывает, как интегрировать логику выдачи Stars
в ваш существующий код StarvellBot.

ИНСТРУКЦИЯ ПО ИНТЕГРАЦИИ:
=============================================================================

1. Скопируйте файлы:
   - fragment_api.py → в корень проекта
   - stars_distributor.py → в корень проекта
   - config_example.py → скопируйте и переименуйте в config.py

2. Установите зависимости:
   pip install tonutils httpx tenacity

3. Обновите ваш config.py:
   - Добавьте fragment_hash, fragment_cookie
   - Добавьте ton_api_key, mnemonic
   - Проверьте destination_address

4. Замените в вашем StarvellBot:
   - Добавьте инициализацию StarsDistributor в __init__
   - Замените метод fragment_giver на новую реализацию
   - Обновите обработку ошибок

=============================================================================
"""

import asyncio
import logging
import traceback
from typing import Optional

# Импорты из вашего кода
import aiosqlite
import aiohttp

from config import cc
# Новые импорты для Fragment
from fragment_api import FragmentConfig
from stars_distributor import StarsDistributor, TONWalletConfig


class StarvellBotUpdated:
    """
    Обновленная версия вашего StarvellBot с интегрированным Fragment API
    
    Показывает все необходимые изменения для работы с выдачей Stars
    """
    
    def __init__(self):
        """Инициализация бота"""
        
        # ===== ВАШ СУЩЕСТВУЮЩИЙ КОД =====
        cookie_session = cc.get('cookie_session')
        my_id = cc.get('my_id')
        
        if cookie_session is None:
            raise ValueError('No session cookie provided')
        
        self.ws_url = "wss://starvell.com/socket.io/?EIO=4&transport=websocket"
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://starvell.com",
            "Cookie": f"session={cookie_session}"
        }
        
        self.my_id = my_id
        self.orders = {}
        self.users = {}
        
        # ===== НОВОЕ: ИНИЦИАЛИЗАЦИЯ FRAGMENT API =====
        self._init_fragment_distributor()
        
        logging.info("✅ StarvellBot initialized with Fragment API support")
    
    def _init_fragment_distributor(self):
        """
        Инициализация дистрибьютора Stars
        
        ВАЖНО: Этот метод нужно добавить в ваш __init__
        """
        try:
            # Импортируем конфигурацию
            from config import (
                fragment_hash,
                fragment_cookie,
                fragment_show_sender,
                ton_api_key,
                mnemonic,
                destination_address,
                is_testnet
            )
            
            # Создаем конфигурацию Fragment
            fragment_config = FragmentConfig(
                hash=fragment_hash,
                cookie=fragment_cookie,
                show_sender=fragment_show_sender
            )
            
            # Создаем конфигурацию TON кошелька
            ton_config = TONWalletConfig(
                api_key=ton_api_key,
                mnemonic=mnemonic,
                destination_address=destination_address,
                is_testnet=is_testnet
            )
            
            # Создаем дистрибьютор
            self.stars_distributor = StarsDistributor(
                fragment_config=fragment_config,
                ton_config=ton_config,
                db_path="orders.db"
            )
            
            logging.info("✅ Fragment distributor initialized")
            
        except ImportError as e:
            logging.error(f"❌ Missing config parameters: {e}")
            logging.error("Please add Fragment and TON config to config.py")
            raise
        except Exception as e:
            logging.error(f"❌ Failed to initialize Fragment distributor: {e}")
            raise
    
    async def fragment_giver(self, order) -> bool:
        """
        НОВАЯ РЕАЛИЗАЦИЯ fragment_giver
        
        Полная логика выдачи Stars через Fragment API:
        1. Подготовка через Fragment API
        2. Отправка TON транзакции
        3. Верификация транзакции
        4. Обновление заказа
        
        Args:
            order: Объект заказа с полями:
                   - order_id: ID заказа
                   - username: Telegram username получателя
                   - quantity: Количество Stars
                   - chat_id: ID чата для сообщений
        
        Returns:
            bool: True если успешно, False если ошибка
        """
        logging.info(
            f"[fragment_giver] Starting for order {order.order_id}: "
            f"username={order.username}, quantity={order.quantity}"
        )
        
        try:
            # ==========================================
            # Выдача Stars через дистрибьютор
            # ==========================================
            success, error, tx_data = await self.stars_distributor.distribute_stars(
                username=order.username,
                quantity=order.quantity,
                order_id=order.order_id
            )
            
            # ==========================================
            # Обработка результата
            # ==========================================
            if success:
                # Успешная выдача
                logging.info(
                    f"✅ [fragment_giver] Success for order {order.order_id}: "
                    f"tx_hash={tx_data['tx_hash']}, ref_id={tx_data['ref_id']}"
                )
                
                # Отправляем сообщение пользователю
                success_message = (
                    f"✅ Заказ #{order.order_id} выполнен!\n"
                    f"🔗 Транзакция: {tx_data['tonviewer_url']}\n"
                    f"⭐️ Отправлено Stars: {order.quantity}\n"
                    f"🔑 Ref ID: {tx_data['ref_id']}\n\n"
                    f"Пожалуйста, оставьте отзыв!"
                )
                
                await self.send_chat_message(order.chat_id, success_message)
                
                # Помечаем заказ как выполненный
                order.mark_completed()
                
                return True
                
            else:
                # Ошибка при выдаче
                logging.error(
                    f"❌ [fragment_giver] Failed for order {order.order_id}: {error}"
                )
                
                # Обрабатываем разные типы ошибок
                await self._handle_distribution_error(order, error, tx_data)
                
                return False
        
        except Exception as e:
            # Неожиданная ошибка
            logging.error(
                f"❌ [fragment_giver] Unexpected error for order {order.order_id}: {e}"
            )
            traceback.print_exc()
            
            # Отправляем сообщение об ошибке
            error_message = (
                f"❌ Произошла ошибка при обработке заказа.\n"
                f"Пожалуйста, свяжитесь с поддержкой.\n"
                f"Код заказа: {order.order_id}"
            )
            
            try:
                await self.send_chat_message(order.chat_id, error_message)
            except Exception as send_error:
                logging.error(f"Failed to send error message: {send_error}")
            
            # Возвращаем деньги при критической ошибке
            await self.refund_order(order.order_id)
            
            return False
    
    async def _handle_distribution_error(
        self, 
        order, 
        error: str, 
        tx_data: Optional[dict]
    ):
        """
        Обработка ошибок при выдаче Stars
        
        Args:
            order: Объект заказа
            error: Сообщение об ошибке
            tx_data: Данные транзакции (если есть)
        """
        error_lower = error.lower()
        
        # ===== Username не найден =====
        if 'username not found' in error_lower or 'no telegram users found' in error_lower:
            logging.warning(f"Username not found: {order.username}")
            
            message = (
                f"❌ Username {order.username} не найден в Telegram.\n"
                f"Пожалуйста, проверьте правильность написания и попробуйте снова.\n\n"
                f"Средства будут возвращены."
            )
            
            await self.send_chat_message(order.chat_id, message)
            await self.refund_order(order.order_id)
        
        # ===== Недостаточно средств =====
        elif 'insufficient funds' in error_lower or 'недостаточно средств' in error_lower:
            logging.error(f"Insufficient funds for order {order.order_id}")
            
            message = (
                f"❌ На кошельке недостаточно средств для выполнения заказа.\n"
                f"Средства будут возвращены.\n"
                f"Приносим извинения за неудобства."
            )
            
            await self.send_chat_message(order.chat_id, message)
            await self.refund_order(order.order_id)
            
            # Деактивируем лоты (опционально)
            # await self._deactivate_lots_due_to_insufficient_funds()
        
        # ===== Ошибка верификации транзакции =====
        elif 'verification failed' in error_lower:
            logging.error(f"Transaction verification failed for order {order.order_id}")
            
            if tx_data and tx_data.get('tx_hash'):
                # Транзакция отправлена, но не подтверждена
                message = (
                    f"⚠️ Не удалось подтвердить статус транзакции.\n"
                    f"🔗 Проверьте статус: {tx_data['tonviewer_url']}\n\n"
                    f"Если Stars не пришли в течение 10 минут, свяжитесь с поддержкой.\n"
                    f"Код заказа: {order.order_id}"
                )
            else:
                # Транзакция не отправлена
                message = (
                    f"❌ Не удалось выполнить транзакцию.\n"
                    f"Средства будут возвращены.\n"
                    f"Код заказа: {order.order_id}"
                )
                await self.refund_order(order.order_id)
            
            await self.send_chat_message(order.chat_id, message)
        
        # ===== Другие ошибки =====
        else:
            logging.error(f"Unknown error for order {order.order_id}: {error}")
            
            message = (
                f"❌ Произошла ошибка при обработке заказа.\n"
                f"Причина: {error}\n\n"
                f"Средства будут возвращены.\n"
                f"Код заказа: {order.order_id}"
            )
            
            await self.send_chat_message(order.chat_id, message)
            await self.refund_order(order.order_id)
    
    async def send_chat_message(self, chat_id: str, content: str):
        """
        Отправка сообщения в чат (ваша существующая функция)
        """
        payload = {"chatId": chat_id, "content": content}
        url = "https://starvell.com/api/messages/send"
        
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(url, json=payload) as resp:
                response_text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}: {response_text}")
                try:
                    return await resp.json()
                except Exception:
                    raise RuntimeError("Invalid response from server")
    
    async def refund_order(self, order_id: str):
        """
        Возврат средств за заказ (ваша существующая функция)
        """
        try:
            payload = {"orderId": order_id}
            url = "https://starvell.com/api/orders/refund"
            
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    logging.info(f"✅ Order {order_id} refunded")
                    return await resp.json()
        except Exception as e:
            logging.error(f"❌ Failed to refund order {order_id}: {e}")
            return None
    
    async def check_balance(self) -> Optional[float]:
        """
        Проверка баланса TON кошелька
        
        Полезно для проактивной проверки перед обработкой заказов
        
        Returns:
            float: Баланс в TON или None при ошибке
        """
        try:
            balance = await self.stars_distributor.check_wallet_balance()
            logging.info(f"💰 Wallet balance: {balance} TON")
            return balance
        except Exception as e:
            logging.error(f"❌ Failed to check balance: {e}")
            return None


# =============================================================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ В ВАШЕМ КОДЕ
# =============================================================================

async def integration_example():
    """
    Пример того, как использовать обновленный StarvellBot
    """
    
    # Создание бота с Fragment API
    bot = StarvellBotUpdated()
    
    # Проверка баланса перед началом работы
    balance = await bot.check_balance()
    if balance is None:
        print("❌ Failed to check balance")
        return
    
    if balance < 1.0:  # Минимум 1 TON для работы
        print(f"⚠️ Low balance: {balance} TON")
    
    # Пример обработки заказа
    class MockOrder:
        def __init__(self):
            self.order_id = "ORDER_123"
            self.username = "@example_user"
            self.quantity = 50
            self.chat_id = "chat_123"
            self.status = "paid"
        
        def mark_completed(self):
            self.status = "completed"
            print(f"✅ Order {self.order_id} marked as completed")
    
    order = MockOrder()
    
    # Выдача Stars
    success = await bot.fragment_giver(order)
    
    if success:
        print(f"✅ Order {order.order_id} processed successfully")
    else:
        print(f"❌ Order {order.order_id} processing failed")


# =============================================================================
# ОБНОВЛЕНИЯ В ВАШЕМ СУЩЕСТВУЮЩЕМ КОДЕ
# =============================================================================

"""
ШАГ 1: Обновите __init__ вашего StarvellBot

    def __init__(self):
        # ... существующий код ...
        
        # ДОБАВЬТЕ:
        self._init_fragment_distributor()


ШАГ 2: Добавьте метод _init_fragment_distributor

    def _init_fragment_distributor(self):
        '''Инициализация дистрибьютора Stars'''
        from config import (
            fragment_hash, fragment_cookie, fragment_show_sender,
            ton_api_key, mnemonic, destination_address, is_testnet
        )
        
        fragment_config = FragmentConfig(
            hash=fragment_hash,
            cookie=fragment_cookie,
            show_sender=fragment_show_sender
        )
        
        ton_config = TONWalletConfig(
            api_key=ton_api_key,
            mnemonic=mnemonic,
            destination_address=destination_address,
            is_testnet=is_testnet
        )
        
        self.stars_distributor = StarsDistributor(
            fragment_config=fragment_config,
            ton_config=ton_config
        )


ШАГ 3: Замените ваш метод fragment_giver

    async def fragment_giver(self, order):
        '''Выдача Stars покупателю'''
        
        success, error, tx_data = await self.stars_distributor.distribute_stars(
            username=order.username,
            quantity=order.quantity,
            order_id=order.order_id
        )
        
        if success:
            await self.send_chat_message(
                order.chat_id,
                f"✅ Заказ выполнен!\\n"
                f"🔗 Транзакция: {tx_data['tonviewer_url']}\\n"
                f"⭐️ Stars: {order.quantity}\\n"
                f"🔑 Ref ID: {tx_data['ref_id']}"
            )
            order.mark_completed()
            return True
        else:
            # Обработка ошибки
            await self._handle_distribution_error(order, error, tx_data)
            return False


ШАГ 4: Добавьте обработчик ошибок

    async def _handle_distribution_error(self, order, error, tx_data):
        '''Обработка ошибок выдачи Stars'''
        
        if 'username not found' in error.lower():
            await self.send_chat_message(
                order.chat_id,
                f"❌ Username {order.username} не найден"
            )
            await self.refund_order(order.order_id)
        
        elif 'insufficient funds' in error.lower():
            await self.send_chat_message(
                order.chat_id,
                "❌ Недостаточно средств, возврат денег"
            )
            await self.refund_order(order.order_id)
        
        else:
            await self.send_chat_message(
                order.chat_id,
                f"❌ Ошибка: {error}"
            )
            await self.refund_order(order.order_id)
"""


# =============================================================================
# ЗАПУСК ПРИМЕРА
# =============================================================================

if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запуск примера
    try:
        asyncio.run(integration_example())
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
