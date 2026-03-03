# -*- coding: utf-8 -*-
"""
Интеграция Fragment API в StarvellBot
Обновленная версия fragment_giver с полной логикой выдачи Stars
"""

import asyncio
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

# Импорты из вашего кода
import aiosqlite

# Импорты нового Fragment API модуля
from fragment_api import (
    FragmentClient,
    FragmentConfig,
    TransactionResult,
    RecipientNotFoundError,
    FragmentAPIError
)

# Для TON транзакций (из вашего второго кода)
try:
    from tonutils.client import TonapiClient
    from tonutils.wallet import WalletV5R1
except ImportError:
    print("⚠️ tonutils не установлен. Установите: pip install tonutils")
    TonapiClient = None
    WalletV5R1 = None

logger = logging.getLogger("StarvellBot.Fragment")


@dataclass
class TONWalletConfig:
    """Конфигурация TON кошелька"""
    api_key: str
    mnemonic: list[str]  # 24 слова
    is_testnet: bool = False
    destination_address: str = "UQCFJEP4WZ_mpdo0_kMEmsTgvrMHG7K_tWY16pQhKHwoOtFz"
    use_old_balance_format: bool = False  # Добавьте этот параметр

class TONTransactionError(Exception):
    """Ошибка при отправке TON транзакции"""
    pass


