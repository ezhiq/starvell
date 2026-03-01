# -*- coding: utf-8 -*-
"""
Модуль для работы с Fragment API (Telegram Stars)
Версия: 2.1 (с адресом кошелька для оплаты)
"""

import asyncio
import base64
import logging
import re
import json
from typing import Optional, Tuple, Dict
from dataclasses import dataclass

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

# Настройка логирования
logger = logging.getLogger("FragmentAPI")


@dataclass
class FragmentConfig:
    """Конфигурация для работы с Fragment API"""
    hash: str
    cookie: str
    url: str = "https://fragment.com/api"
    show_sender: str = "0"  # "0" = анонимно, "1" = с отправителем

    def validate(self) -> bool:
        """Валидация конфигурации"""
        if not self.hash or len(self.hash) < 10:
            logger.error("Invalid hash")
            return False
        if not self.cookie or len(self.cookie) < 10:
            logger.error("Invalid cookie")
            return False
        return True


@dataclass
class TransactionResult:
    """Результат транзакции Fragment"""
    success: bool
    amount: Optional[float] = None
    ref_id: Optional[str] = None
    comment: Optional[str] = None
    destination_address: Optional[str] = None  # Адрес кошелька для оплаты
    error: Optional[str] = None


class FragmentAPIError(Exception):
    """Базовое исключение для ошибок Fragment API"""
    pass


class RecipientNotFoundError(FragmentAPIError):
    """Username не найден в Telegram"""
    pass


class InsufficientFundsError(FragmentAPIError):
    """Недостаточно средств"""
    pass


