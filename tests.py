import asyncio
import websockets
import json
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("StarvellWebSocket")


class StarvellWebSocket:
    def __init__(self, session_cookie: str):
        self.session_cookie = session_cookie
        self.websocket = None
        self.is_connected = False

    def get_websocket_url(self) -> str:
        """Генерация URL для WebSocket соединения"""
        # Параметры из вашего лога:
        # EIO=4 - Engine.IO версия 4
        # transport=websocket - используем WebSocket транспорт
        return f"wss://starvell.com/socket.io/?EIO=4&transport=websocket"

    async def connect(self):
        """Подключение к WebSocket"""
        try:
            url = self.get_websocket_url()
            logger.info(f"Подключаюсь к WebSocket: {url}")

            # Дополнительные заголовки для аутентификации
            headers = {
                "Cookie": f"session={self.session_cookie}",
                "Origin": "https://starvell.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            # Подключаемся через библиотеку websockets
            self.websocket = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=25,  # Пинг каждые 25 секунд
                ping_timeout=10,  # Таймаут пинга 10 секунд
                close_timeout=5
            )

            self.is_connected = True
            logger.info("✅ WebSocket подключен!")

            # Socket.IO требует начального handshake
            await self.send_handshake()

            return True

        except Exception as e:
            logger.error(f"❌ Ошибка подключения WebSocket: {e}")
            return False

    async def send_handshake(self):
        """Отправка handshake для Socket.IO"""
        # Socket.IO protocol: '0' - открытие соединения
        await self.websocket.send('0')
        logger.debug("Отправлен handshake '0'")

        # Получаем ответ с sid
        response = await self.websocket.recv()
        logger.debug(f"Ответ на handshake: {response}")

        # Ответ должен быть типа: '0{"sid":"abc123","upgrades":[],"pingInterval":25000,"pingTimeout":5000}'
        if response.startswith('0'):
            # Парсим SID
            data = json.loads(response[1:])
            self.sid = data.get('sid')
            logger.info(f"SID получен: {self.sid}")

            # Отправляем ping
            await self.websocket.send('2')
            logger.debug("Отправлен ping '2'")

            # Ждем pong
            pong = await self.websocket.recv()
            logger.debug(f"Получен pong: {pong}")

    async def authenticate(self):
        """Аутентификация в WebSocket"""
        auth_data = {
            "type": "auth",
            "token": self.session_cookie,
            "session": self.session_cookie
        }

        # Socket.IO формат: '42["auth", {...}]'
        message = f'42{json.dumps(["auth", auth_data])}'
        await self.websocket.send(message)
        logger.debug("Отправлена аутентификация")

        # Ждем подтверждения
        response = await self.websocket.recv()
        logger.debug(f"Ответ на аутентификацию: {response}")

    async def listen(self):
        """Прослушивание входящих сообщений"""
        if not self.is_connected or not self.websocket:
            logger.error("WebSocket не подключен")
            return

        logger.info("👂 Начинаю слушать WebSocket...")

        try:
            while self.is_connected:
                try:
                    # Получаем сообщение с таймаутом
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=30.0
                    )

                    await self.handle_message(message)

                except asyncio.TimeoutError:
                    # Таймаут - отправляем ping
                    logger.debug("Таймаут, отправляю ping...")
                    await self.websocket.send('2')

                except websockets.exceptions.ConnectionClosed:
                    logger.error("Соединение закрыто")
                    self.is_connected = False
                    break

        except Exception as e:
            logger.error(f"Ошибка в listen: {e}")
            self.is_connected = False

    async def handle_message(self, message: str):
        """Обработка входящих сообщений"""
        try:
            logger.debug(f"Получено сырое сообщение: {message}")

            # Socket.IO протокол:
            # '0' - открытие соединения
            # '1' - закрытие соединения
            # '2' - ping
            # '3' - pong
            # '4' - сообщение

            if message == '0':
                logger.debug("Получен запрос на открытие соединения")
            elif message == '1':
                logger.debug("Получен запрос на закрытие соединения")
                self.is_connected = False
            elif message == '2':
                logger.debug("Получен ping, отправляю pong...")
                await self.websocket.send('3')  # pong
            elif message == '3':
                logger.debug("Получен pong")
            elif message.startswith('4'):
                # Это данные сообщения
                await self.handle_data_message(message)
            else:
                logger.warning(f"Неизвестный тип сообщения: {message}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def handle_data_message(self, message: str):
        """Обработка data сообщений Socket.IO"""
        # Форматы:
        # '42["event_name", data]' - событие
        # '43[ack_id, data]' - подтверждение

        if message.startswith('42'):
            # Событие
            try:
                # Убираем '42' и парсим JSON
                data_str = message[2:]
                data = json.loads(data_str)

                if isinstance(data, list) and len(data) >= 2:
                    event_name = data[0]
                    event_data = data[1]

                    logger.info(f"📡 Событие: {event_name}")
                    logger.debug(f"Данные: {event_data}")

                    # Обработка конкретных событий
                    await self.process_event(event_name, event_data)

            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON: {e}, message: {message}")
            except Exception as e:
                logger.error(f"Ошибка обработки события: {e}")

    async def process_event(self, event_name: str, data: dict):
        """Обработка конкретных событий"""
        handlers = {
            "new_message": self.handle_new_message,
            "message": self.handle_new_message,
            "order_update": self.handle_order_update,
            "new_order": self.handle_new_order,
            "notification": self.handle_notification,
            "chat_update": self.handle_chat_update,
            "balance_update": self.handle_balance_update,
            "auth_success": self.handle_auth_success,
            "auth_error": self.handle_auth_error,
        }

        handler = handlers.get(event_name)
        if handler:
            await handler(data)
        else:
            logger.debug(f"Нет обработчика для события: {event_name}")

    async def handle_new_message(self, data: dict):
        """Обработка нового сообщения"""
        logger.info(f"📨 Новое сообщение: {data}")
        # Здесь ваша логика обработки сообщений

    async def handle_order_update(self, data: dict):
        """Обработка обновления заказа"""
        logger.info(f"🛒 Обновление заказа: {data}")

    async def handle_new_order(self, data: dict):
        """Обработка нового заказа"""
        logger.info(f"🎉 Новый заказ: {data}")

    async def send_event(self, event_name: str, data: dict):
        """Отправка события через WebSocket"""
        if not self.is_connected or not self.websocket:
            logger.error("Не могу отправить: WebSocket не подключен")
            return False

        try:
            # Формат Socket.IO: '42["event_name", data]'
            message = f'42{json.dumps([event_name, data])}'
            await self.websocket.send(message)
            logger.debug(f"Отправлено событие: {event_name}")
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки события: {e}")
            return False

    async def close(self):
        """Закрытие соединения"""
        if self.websocket:
            await self.websocket.close()
        self.is_connected = False
        logger.info("WebSocket соединение закрыто")


async def main():
    # Ваша сессия из cookies
    SESSION_COOKIE = "13a7f0b3-3136-473c-bf65-08e592fc9306"

    ws_client = StarvellWebSocket(SESSION_COOKIE)

    try:
        # Подключаемся
        if await ws_client.connect():
            # Аутентифицируемся
            await ws_client.authenticate()

            # Слушаем сообщения
            await ws_client.listen()

    except KeyboardInterrupt:
        logger.info("Останавливаюсь...")
    except Exception as e:
        logger.error(f"Ошибка в main: {e}")
    finally:
        await ws_client.close()


if __name__ == "__main__":
    asyncio.run(main())