class StarsDistributor:
    """
    Класс для выдачи Telegram Stars через Fragment
    Интегрирует Fragment API и TON Wallet
    """
    
    def __init__(
        self, 
        fragment_config: FragmentConfig,
        ton_config: TONWalletConfig,
        db_path: str = "orders.db"
    ):
        """
        Инициализация дистрибьютора Stars
        
        Args:
            fragment_config: Конфигурация Fragment API
            ton_config: Конфигурация TON кошелька
            db_path: Путь к базе данных заказов
        """
        self.fragment_config = fragment_config
        self.ton_config = ton_config
        self.db_path = db_path
        
        # Проверка наличия TON библиотек
        if TonapiClient is None or WalletV5R1 is None:
            raise ImportError("tonutils library is required for TON transactions")

    async def check_wallet_balance(self) -> float:
        """
        Проверка баланса TON кошелька

        Returns:
            Баланс в TON
        """
        try:
            client = TonapiClient(
                api_key=self.ton_config.api_key,
                is_testnet=self.ton_config.is_testnet
            )

            wallet, _, _, _ = WalletV5R1.from_mnemonic(client, self.ton_config.mnemonic)

            # Получаем баланс
            balance_raw = await wallet.balance()

            logger.debug(f"Raw balance from wallet: {balance_raw}")
            logger.debug(f"Balance type: {type(balance_raw)}")

            # Если включен старый формат, возвращаем как есть
            if self.ton_config.use_old_balance_format:
                balance_ton = float(balance_raw)
                logger.info(f"Wallet balance (old format): {balance_ton:.6f} TON")
                return balance_ton

            # Иначе определяем автоматически
            if isinstance(balance_raw, float):
                if balance_raw < 1000:
                    # Маленькое float - уже TON
                    logger.info(f"Wallet balance: {balance_raw:.6f} TON")
                    return balance_raw
                else:
                    # Большое float - конвертируем из nanoTON
                    balance_ton = balance_raw / 1_000_000_000
                    logger.info(f"Wallet balance: {balance_ton:.6f} TON (converted)")
                    return balance_ton
            elif isinstance(balance_raw, int):
                if balance_raw > 1_000_000_000:
                    # Большое int - nanoTON
                    balance_ton = balance_raw / 1_000_000_000
                    logger.info(f"Wallet balance: {balance_ton:.6f} TON (converted from int)")
                    return balance_ton
                else:
                    # Маленькое int - уже TON
                    balance_ton = float(balance_raw)
                    logger.info(f"Wallet balance: {balance_ton:.6f} TON")
                    return balance_ton

            # Любой другой тип
            balance_ton = float(balance_raw)
            logger.info(f"Wallet balance: {balance_ton:.6f} TON")
            return balance_ton

        except Exception as e:
            logger.error(f"Error checking wallet balance: {e}")
            raise TONTransactionError(f"Failed to check balance: {e}")

    async def send_ton_transaction(
            self,
            amount: float,
            comment: str,
            destination_address: str  # КРИТИЧНО: Адрес получателя из Fragment API!
    ) -> Tuple[Optional[str], Optional[str]]:

        try:
            # Проверка баланса
            balance = await self.check_wallet_balance()
            if balance < amount:
                error_msg = (
                    f"Insufficient funds. Required: {amount} TON, "
                    f"Available: {balance:.6f} TON"
                )
                logger.error(error_msg)
                return None, error_msg

            logger.info(f"Sending {amount} TON to {destination_address}")
            logger.info(f"Comment: {comment}")

            # Отправка транзакции
            client = TonapiClient(
                api_key=self.ton_config.api_key,
                is_testnet=self.ton_config.is_testnet
            )

            wallet, _, _, _ = WalletV5R1.from_mnemonic(client, self.ton_config.mnemonic)

            # ВАЖНО: Используем адрес из Fragment API, а не статический!
            tx_hash = await wallet.transfer(
                destination=destination_address,  # Адрес Fragment
                amount=amount,  # УЖЕ В TON
                body=comment,
            )

            logger.info(f"✅ Transaction sent to Fragment: {amount} TON")
            logger.info(f"TX Hash: {tx_hash}")
            logger.info(f"Destination: {destination_address}")

            return tx_hash, None

        except Exception as e:
            error_msg = f"Error sending TON to Fragment: {e}"
            logger.error(error_msg)
            return None, error_msg
    
    async def verify_transaction(
        self, 
        tx_hash: str, 
        max_attempts: int = 25,
        delay: int = 5
    ) -> bool:
        """
        Проверка успешности транзакции через TON API
        
        Args:
            tx_hash: Хеш транзакции
            max_attempts: Максимальное количество попыток проверки
            delay: Задержка между попытками (секунды)
            
        Returns:
            True если транзакция успешна, False иначе
        """
        import httpx
        
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f'https://preview.toncenter.com/api/v3/traces'
                        f'?msg_hash={tx_hash}&include_actions=true',
                        timeout=10.0
                    )

                    if response.status_code == 404:
                        logger.warning(
                            f"Attempt {attempt + 1}: Transaction not found (404)"
                        )
                        await asyncio.sleep(delay)
                        continue
                    
                    response.raise_for_status()
                    data = response.json()

                print(response.status_code)
                print(data)

                # Проверяем успешность транзакции
                for trace in data.get('traces', []):
                    for action in trace.get('actions', []):
                        if action.get('success', False):
                            logger.info(
                                f"Transaction verified successfully: "
                                f"{action['trace_external_hash']}"
                            )
                            return True
                
                logger.warning(
                    f"Attempt {attempt + 1}/{max_attempts}: "
                    "Transaction not successful yet"
                )
                await asyncio.sleep(delay)
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(
                        f"Attempt {attempt + 1}: Transaction not found (404)"
                    )
                else:
                    logger.error(
                        f"Attempt {attempt + 1}: HTTP error {e.response.status_code}"
                    )
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(
                    f"Attempt {attempt + 1}: Error verifying transaction: {e}"
                )
                await asyncio.sleep(delay)
        
        logger.error(
            f"Transaction verification failed after {max_attempts} attempts"
        )
        return False

    async def distribute_stars(
            self,
            username: str,
            quantity: int,
            order_id: str
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Полный процесс выдачи Stars:
        1. Подготовка через Fragment API (получаем адрес для оплаты)
        2. Отправка TON транзакции на адрес Fragment
        3. Верификация транзакции
        4. Обновление БД

        Args:
            username: Telegram username получателя
            quantity: Количество Stars
            order_id: ID заказа для записи в БД

        Returns:
            Tuple (success, error_message, transaction_data)
        """

        logger.info(
            f"Starting Stars distribution: "
            f"username={username}, quantity={quantity}, order_id={order_id}"
        )

        try:
            # ==========================================
            # Шаг 1: Подготовка через Fragment API
            # ==========================================
            async with FragmentClient(self.fragment_config) as client:
                # ВАЖНО: Fragment API должен вернуть адрес кошелька для оплаты!
                result: TransactionResult = await client.purchase_stars(
                    username=username,
                    quantity=quantity
                )

                if not result.success:
                    logger.error(f"Fragment API failed: {result.error}")
                    return False, result.error, None

                logger.info(f"Fragment API success!")
                logger.info(f"Amount: {result.amount} TON")
                logger.info(f"Ref ID: {result.ref_id}")
                logger.info(f"Destination address: {result.destination_address}")

                # Проверяем, что есть адрес для оплаты
                if not hasattr(result, 'destination_address') or not result.destination_address:
                    error_msg = "Fragment API не вернул адрес для оплаты!"
                    logger.error(error_msg)
                    return False, error_msg, None

            # ==========================================
            # Шаг 2: Отправка TON транзакции на адрес Fragment
            # ==========================================
            tx_hash, tx_error = await self.send_ton_transaction(
                amount=result.amount,
                comment=result.comment,
                destination_address=result.destination_address  # Адрес из Fragment!
            )

            if tx_hash is None:
                logger.error(f"TON transaction failed: {tx_error}")
                return False, tx_error, None

            logger.info(f"✅ TON transaction sent to Fragment: {tx_hash}")

            # ==========================================
            # Шаг 3: Верификация транзакции
            # ==========================================
            logger.info("Starting transaction verification...")
            is_verified = await self.verify_transaction(tx_hash)

            if not is_verified:
                error_msg = "Transaction verification failed after multiple attempts"
                logger.error(error_msg)
                return False, error_msg, {
                    'tx_hash': tx_hash,
                    'ref_id': result.ref_id,
                    'amount': result.amount,
                    'comment': result.comment,
                    'destination_address': result.destination_address,
                    'verified': False
                }

            logger.info("✅ Transaction verified successfully!")

            # ==========================================
            # Шаг 4: Обновление БД
            # ==========================================
            await self._update_order_in_db(
                order_id=order_id,
                tx_hash=tx_hash,
                ref_id=result.ref_id,
                status='completed'
            )

            logger.info(
                f"✅ Stars distribution completed successfully!"
            )
            logger.info(f"Order ID: {order_id}")
            logger.info(f"TX Hash: {tx_hash}")
            logger.info(f"Ref ID: {result.ref_id}")
            logger.info(f"Amount: {result.amount} TON")

            return True, None, {
                'tx_hash': tx_hash,
                'ref_id': result.ref_id,
                'amount': result.amount,
                'comment': result.comment,
                'destination_address': result.destination_address,
                'tonviewer_url': f'https://tonviewer.com/transaction/{tx_hash}',
                'tonana_url': f'https://tonana.org/transaction/{tx_hash}',
                'verified': True
            }

        except RecipientNotFoundError as e:
            error_msg = f"Username not found: {username}"
            logger.error(error_msg)
            return False, error_msg, None

        except TONTransactionError as e:
            error_msg = str(e)
            logger.error(f"TON transaction error: {error_msg}")
            return False, error_msg, None

        except FragmentAPIError as e:
            error_msg = str(e)
            logger.error(f"Fragment API error: {error_msg}")
            return False, error_msg, None

        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(error_msg)
            import traceback
            logger.error(traceback.format_exc())
            return False, error_msg, None
    
    async def _update_order_in_db(
        self,
        order_id: str,
        tx_hash: str,
        ref_id: str,
        status: str
    ) -> None:
        """
        Обновление информации о заказе в базе данных
        
        Args:
            order_id: ID заказа
            tx_hash: Хеш транзакции
            ref_id: Reference ID из Fragment
            status: Новый статус заказа
        """
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    '''
                    UPDATE orders 
                    SET status = ?, tx_hash = ?, ref_id = ?
                    WHERE id = ?
                    ''',
                    (status, tx_hash, ref_id, order_id)
                )
                await conn.commit()
                logger.debug(f"Order {order_id} updated in database")
                
        except Exception as e:
            logger.error(f"Error updating order in DB: {e}")
            # Не прокидываем исключение дальше, т.к. транзакция уже прошла


# ============================================================================
# ИНТЕГРАЦИЯ В ВАШ STARVELLBOT
# ============================================================================

async def fragment_giver_integration_example():
    """
    Пример интеграции в ваш StarvellBot
    Замените этой логикой вашу функцию fragment_giver
    """
    
    # Конфигурация из вашего config.py
    fragment_config = FragmentConfig(
        hash="YOUR_FRAGMENT_HASH",  # из config
        cookie="YOUR_FRAGMENT_COOKIE",  # из config
        show_sender="0"  # "0" = анонимно
    )
    
    ton_config = TONWalletConfig(
        api_key="YOUR_TON_API_KEY",  # из config
        mnemonic=[
            "word1", "word2", "word3", "word4", "word5", "word6",
            "word7", "word8", "word9", "word10", "word11", "word12",
            "word13", "word14", "word15", "word16", "word17", "word18",
            "word19", "word20", "word21", "word22", "word23", "word24"
        ],  # из config
        is_testnet=False,
        destination_address="UQCFJEP4WZ_mpdo0_kMEmsTgvrMHG7K_tWY16pQhKHwoOtFz"
    )
    
    # Создание дистрибьютора
    distributor = StarsDistributor(
        fragment_config=fragment_config,
        ton_config=ton_config,
        db_path="orders.db"
    )
    
    # Выдача Stars
    success, error, tx_data = await distributor.distribute_stars(
        username="@example_user",
        quantity=50,
        order_id="ORDER_123456"
    )
    
    if success:
        print(f"✅ Success!")
        print(f"   TX Hash: {tx_data['tx_hash']}")
        print(f"   Ref ID: {tx_data['ref_id']}")
        print(f"   Amount: {tx_data['amount']} TON")
        print(f"   Tonviewer: {tx_data['tonviewer_url']}")
    else:
        print(f"❌ Failed: {error}")
        if tx_data:
            print(f"   Partial data: {tx_data}")


# ============================================================================
# КАК ВСТРОИТЬ В ВАШ КОД
# ============================================================================

"""
В вашем StarvellBot классе замените метод fragment_giver на:

class StarvellBot:
    def __init__(self):
        # ... ваш существующий код ...
        
        # Добавьте инициализацию дистрибьютора
        from config import (
            fragment_hash, 
            fragment_cookie, 
            ton_api_key, 
            mnemonic,
            destination_address
        )
        
        fragment_config = FragmentConfig(
            hash=fragment_hash,
            cookie=fragment_cookie,
            show_sender="0"
        )
        
        ton_config = TONWalletConfig(
            api_key=ton_api_key,
            mnemonic=mnemonic,
            destination_address=destination_address
        )
        
        self.stars_distributor = StarsDistributor(
            fragment_config=fragment_config,
            ton_config=ton_config
        )
    
    async def fragment_giver(self, order):
        '''Выдача Stars покупателю'''
        
        success, error, tx_data = await self.stars_distributor.distribute_stars(
            username=order.username,
            quantity=order.quantity,
            order_id=order.order_id
        )
        
        if success:
            # Отправить сообщение пользователю о успехе
            await self.send_chat_message(
                order.chat_id,
                f"✅ Заказ выполнен!\\n"
                f"🔗 Транзакция: {tx_data['tonviewer_url']}\\n"
                f"⭐️ Отправлено Stars: {order.quantity}\\n"
                f"🔑 Ref ID: {tx_data['ref_id']}"
            )
            
            # Пометить заказ как выполненный
            order.mark_completed()
            
            return True
        else:
            # Обработка ошибки
            logger.error(f"Failed to distribute stars for order {order.order_id}: {error}")
            
            # Если нужно вернуть деньги
            if 'insufficient funds' in error.lower():
                await self.refund_order(order.order_id)
            
            # Отправить сообщение об ошибке
            await self.send_chat_message(
                order.chat_id,
                f"❌ Произошла ошибка при выдаче Stars.\\n"
                f"Причина: {error}\\n"
                f"Пожалуйста, свяжитесь с поддержкой."
            )
            
            return False
"""


if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запуск примера
    asyncio.run(fragment_giver_integration_example())