class FragmentClient:
    """Клиент для работы с Fragment API"""

    # Константы
    MAX_USERNAME_LENGTH = 32
    MIN_USERNAME_LENGTH = 5
    USERNAME_PATTERN = re.compile(r'^[a-zA-Z]\w{4,31}$')

    def __init__(self, config: FragmentConfig):
        """
        Инициализация клиента

        Args:
            config: Конфигурация Fragment API
        """
        if not config.validate():
            raise ValueError("Invalid FragmentConfig")

        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None

        # Заголовки для запросов
        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "ru,en;q=0.9",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": self.config.cookie,
            "Host": "fragment.com",
            "Origin": "https://fragment.com",
            "Referer": "https://fragment.com/stars/buy",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }

    async def __aenter__(self):
        """Context manager entry"""
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=timeout
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if self.session:
            await self.session.close()

    @staticmethod
    def validate_username(username: str) -> str:
        """
        Валидация и нормализация username

        Args:
            username: Telegram username (с @ или без)

        Returns:
            Очищенный username без @

        Raises:
            ValueError: Если username невалиден
        """
        # Убираем @ если есть
        clean_username = username.lstrip('@').strip()

        # Проверяем формат
        if not FragmentClient.USERNAME_PATTERN.match(clean_username):
            raise ValueError(
                f"Invalid username format: {username}. "
                f"Username must be {FragmentClient.MIN_USERNAME_LENGTH}-"
                f"{FragmentClient.MAX_USERNAME_LENGTH} characters, "
                "start with a letter, and contain only letters, digits, and underscores."
            )

        return clean_username

    @staticmethod
    def decode_payload(payload: str) -> str:
        """
        Декодирование payload для получения ref_id

        Args:
            payload: Base64 encoded payload

        Returns:
            Ref ID
        """
        # Добавляем padding если нужно
        while len(payload) % 4 != 0:
            payload += "="

        try:
            decoded_bytes = base64.b64decode(payload)
            decoded_str = decoded_bytes.decode('latin1')

            # Извлекаем ref_id
            if "Ref#" in decoded_str:
                ref_id = decoded_str.split("Ref#")[-1].strip()
                return ref_id
            else:
                logger.warning(f"No Ref# found in decoded payload: {decoded_str}")
                return decoded_str
        except Exception as e:
            logger.error(f"Error decoding payload: {e}")
            raise FragmentAPIError(f"Failed to decode payload: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def _post_request(self, payload: dict) -> dict:
        """
        Выполнение POST запроса к Fragment API с retry логикой

        Args:
            payload: Данные для отправки

        Returns:
            JSON ответ от сервера

        Raises:
            FragmentAPIError: При ошибке запроса
        """
        if not self.session:
            raise FragmentAPIError("Session not initialized. Use 'async with' context manager.")

        url = f"{self.config.url}?hash={self.config.hash}"
        logger.info(f"POST URL: {url}")

        try:
            async with self.session.post(url, data=payload) as response:
                response.raise_for_status()
                logger.info(f"POST RESPONSE: {response.status}")
                response_text = await response.text()
                logger.info(f"POST RESPONSE TEXT: {response_text}")
                if not response_text:
                    raise FragmentAPIError("Empty response from Fragment API")

                try:
                    data = await response.json()
                    logger.warning(f'Response from Fragment API - {data}')
                    return data
                except aiohttp.ContentTypeError:
                    logger.error(f"Invalid JSON response: {response_text}")
                    raise FragmentAPIError(f"Invalid JSON response: {response_text}")

        except aiohttp.ClientError as e:
            logger.error(f"HTTP request failed: {e}")
            raise FragmentAPIError(f"HTTP request failed: {e}")

    async def search_recipient(self, username: str, quantity: int = 50) -> dict:
        """
        Поиск получателя Stars по username

        Args:
            username: Telegram username (без @)
            quantity: Количество Stars для проверки (default: 50)

        Returns:
            Информация о получателе

        Raises:
            RecipientNotFoundError: Если username не найден
            FragmentAPIError: При других ошибках
        """
        payload = {
            "query": username,
            "quantity": quantity,
            "method": "searchStarsRecipient"
        }

        logger.debug(f"Searching recipient: {username}")

        try:
            data = await self._post_request(payload)

            if data.get('ok') is True:
                recipient = data.get('found', {}).get('recipient')
                if not recipient:
                    raise RecipientNotFoundError(f"No recipient found for username: {username}")

                logger.info(f"Found recipient: {recipient} for username: {username}")
                return data.get('found', {})
            else:
                error = data.get('error', 'Unknown error')
                if 'No Telegram users found' in error:
                    raise RecipientNotFoundError(f"Username not found: {username}")
                else:
                    raise FragmentAPIError(f"Search failed: {error}")

        except (RecipientNotFoundError, FragmentAPIError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in search_recipient: {e}")
            raise FragmentAPIError(f"Unexpected error: {e}")

    async def init_purchase(self, recipient: str, quantity: int) -> Tuple[str, float]:
        """
        Инициализация покупки Stars

        Args:
            recipient: ID получателя из search_recipient
            quantity: Количество Stars

        Returns:
            Tuple (req_id, amount_ton)

        Raises:
            FragmentAPIError: При ошибке инициализации
        """
        payload = {
            "recipient": recipient,
            "quantity": quantity,
            "method": "initBuyStarsRequest"
        }

        logger.debug(f"Initializing purchase: recipient={recipient}, quantity={quantity}")

        try:
            data = await self._post_request(payload)

            req_id = data.get('req_id')
            amount = data.get('amount', 0)

            try:
                amount_ton = float(amount)
            except (TypeError, ValueError):
                raise FragmentAPIError(f"Invalid amount in response: {amount}")

            if not req_id or amount_ton == 0:
                raise FragmentAPIError(f"Invalid response: req_id={req_id}, amount={amount_ton}")

            logger.info(f"Purchase initialized: req_id={req_id}, amount={amount_ton} TON")
            return req_id, amount_ton

        except FragmentAPIError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in init_purchase: {e}")
            raise FragmentAPIError(f"Unexpected error: {e}")

    async def get_payment_link(self, req_id: str) -> Tuple[str, str, float, str]:
        """
        Получение данных для оплаты, включая адрес кошелька

        Args:
            req_id: Request ID из init_purchase

        Returns:
            Tuple (payload, ref_id, amount, destination_address)

        Raises:
            FragmentAPIError: При ошибке получения ссылки
        """
        # Hardcoded wallet state init (из оригинального кода)
        wallet_state_init = (
            "te6ccgECFgEAAwQAAgE0AQIBFP8A9KQT9LzyyAsDAFEAAAAAKamjF5hE%2BFriD8Ufe710n9USsAZBzBxLOlXNYCYDiPBRvJZXQAIBIAQFAgFIBgcE%2BPKDCNcYINMf0x%2FT%2F%2FQE0VFDuvKhUVG68qIF%2BQFUEGT5EPKj%2BAAkpMjLH1JAyx9SMMv%2FUhD0AMntVPgPAdMHIcAAn2xRkyDXSpbTB9QC%2BwDoMOAhwAHjACHAAuMAAcADkTDjDQOkyMsfEssfy%2F8SExQVAubQAdDTAyFxsJJfBOAi10nBIJJfBOAC0x8hghBwbHVnvSKCEGRzdHK9sJJfBeAD%2BkAwIPpEAcjKB8v%2FydDtRNCBAUDXIfQEMFyBAQj0Cm%2BhMbOSXwfgBdM%2FyCWCEHBsdWe6kjgw4w0DghBkc3RyupJJfBuMNCAkCASAKCwB4AfoA9AQw%2BCdvIjBQCqEhvvLgUIIQcGx1Z4MesXCAGFAEywUmzxZY%2BgIZ9ADLaRfLH1Jgyz8gyYBA%2BwAGAIpQBIEBCPRZMO1E0IEBQNcgyAHPFvQAye1UAXKwjiOCEGRzdHKDHrFwgBhQBcsFUAPPFiP6AhPLassfyz%2FJgED7AJJfA%2BICASAMDQBZvSQrb2omhAgKBrkPoCGEcNQICEekk30pkQzmkD6f%2BYN4EoAbeBAUiYcVnzGEAgFYDg8AEbjJftRNDXCx%2BAA9sp37UTQgQFA1yH0BDACyMoHy%2F%2FJ0AGBAQj0Cm%2BhMYAIBIBARABmtznaiaEAga5Drhf%2FAABmvHfaiaEAQa5DrhY%2FAAG7SB%2FoA1NQi%2BQAFyMoHFcv%2FydB3dIAYyMsFywIizxZQBfoCFMtrEszMyXP7AMhAFIEBCPRR8qcCAHCBAQjXGPoA0z%2FIVCBHgQEI9FHyp4IQbm90ZXB0gBjIywXLAlAGzxZQBPoCE8tqEszMyXP7AMhAFIEBCPRR8qcCAHCBAQjXGPoA0z%2FIVCBHgQEI9FHyp4IQZHN0cnB0gBjIywXLAlAFzxZQA%2FoCE8tqyx8Syz%2FJc%2FsAAAr0AMntVA%3D%3D"
        )

        device_info = (
            '{"platform":"android","appName":"Tonkeeper","appVersion":"5.0.18",'
            '"maxProtocolVersion":2,"features":["SendTransaction",'
            '{"name":"SendTransaction","maxMessages":4}]}'
        )

        wallet_account = (
                '{"address":"0:adc5b49f73e4796ecc3c290ad0d89f87fa552b515d173d5295469df9612c24a",'
                '"chain":"-239","walletStateInit":"' + wallet_state_init + '"}'
        )

        payload = {
            "account": wallet_account,
            "device": device_info,
            "transaction": "1",
            "id": req_id,
            "show_sender": self.config.show_sender,
            "method": "getBuyStarsLink"
        }

        logger.debug(f"Getting payment link for req_id: {req_id}")

        try:
            data = await self._post_request(payload)

            if data.get('ok') is not True:
                error = data.get('error', 'Unknown error')
                raise FragmentAPIError(f"Failed to get payment link: {error}")

            # Извлекаем transaction messages
            transaction_data = data.get('transaction', {})
            transaction_messages = transaction_data.get('messages', [])

            if not transaction_messages:
                raise FragmentAPIError(f"No transaction messages in response: {data}")

            # Получаем payload из первого сообщения
            first_message = transaction_messages[0]
            message_payload = first_message.get('payload')

            if not message_payload:
                raise FragmentAPIError(f"No payload in transaction message: {data}")

            # Декодируем payload для получения ref_id
            ref_id = self.decode_payload(message_payload)

            # Получаем amount из первого сообщения
            amount_str = first_message.get('amount', '0')
            try:
                amount = float(amount_str) / 1_000_000_000  # Конвертируем из nanoTON в TON
            except (TypeError, ValueError):
                raise FragmentAPIError(f"Invalid amount in message: {amount_str}")

            # Получаем адрес назначения из первого сообщения
            destination_address = first_message.get('address')
            if not destination_address:
                logger.warning(f"No destination address in message: {first_message}")
                # Пытаемся найти адрес в других местах
                destination_address = transaction_data.get('destination_address') or \
                                      transaction_data.get('address')

            if not destination_address:
                raise FragmentAPIError(f"No destination address found in response: {data}")

            # Преобразуем адрес в нужный формат (если нужно)
            if destination_address.startswith('0:'):
                # Это raw адрес, нужно конвертировать в user-friendly
                from tonutils.utils import convert_raw_to_userfriendly
                try:
                    destination_address = convert_raw_to_userfriendly(destination_address, is_testnet=False)
                except:
                    logger.warning(f"Could not convert address {destination_address}, using as-is")

            logger.info(f"Payment link data:")
            logger.info(f"  ref_id={ref_id}")
            logger.info(f"  amount={amount} TON")
            logger.info(f"  destination_address={destination_address}")

            return message_payload, ref_id, amount, destination_address

        except FragmentAPIError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in get_payment_link: {e}")
            raise FragmentAPIError(f"Unexpected error: {e}")

    async def purchase_stars(
            self,
            username: str,
            quantity: int
    ) -> TransactionResult:
        """
        Полный процесс покупки Stars (без отправки TON)

        Args:
            username: Telegram username получателя
            quantity: Количество Stars

        Returns:
            TransactionResult с данными для отправки TON, включая адрес назначения
        """
        try:
            # 1. Валидация username
            clean_username = self.validate_username(username)
            logger.info(f"Starting purchase: username={clean_username}, quantity={quantity}")

            # 2. Поиск получателя
            recipient_data = await self.search_recipient(clean_username, quantity)
            recipient_id = recipient_data.get('recipient')

            # 3. Инициализация покупки
            req_id, amount_ton = await self.init_purchase(recipient_id, quantity)

            # 4. Получение данных для оплаты (включая адрес)
            payload, ref_id, confirmed_amount, destination_address = await self.get_payment_link(req_id)

            # 5. Формируем комментарий для транзакции
            comment = f"{quantity} Telegram Stars \n\nRef#{ref_id}"

            logger.info(
                f"Purchase prepared successfully: "
                f"username={clean_username}, quantity={quantity}, "
                f"amount={confirmed_amount} TON, ref_id={ref_id}, "
                f"destination={destination_address}"
            )

            return TransactionResult(
                success=True,
                amount=confirmed_amount,
                ref_id=ref_id,
                comment=comment,
                destination_address=destination_address,
                error=None
            )

        except RecipientNotFoundError as e:
            logger.warning(f"Recipient not found: {e}")
            return TransactionResult(
                success=False,
                error=f"Username not found: {username}"
            )
        except FragmentAPIError as e:
            logger.error(f"Fragment API error: {e}")
            return TransactionResult(
                success=False,
                error=str(e)
            )
        except Exception as e:
            logger.error(f"Unexpected error in purchase_stars: {e}")
            return TransactionResult(
                success=False,
                error=f"Unexpected error: {e}"
            )


# ============================================================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================================================

async def example_usage():
    """Пример использования FragmentClient"""

    # Настройка конфига
    config = FragmentConfig(
        hash="YOUR_FRAGMENT_HASH",
        cookie="YOUR_FRAGMENT_COOKIE",
        show_sender="0"  # Анонимная отправка
    )

    # Использование через context manager
    async with FragmentClient(config) as client:
        # Покупка Stars
        result = await client.purchase_stars(
            username="@example_user",
            quantity=50
        )

        if result.success:
            print(f"✅ Purchase prepared:")
            print(f"   Amount: {result.amount} TON")
            print(f"   Ref ID: {result.ref_id}")
            print(f"   Destination Address: {result.destination_address}")
            print(f"   Comment: {result.comment}")
        else:
            print(f"❌ Purchase failed: {result.error}")


if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Запуск примера
    asyncio.run(example_usage())