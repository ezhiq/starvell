import traceback
from datetime import timezone

import aiohttp
import asyncio
import json
import logging
import re

import aiosqlite
from aiohttp import ContentTypeError
from config import universal, my_id, refund_msg, hello
from fragment_api import FragmentConfig
from stars_distributor import StarsDistributor, TONWalletConfig
from telegram_manager import TelegramManager

from bs4 import BeautifulSoup
import re

class Order:
    def __init__(self, order_id: str, amount: int, quantity: int, user_id: int, username: str, chat_id: str = None):
        self.order_id = order_id
        self.amount = amount
        self.quantity = quantity
        self.user_id = user_id
        self.username = username
        self.chat_id = chat_id
        self.status = "оплачен"  # CREATED, PROCESSING, COMPLETED, REFUNDED
        self.created_at = None
        self.completed_at = None
        self.refunded_at = None

    def __repr__(self):
        return f"Order(id={self.order_id}, amount={self.amount}, user={self.username}, status={self.status})"

    def to_dict(self):
        """Преобразовать в словарь для JSON или БД"""
        return {
            "order_id": self.order_id,
            "amount": self.amount,
            "quantity": self.quantity,
            "user_id": self.user_id,
            "username": self.username,
            "chat_id": self.chat_id,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "refunded_at": self.refunded_at
        }

    def mark_completed(self):
        """Пометить заказ как выполненный"""
        self.status = "закрыт"
        from datetime import datetime
        self.completed_at = datetime.now().isoformat()

    def mark_refunded(self):
        """Пометить заказ как возвращенный"""
        self.status = "возврат"
        from datetime import datetime
        self.refunded_at = datetime.now().isoformat()

# Настройка цветного логгера
class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m',
    }
    RESET = '\033[0m'

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        record.msg = f"{log_color}{record.msg}{self.RESET}"
        return super().format(record)


logger = logging.getLogger("StarvellBot")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter('%(levelname)s | %(message)s'))
logger.addHandler(handler)


class StarvellBot:
    def __init__(self, tg_manager, admin):

        from config import cookie_session

        self.admin = admin
        self.ws_url = "wss://starvell.com/socket.io/?EIO=4&transport=websocket"
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://starvell.com",
            "Cookie": (
                "starvell.time_zone=Europe%2FMoscow; "
                "starvell.theme=dark; "
                f"session={cookie_session}"
            )
        }

        self.telegram_manager = tg_manager
        self.bot = self.telegram_manager.bot
        # Все нужные namespace'ы
        self.namespaces = [
            "/chats",
            "/user-notifications",
            "/orders",
            "/user-presence",
            "/online"
        ]

        self.USER_RE = re.compile(
            r'''(?:
                (?<=@)          # 1. после @
                |
                (?<=t\.me/)     # 2. после t.me/
                |
                (?<=/)          # 3. после любого слэша (на всякий)
                )
                ([A-Za-z]\w{3,30})   # сам username 5-32 символа
            ''', re.VERBOSE)

        self.states = {}
        self.users = {}
        self.orders = {}

        self._init_fragment_distributor()

        self.ws_connected = asyncio.Event()

        self.event_pattern = re.compile(r'^42(/[\w\-]+)?,?\[(.+)\]$')\

        self.categories = []
        self.game_id = None


    async def bumping(self, game_id, categories):

        try:
            url = "https://starvell.com/api/offers/bump"
            payload = {"gameId": game_id, "categoryIds": [categories]}

            async with aiohttp.ClientSession() as session:
                async with session.post(headers=self.headers, url=url, json=payload) as response:

                    if response:
                        if 200 <= response.status < 300:
                            logger.info('Успешно подняли предложения')
                            return True
                        else:
                            logger.warning('Не удалось поднять предложения - скорее всего они уже подняты')

        except Exception as e:
            traceback.print_exc()
            logger.error('Не удалось поднять предложения')


    async def bumper(self):

        while True:
            game_id = 14
            categories = 182
            await self.bumping(game_id, categories)
            await asyncio.sleep(300)

    # async def update_order_price(self, order, price):
    #
    #     id = order["id"]
    #     url = f'https://starvell.com/api/offers/{id}/partial-update'
    #     payload = {
    #         "price": price
    #     }
    #
    #     async with aiohttp.ClientSession() as session:
    #         async with session.post(url, headers=self.headers, json=payload) as response:
    #             print(response.status)
    #             if 200 <= response.status < 300:
    #                 return True
    #             else:
    #                 logger.error('ошибочка')
    #                 traceback.print_exc()
    #                 return None

    async def dumping(self, queue):
        """Автопонижение цен на наши предложения"""
        from config import min_star_rate, is_online

        offers = queue['pageProps']['offers']
        for my_offer in offers:

            # Пропускаем неактивные
            if not my_offer.get("isActive"):
                continue

            my_price = float(my_offer.get('price'))
            my_offer_id = my_offer.get("id")

            # Получаем данные для поиска конкурентов
            game_id = my_offer.get("game_id")
            category_id = my_offer.get("categoryId")
            sub_category_id = my_offer.get("subCategoryId")

            category_obj = my_offer.get('subCategory')
            sub_category_name = category_obj.get("name", "")

            visibility = my_offer.get('visibility')

            if visibility == 'PUBLIC':
                not_raised = False
            else:
                not_raised = True

            # Извлекаем количество звёзд из названия подкатегории
            stars_match = re.search(r'(\d+)', sub_category_name)
            if not stars_match:
                logger.warning(f"⚠️ Не удалось извлечь количество звёзд из '{sub_category_name}'")
                continue

            stars_count = int(stars_match.group(1))

            if stars_count == 43:
                stars_count = 50
            elif stars_count == 21:
                stars_count = 25
            elif stars_count == 13:
                stars_count = 15

            my_course = my_price / stars_count

            if my_course < min_star_rate:
                await self.update_order_price(my_offer, round(min_star_rate * stars_count * 1.01, 1))
                await asyncio.sleep(1)
                continue

            logger.info(f"📊 Обрабатываем предложение {my_offer_id}: {stars_count} звёзд по {my_price}₽")

            # Получаем конкурентов (до 10 предложений)
            competitors = await self.get_him_offers(game_id, category_id, sub_category_id)

            if not competitors:
                logger.warning(f"⚠️ Не удалось получить конкурентов для {my_offer_id}")
                continue

            raw_competitors = competitors[:30]

            if is_online:
                raw_competitors = [c for c in competitors if c.get("user", {}).get("isOnline") == True]

            competitors = raw_competitors
            competitors = [c for c in competitors if c.get("user", {}).get("id") != my_id]

            if not competitors:
                logger.info(f"✅ Наше предложение {my_offer_id} единственное в категории")
                continue

            # === ЛОГИКА ПОНИЖЕНИЯ ===

            our_position = None
            cheaper_competitor = None
            first_competitor = raw_competitors[0] if competitors else None
            second_competitor = raw_competitors[1] if len(competitors) > 1 else None

            # Определяем нашу позицию

            if not not_raised:
                for idx, competitor in enumerate(raw_competitors):
                    if competitor.get("user", {}).get("id") == my_id:
                        our_position = idx

            else:
                minimum = 10e9
                our_position = 0
                for idx, comp in enumerate(competitors):
                    comp_price = float(comp.get('price', 0))

                    if abs(comp_price - my_price) < minimum:  # Это мы
                        minimum = abs(comp_price - my_price)
                        our_position = idx

            cheaper_competitors = []
            # Ищем первого конкурента дешевле нас
            for comp in competitors:
                if comp.get("id") == my_id:
                    break

                comp_price = float(comp.get('price', 0))

                if comp_price < my_price:
                    cheaper_competitors.append(comp)

            # === СЛУЧАЙ 1: Мы первые ===
            if our_position == 0 and second_competitor:

                if not_raised:
                    second_price = float(first_competitor.get('price', 0))
                    convert_price = second_price
                    him_price = round(convert_price, 1)

                else:
                    second_price = float(second_competitor.get('price', 0))
                    convert_price = second_price
                    him_price = round(convert_price, 1)

            # === СЛУЧАЙ 2: Есть конкурент дешевле ===
            elif cheaper_competitors:
                fixed = False
                for comp in cheaper_competitors:
                    comp_price = float(comp.get('price', 0))
                    convert_price = comp_price
                    optimal_price = round(convert_price - 0.1, 1)
                    seller = comp.get("id")

                    # Проверяем курс
                    star_rate = convert_price / stars_count

                    if star_rate < min_star_rate:
                        logger.warning(f"⚠️ Не перебиваем {seller}: курс {star_rate:.2f}₽ < минимум {min_star_rate}₽")
                        continue

                    logger.info(f"🎯 Перебиваем конкурента: {my_price}₽ → {optimal_price}₽ (курс {star_rate:.2f}₽)")
                    await self.update_order_price(my_offer, optimal_price)
                    fixed = True
                    await asyncio.sleep(1)
                    break

                if not fixed:
                    try:
                        him_price = round(float(raw_competitors[our_position + 1].get("price", 0)), 1)
                    except:
                        him_price = my_price

                else:
                    continue

            # Если наша цена не оптимальна - обновляем
            if abs(round(my_price - him_price, 1)) != 0.1:
                logger.info(f"💰 Оптимизируем первое место: {my_price}₽ → {him_price - 0.1}₽")
                await self.update_order_price(my_offer, round(him_price - 0.1, 1))
                await asyncio.sleep(1)  # Защита от rate limit

    async def dumper(self):
        """Периодическая проверка и обновление цен"""

        # Ждём подключения WebSocket
        await self.ws_connected.wait()
        logger.info("✅ WebSocket готов, запускаем автопонижение цен")

        while True:
            try:
                logger.info("🔄 Проверяем цены...")

                # Получаем наши предложения
                data = await self.get_my_offers()

                if not data:
                    logger.warning("⚠️ Не удалось получить наши предложения")
                    await asyncio.sleep(300)
                    continue

                # Парсим предложения
                queue = data

                logger.info(f"📦 Найдено {len(queue)} наших предложений")

                # Обрабатываем
                await self.dumping(queue)

                logger.info("✅ Проверка цен завершена")

            except Exception as e:
                logger.error(f"❌ Ошибка в dumper: {e}")
                traceback.print_exc()

            # Проверяем каждые 5 минут
            await asyncio.sleep(30)

    async def update_order_price(self, order, price):
        """Обновление цены предложения"""

        order_id = order["id"]
        url = f'https://starvell.com/api/offers/{order_id}/partial-update'
        payload = {"price": str(price)}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload) as response:

                    if 200 <= response.status < 300:
                        logger.info(f"✅ Цена обновлена: {price}₽ (ID: {order_id})")
                        return True
                    else:
                        resp_text = await response.text()
                        logger.error(f"❌ Ошибка обновления цены {order_id}: HTTP {response.status}")
                        logger.error(f"Response: {resp_text}")
                        return False

        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении цены {order_id}: {e}")
            traceback.print_exc()
            return False

    async def update_order_status(self, id, status):
        """Обновление цены предложения"""

        url = f'https://starvell.com/api/offers/{id}/partial-update'
        payload = {"isActive": True if status else False}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload) as response:

                    if 200 <= response.status < 300:
                        logger.info(f"✅ Статус обновлен: {status} (ID: {id})")
                        return True
                    else:
                        resp_text = await response.text()
                        logger.error(f"❌ Ошибка обновления статуса {id}: HTTP {response.status}")
                        logger.error(f"Response: {resp_text}")
                        return False

        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении цены {id}: {e}")
            traceback.print_exc()
            return False

    async def get_my_offers(self):

        for attempt in range(2):

            id = await self.get_build_id()
            url = f'https://starvell.com/_next/data/{id}/telegram/stars/trade.json?game=telegram&game=stars'

            async with aiohttp.ClientSession() as session:
                async with session.get(headers=self.headers, url=url) as response:

                    try:
                        json_data = await response.json()
                    except Exception as e:
                        logger.info(f"{e}")
                    if 200 <= response.status < 300:

                        logger.info('Успешно парсили ордеры')
                        json_data = await response.json()
                        return json_data
                    else:
                        logger.warning(f'Не удалось парсить ордеры - обновляем build_id и пробуем снова - atttempt {attempt}')
                        continue


    def _init_fragment_distributor(self):
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
            fragment_config, ton_config
        )

    async def fragment_giver(self, order):
        '''Выдача Stars (НОВАЯ РЕАЛИЗАЦИЯ)'''

        success, error, tx_data = await self.stars_distributor.distribute_stars(
            username=order.username,
            quantity=order.amount,
            order_id=order.order_id
        )

        if success:
            # Успех
            await self.send_chat_message(
                order.chat_id,
                f"✅ Заказ выполнен!\n"
                f"🔗 {tx_data['tonviewer_url']}\n"
                f"⭐️ Stars: {order.amount}\n"
                f"🔑 Ref ID: {tx_data['ref_id']}"
            )
            order.mark_completed()
            return True
        else:
            # Ошибка - вернуть деньги
            await self.send_chat_message(
                order.chat_id,
                f"❌ Ошибка, возвращаем деньги на баланс StarVell"
            )
            await self.refund_order(order.order_id)
            return False

    async def get_updates(self):
        while True:
            try:
                async with aiohttp.ClientSession(headers=self.headers) as session:
                    async with session.ws_connect(self.ws_url, heartbeat=25) as ws:
                        logger.info("🟢 WebSocket connected")
                        self.ws_connected.set()

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self.handle(ws, msg.data)

            except Exception as e:
                logger.error(f"🔴 WebSocket disconnected: {e}")
                self.ws_connected.clear()
                await asyncio.sleep(5)  # Ждем перед переподключением
                logger.info("🔄 Reconnecting...")

    async def handle(self, ws, data: str):
        logger.info(f"⬅️  {data}")

        # Engine.IO handshake
        if data.startswith("0"):
            for ns in self.namespaces:
                await ws.send_str(f"40{ns}")
                logger.warning(f"➡️  Connecting to namespace: {ns}")
            return

        # ping
        if data == "2":
            await ws.send_str("3")
            return

        # Socket.IO событие - парсинг через регулярку
        if data.startswith("42"):
            match = self.event_pattern.match(data)

            if match:
                namespace = match.group(1) or "/"  # Если нет namespace, то "/"
                payload_str = f"[{match.group(2)}]"

                try:
                    payload = json.loads(payload_str)
                    event = payload[0]
                    content = payload[1] if len(payload) > 1 else None

                    # Роутинг событий
                    await self.route_event(event, content, namespace)

                except Exception as e:
                    logger.error(f"⚠️  Parse error: {e}")
                    logger.error(f"Raw: {data}")
            else:
                logger.warning(f"⚠️  Failed to parse Socket.IO event: {data}")

    async def route_event(self, event: str, data: dict, namespace: str):
        """Роутинг событий к соответствующим обработчикам"""

        # События из /user-notifications
        if namespace == "/user-notifications":
            if event == "sale_update":
                await self.on_sale_update(data)
            else:
                logger.info(f"📩 [/user-notifications] {event}")

        # События из /orders
        elif namespace == "/orders":
            if event == "order_subscribe":
                logger.debug(f"🔔 Subscribed to order: {data.get('orderId')}")
            elif event == "order_refunded":
                await self.on_order_refunded(data)
            else:
                logger.info(f"📩 [/orders] {event}: {data}")

        # События из /chats
        elif namespace == "/chats":
            if event == "message_created":
                await self.on_message_created(data)
            elif event == "chat_read":
                logger.debug(f"✓ Chat read: {data.get('chatId')}")
            elif event == "typing_subscribe":
                logger.debug(f"⌨️  Typing subscribe: {data.get('chatId')}")
            elif event == "typing_unsubscribe":
                logger.debug(f"⌨️  Typing unsubscribe: {data.get('chatId')}")
            else:
                logger.info(f"📩 [/chats] {event}")

        # События из /user-presence
        elif namespace == "/user-presence":
            if event == "user_presence_update":
                user_id = data.get("userId")
                is_online = data.get("isOnline")
                logger.debug(f"👤 User {user_id}: {'🟢 online' if is_online else '🔴 offline'}")
            else:
                logger.debug(f"[/user-presence] {event}")

        else:
            logger.info(f"📩 [{namespace}] {event}: {data}")

    # ========== ОБРАБОТЧИКИ ЗАКАЗОВ ==========

    async def on_sale_update(self, data: dict):
        """Обработка обновления продаж (ГЛАВНОЕ СОБЫТИЕ!)"""
        order_id = data.get("orderId")
        delta = data.get("delta")

        if delta > 0:
            logger.info("🛒 NEW ORDER!")
            logger.info(f"Order ID: {order_id}")
            #ДОБАВИМ В БАЗУ ДАННЫХ - ДЛЯ БЕЗОПАСНОСТИ
            conn = await aiosqlite.connect("orders.db")
            cursor = await conn.cursor()
            await cursor.execute('insert or ignore into orders(id, status) values (?, ?)', (order_id, 'оплачен',))
            await conn.commit()
            await conn.close()

            if order_id not in self.orders:
                await self.process_new_order(order_id)

        elif delta < 0:
            logger.warning(f"💸 Order refunded: {order_id} (delta: {delta})")

    async def on_order_refunded(self, data: dict):
        """Обработка возврата заказа"""
        order_id = data.get("orderId")
        logger.warning(f"🔙 Order {order_id} has been refunded")

    async def get_order_details(self, order_id: str):
        """Получить детали заказа включая reviewId"""
        build_id = await self.get_build_id()

        for attempt in range(2):
            url = f"https://starvell.com/_next/data/{build_id}/order/{order_id}.json?order_id={order_id}"

            try:
                async with aiohttp.ClientSession(headers=self.headers) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            build_id = await self.get_build_id()
                            logger.error(f"❌ Failed to get order details: HTTP {resp.status}. Пробуем снова")
                            continue

                        data = await resp.json()
                        return data

            except Exception as e:
                logger.error(f"❌ Error getting order details: {e}")
                traceback.print_exc()
                return None

        return None
    async def on_message_created(self, data: dict):
        try:
            msg_type = data.get("type")

            if msg_type == "NOTIFICATION":
                metadata = data.get("metadata", {})
                notification_type = metadata.get("notificationType")
                order_id = metadata.get("orderId")

                if notification_type == "REVIEW_CREATED":
                    logger.info(f"📝 Новый отзыв для заказа {order_id}")

                    # Получаем детали заказа чтобы найти reviewId
                    order_details = await self.get_order_details(order_id)

                    if not order_details:
                        logger.error(f"❌ Не удалось получить детали заказа {order_id}")
                        return

                    # reviewId находится в pageProps.review.id
                    review_data = order_details.get('pageProps', {}).get('review')

                    if not review_data:
                        logger.error(f"❌ reviewId не найден в деталях заказа {order_id}")
                        return

                    review_id = review_data.get('id')

                    if not review_id:
                        logger.error(f"❌ review.id отсутствует")
                        return

                    logger.info(f"✅ Найден reviewId: {review_id}")

                    # Ответить на отзыв
                    res = await self.answer_review(review_id, universal)

                    if res:
                        logger.info(f'✅ Ответили на отзыв {review_id}')
                    else:
                        logger.warning(f'❌ Не удалось ответить, повтор...')
                        await asyncio.sleep(1)
                        res = await self.answer_review(review_id, universal)
                        if res:
                            logger.info(f'✅ Ответили на отзыв {review_id} (попытка 2)')
                        else:
                            logger.error(f'❌ Не удалось ответить после 2 попыток')

                if notification_type == "ORDER_REFUND":
                    try:

                        await self.send_chat_message(data.get("chatId"), refund_msg)
                    except Exception as e:
                        logger.error(f'Ошибка при отправке уведомления о возврате {order_id}')

            elif msg_type == "DEFAULT":
                author = data.get("author")
                content = data.get("content", "")

                metadata = data.get("metadata")
                if metadata and metadata.get("isAuto"):
                    return

                if author:
                    chat_id = data.get("chatId")
                    author_id = data.get("authorId")
                    if author_id:
                        if author_id != my_id:

                            user_id = author_id

                            if user_id not in self.users:
                                self.users[user_id] = {}
                                self.users[user_id]['state'] = 'FREE'
                                self.users[user_id]['order'] = None
                                self.users[user_id]['hello'] = False

                            self.users[user_id]['active'] = True

                            if content.startswith('/'):

                                try:
                                    self.users[user_id]['limit']
                                except:
                                    self.users[user_id]['limit'] = 0

                                self.users[user_id]['limit'] += 1

                                if self.users[user_id]['limit'] >= 5:
                                    return

                                if content.lower().startswith('/вызов'):

                                    text = f'🚨 Вызов от https://starvell.com/users/{user_id} \n'
                                    text += content.replace('/вызов', '').replace('/Вызов', '').strip()

                                    await self.bot.send_message(chat_id=self.admin, text=text, disable_web_page_preview=True)

                                    await self.send_chat_message(
                                        chat_id,
                                        '📞 Ваш вызов отправлен владельцу, ожидайте ответа'
                                    )


                                elif content.lower() == '/help':
                                    await self.send_chat_message(
                                        chat_id,
                                        '📋 Доступные команды:\n'
                                        '/вызов - вызвать поддержку\n'
                                        '/help - список команд\n'
                                    )
                                else:
                                    await self.send_chat_message(
                                        chat_id,
                                        '❌ Не распознал команду, /help для списка команд'
                                    )
                                return

                            if self.users[user_id]['state'] == 'FREE':
                                # Приветствие для новых пользователей
                                if not self.users[user_id]['hello']:
                                    self.users[user_id]['hello'] = True
                                    await self.send_chat_message(chat_id, hello)

                            elif self.users[user_id]['state'] == 'CHOOSING_FINALE':

                                order_id = self.users[user_id]['order'].order_id
                                if 'да' in content.lower() or '+' in content.lower():
                                    await self.send_chat_message(chat_id,
                                                                 '🐬 Ваш заказ был добавлен в очередь, ожидайте')
                                    self.users[user_id]['state'] = 'FREE'
                                    self.users[user_id]['order'] = None
                                    self.users[user_id]['hello'] = False

                                    # await self.fragment_giver(order)
                                    self.orders[order_id].mark_completed()

                                    res = None
                                    for _ in range(self.orders[order_id].quantity):
                                        res = await self.fragment_giver(self.orders[order_id])

                                    if res:
                                        await self.send_chat_message(chat_id,
                                                                     '✅ Звезды отправлены на ваш аккаунт, оставьте отзыв')
                                        self.users[user_id]['active'] = False
                                        self.users[user_id]['limit'] = 0
                                        return
                                    else:
                                        await self.send_chat_message(chat_id,
                                                                     '❌ Произошла ошибка, напишите /вызов и скоро подключится администратор')
                                        self.users[user_id]['active'] = False
                                        self.users[user_id]['limit'] = 0
                                        return

                                elif 'нет' in content.lower() or '-' in content.lower():
                                    await self.send_chat_message(chat_id,
                                                                 'Начинаем заново..\n'
                                                                 '👤 Введите актуальный @username')
                                    self.users[user_id]['state'] = 'CHOOSING_USERNAME'


                            elif self.users[user_id]['state'] == 'CHOOSING_USERNAME':
                                order_id = self.users[user_id]['order'].order_id
                                username = self.extract_username(content)

                                if not username:
                                    await self.send_chat_message(chat_id,
                                                                 '❌ Недопустимый формат юзернейма!\nВнимательно проверьте правильность написания и попробуйте еще раз ⬇️')
                                    return

                                self.orders[order_id].username = username

                                conn = await aiosqlite.connect("orders.db")
                                cursor = await conn.cursor()
                                await cursor.execute('update orders set username=? where id=?', (username, order_id,))
                                await conn.commit()
                                await conn.close()

                                order = self.orders[order_id]

                                self.users[user_id]['state'] = 'CHOOSING_FINALE'
                                start_order_msg = (
                                    f"🧾 Ваш заказ:\n"
                                    f"#️⃣ ID: {order.order_id}\n"
                                    f"👤 Username: {order.username}\n"
                                    f"⭐ Stars: {order.amount} | {order.quantity} шт.\n"
                                    f"\n"
                                    f"Все верно?\n"
                                    f"✅ Да (+) | Нет (-) ❌"
                                )

                                await self.send_chat_message(chat_id, start_order_msg)




        except Exception as e:
            logger.error(f"❌ Error in on_message_created: {e}")
            traceback.print_exc()

    def extract_username(self, text: str) -> str | None:
        m = self.USER_RE.findall(text)  # вытягиваем ВСЕ совпадения
        if m:
            return m[-1]  # берём последний
        # если ничего не нашли — пытаемся взять «последнее слово»
        fallback = re.findall(r'\b([A-Za-z]\w{3,30})\b', text)
        return fallback[-1] if fallback else None

    async def get_build_id(self):
        """Получить актуальный Build ID"""
        async with aiohttp.ClientSession() as session:
            async with session.get("https://starvell.com", headers=self.headers) as resp:
                html = await resp.text()
                match = re.search(r'"buildId":"([^"]+)"', html)
                if match:
                    return match.group(1)
        return None

    async def get_orders_info(self):
        id = await self.get_build_id()

        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://starvell.com/_next/data/{id}/account/sells.json", headers=self.headers) as resp:
                        if not 200 <= resp.status < 300:
                            logger.warning(f'Попытка {attempt} - пробуем еще раз')
                            id = await self.get_build_id()
                        else:
                            json_data = await resp.json()
                            return json_data
            except:
                logger.error(f'Попытка {attempt} - пробуем еще раз')
        return


    async def check_balance_and_cancel(self):

        while True:
            try:
                from config import fragment_cookie

                ton_balance = await self.stars_distributor.check_wallet_balance()
                ton_balance = float(ton_balance)

                headers = {
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Accept-Language": "ru,en;q=0.9",
                    "Connection": "keep-alive",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Cookie": fragment_cookie,
                    "Host": "fragment.com",
                    "Origin": "https://fragment.com",
                    "Referer": "https://fragment.com/stars/buy",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "X-Requested-With": "XMLHttpRequest"
                }

                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get("https://fragment.com/stars/buy", headers=headers) as resp:

                        json_response = await resp.text()
                        prices = self.extract_stars_prices(json_response)
                        my_offers = await self.get_my_offers()
                        my_offers = my_offers['pageProps']['offers']

                        stars_offers = {}
                        for offer in my_offers:

                            if not offer.get('isActive'):
                                continue

                            id = offer.get('id')
                            category_obj = offer.get('subCategory')
                            sub_category_name = category_obj.get("name", "")

                            stars_match = re.search(r'(\d+)', sub_category_name)
                            if not stars_match:
                                logger.warning(f"⚠️ Не удалось извлечь количество звёзд из '{sub_category_name}'")
                                continue

                            stars_count = int(stars_match.group(1))
                            stars_offers[stars_count] = id

                        if prices:
                            for count, price in prices.items():
                                if count in stars_offers:
                                    if price + 0.1 >= ton_balance:
                                        await self.update_order_status(stars_offers[count], False)

                await asyncio.sleep(30)
            except:
                await asyncio.sleep(30)
                continue

    def extract_stars_prices(self, json_or_html_content):
        try:
            data = json.loads(json_or_html_content)
            html_content = data.get('h', '')
            if not html_content:
                logger.warning("⚠️ HTML не найден в JSON ответе")
                return {}
        except json.JSONDecodeError:
            # Если это не JSON, считаем что это уже HTML
            html_content = json_or_html_content

        parser = BeautifulSoup(html_content, 'html.parser')

        # Найти все радио-кнопки с пакетами звёзд
        radio_items = parser.find_all('input', {'type': 'radio', 'name': 'stars'})
        stars_prices = {}

        for radio in radio_items:
            # Получить количество звёзд из value
            stars_count = radio.get('value')

            if not stars_count:
                continue

            # Найти родительский элемент с ценой
            parent = radio.find_parent('label', {'class': 'tm-form-radio-item'})

            if not parent:
                continue

            # Найти div с классом tm-value (цена в TON)
            price_div = parent.find('div', {'class': 'tm-value'})

            if not price_div:
                continue

            # Извлечь текст цены
            price_text = price_div.get_text(strip=True)

            # Убрать всё кроме цифр, точек и запятых
            price_clean = price_text.replace(',', '')

            # Найти число с помощью regex
            match = re.search(r'[\d.]+', price_clean)

            if match:
                price_ton = float(match.group())
                stars_prices[int(stars_count)] = price_ton
        try:

            stars_100 = stars_prices[100]

            stars_prices[200] = stars_100 * 2
            stars_prices[300] = stars_100 * 3

        except:
            pass

        return stars_prices

    async def answer_review(self, review_id: str, content: str):
        """Ответить на отзыв"""

        payload = {
            "content": content,
            "reviewId": review_id
        }

        url = "https://starvell.com/api/review-responses/create"

        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json=payload) as resp:
                    response_text = await resp.text()

                    if resp.status >= 400:
                        logger.error(f"❌ Ошибка при ответе: HTTP {resp.status}")
                        logger.error(f" Ответ: {response_text}")
                        return None

                    try:
                        json_data = json.loads(response_text)
                        logger.info(f"✅ Успешно ответили: {review_id}")
                        return json_data
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ Invalid JSON response: {e}")
                        return None

        except Exception as e:
            logger.error(f"❌ Ошибка при ответе на отзыв: {e}")
            traceback.print_exc()
            return None

    async def send_chat_message(self, chat_id: str, content: str):

        payload = {"chatId": chat_id, "content": content}
        url = "https://starvell.com/api/messages/send"

        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(url, json=payload) as resp:
                response_text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}: {response_text}")
                try:
                    return json.loads(response_text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Invalid response from server") from exc

    async def get_extra_orders(self):
        """Резервная проверка заказов на случай пропуска через WebSocket"""
        from datetime import datetime

        # Запоминаем время запуска
        UTC_TZ = timezone.utc
        start_time = datetime.now(UTC_TZ)

        await self.ws_connected.wait()
        logger.info("✅ WebSocket готов, запускаем проверку заказов")

        while True:
            try:
                data = await self.get_orders_info()

                if not data:
                    logger.warning("⚠️ Не удалось получить список заказов")
                    await asyncio.sleep(100)
                    continue

                page_props = data.get('pageProps', {})
                orders = page_props.get('orders', [])

                for order in orders:
                    order_id = order.get('id')
                    order_status = order.get('status')
                    created_at_str = order.get('createdAt')

                    # Пропускаем возвращённые заказы
                    if order_status == 'REFUND':
                        continue

                    # Пропускаем если заказ уже обработан
                    if order_id in self.orders:
                        logger.info('пропустили')
                        continue

                    UTC = timezone.utc  # или timezone(timedelta(hours=0))

                    try:

                        created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))

                        if created_at < start_time.astimezone(UTC):
                            continue

                        time_diff = datetime.now(UTC) - created_at
                        if time_diff.total_seconds() < 5:
                            continue

                    except Exception as e:
                        logger.error(f"❌ Ошибка парсинга времени заказа {order_id}: {e}")
                        continue

                    # Заказ пропущен! Обрабатываем
                    logger.warning(f"⚠️ ПРОПУЩЕННЫЙ ЗАКАЗ ОБНАРУЖЕН: {order_id}")
                    logger.warning(f"   Создан: {created_at_str}")
                    logger.warning(f"   Статус: {order_status}")
                    logger.warning(f"   Обрабатываем...")

                    # Проверяем в БД
                    conn = await aiosqlite.connect("orders.db")
                    cursor = await conn.cursor()
                    await cursor.execute('INSERT OR IGNORE INTO orders(id, status) VALUES (?, ?)',
                                         (order_id, 'оплачен'))
                    await conn.commit()
                    await conn.close()

                    # Обрабатываем как обычный заказ
                    await self.process_new_order(order_id)

                    logger.info(f"✅ Пропущенный заказ {order_id} успешно обработан")

            except Exception as e:
                logger.error(f"❌ Ошибка в get_extra_orders: {e}")
                traceback.print_exc()

            # Проверяем каждые 100 секунд
            await asyncio.sleep(10)

    async def process_new_order(self, order_id: str):

        try:
            """Обработка нового заказа"""
            logger.info(f"⚙️  Processing order {order_id}...")

            details = await self.get_order_details(order_id)

            if not details:
                logger.error(f"❌ Не удалось получить детали заказа {order_id}")
                return

            # Извлекаем данные из ответа API
            page_props = details.get("pageProps", {})
            order_data = page_props.get("order", {})

            chat_data = page_props.get("chat", {})

            # === ОСНОВНЫЕ ДАННЫЕ ЗАКАЗА ===
            total_price = order_data.get("totalPrice")
            quantity = order_data.get("quantity")

            # === КОЛИЧЕСТВО ЗВЁЗД ===
            offer_details = order_data.get("offerDetails", {})
            sub_category = offer_details.get("subCategory", {})
            sub_category_name = sub_category.get("name", "")

            stars_match = re.search(r'(\d+)', sub_category_name)
            stars_amount = int(stars_match.group(1)) if stars_match else 0

            logger.info(f"⭐ Звёзд в заказе: {stars_amount}")

            # === USERNAME ИЗ ЗАКАЗА ===
            order_args = order_data.get("orderArgs", [])
            entered_username = None

            if order_args:
                entered_username = order_args[0].get("value", "").strip()

            logger.info(f"📝 Введённый username: {entered_username}")

            # === ДАННЫЕ ПОКУПАТЕЛЯ ===
            buyer = order_data.get("buyer", {})
            buyer_id = buyer.get("id")

            # === ID ЧАТА ===
            chat_id = chat_data.get("id")

            # === ЛОГИРОВАНИЕ ===
            logger.info("=" * 60)
            logger.info(f"📦 Order ID: {order_id}")
            logger.info(f"💰 Total Price: {total_price}")
            logger.info(f"🔢 Quantity: {quantity}")
            logger.info(f"⭐ Stars: {stars_amount}")
            logger.info(f"👤 Buyer ID: {buyer_id}")
            logger.info(f"📝 Telegram Username (entered): {entered_username}")
            logger.info(f"💬 Chat ID: {chat_id}")
            logger.info("=" * 60)

            if buyer_id in self.users:
                if self.users[buyer_id]['state'] != 'FREE':
                    return

            # === РАБОТА С БД ===
            conn = await aiosqlite.connect("orders.db")
            try:
                cursor = await conn.cursor()

                await cursor.execute(
                    """
                    UPDATE orders 
                    SET amount = ?, quantity = ?, username = ?, user_id = ?, chat_id = ?
                    WHERE id = ?
                    """,
                    (stars_amount, quantity, entered_username, buyer_id, chat_id, order_id,)
                )

                await conn.commit()
                logger.info(f"✅ Order {order_id} updated in database")

            except Exception as e:
                logger.error(f"❌ Ошибка ДБ: {e}")
                traceback.print_exc()
            finally:
                await conn.close()

            # === СОЗДАЁМ ОБЪЕКТ ЗАКАЗА ===
            final_username = entered_username

            order = Order(
                order_id=order_id,
                amount=stars_amount,
                quantity=quantity,
                user_id=buyer_id,
                username=final_username,
                chat_id=chat_id
            )

            self.orders[order_id] = order

            try:
                await self.bot.send_message(chat_id=self.admin,
                                            text=f"🧾 Пришел заказ:\n"
                f"#️⃣ ID: {order.order_id}\n"
                f"👤 Username: {order.username}\n"
                f"⭐ Stars: {order.amount} | {order.quantity} шт.\n"
                f"Ссылка на заказ: https://starvell.com/order/{order.order_id}\n", disable_web_page_preview=True
                                            )
            except:
                pass

            # === ИНИЦИАЛИЗАЦИЯ ПОЛЬЗОВАТЕЛЯ ===
            if buyer_id not in self.users:
                self.users[buyer_id] = {}

            self.users[buyer_id]['order'] = order
            self.users[buyer_id]['state'] = 'CHOOSING_FINALE'
            self.users[buyer_id]['active'] = True
            self.users[buyer_id]['hello'] = True

            # === ОТПРАВКА СООБЩЕНИЯ ===
            start_order_msg = (
                f"🧾 Ваш заказ:\n"
                f"#️⃣ ID: {order.order_id}\n"
                f"👤 Username: {order.username}\n"
                f"⭐ Stars: {order.amount} | {order.quantity} шт.\n"
                f"\n"
                f"Все верно?\n"
                f"✅ Да (+) | Нет (-) ❌"
            )

            await self.send_chat_message(chat_id, start_order_msg)
        except:
            traceback.print_exc()
            logger.error(f'Ошибка при процессинге {order_id}')
            await self.refund_order(order_id)


    async def refund_order(self, order_id):

        try:
            payload = {"orderId": order_id}
            url = "https://starvell.com/api/orders/refund"

            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    try:
                        ct = resp.headers.get("Content-Type", "")
                        if "application/json" in ct.lower():
                            return await resp.json()
                        text = await resp.text()
                        return {"status": resp.status, "text": text}
                    except ContentTypeError:
                        try:
                            text = await resp.text()
                        except Exception:
                            text = ""
                        return {"status": resp.status, "text": text}

        except Exception as e:
            logger.error(f'Ошибка при рефаунде заказа {order_id}: {e}')
            traceback.print_exc()
            return None

    def parse_starvell_offers(self, api_response):
        """
        Парсит предложения из ответа Starvell API

        Args:
            api_response: Ответ API Starvell

        Returns:
            Список словарей с информацией о предложениях
        """
        try:
            # Извлекаем предложения из ответа
            offers = api_response.get('pageProps', {}).get('offers', [])

            parsed_offers = []

            for offer in offers:
                # Базовые данные
                offer_id = offer.get('id')
                price = offer.get('price')  # Цена в рублях
                category_id = offer.get('categoryId')
                game_id = offer.get('gameId')
                sub_category_id = offer.get('subCategoryId')
                is_active = offer.get('isActive', False)

                # Название подкатегории (количество звезд)
                sub_category_name = None
                if 'subCategory' in offer:
                    sub_category_name = offer['subCategory'].get('name')

                # Способ получения (из атрибутов)
                delivery_method = None
                attributes = offer.get('attributes', [])
                for attr in attributes:
                    option_id = attr.get('optionId')
                    if option_id == 'c08b9c8b-c7a3-4cb5-b853-ad20252e3dc2':
                        delivery_method = 'По username'
                    elif option_id == '9abe5573-3184-401c-8bec-de0d8dd38529':
                        delivery_method = 'Подарком'

                # Формируем объект предложения
                parsed_offer = {
                    'id': offer_id,
                    'price': float(price) if price else 0.0,
                    'sub_category_id': sub_category_id,
                    'sub_category_name': sub_category_name,
                    'is_active': is_active,
                    'game_id': game_id,
                    'category_id': category_id,
                }

                parsed_offers.append(parsed_offer)

            return parsed_offers

        except Exception as e:
            return []

    async def get_him_offers(self, game_id, category_id, sub_category_id):
        """Получить предложения конкурентов (POST запрос)"""

        url = 'https://starvell.com/api/offers/list-by-category'

        # Параметры для POST запроса
        payload = {
            "limit": 100,
            "offset": 0,
            "categoryId": int(category_id),
            "onlyOnlineUsers": False,
            "attributes": [],
            "sortBy": "price",
            "sortDir": "ASC",
            "sortByPriceAndBumped": True,
            "withCompletionRates": True,
            "subCategoryId": int(sub_category_id)
        }

        try:
            async with aiohttp.ClientSession() as session:
                # POST запрос с JSON в теле
                async with session.post(
                        url,
                        headers=self.headers,
                        json=payload
                ) as response:

                    if 200 <= response.status < 300:
                        json_data = await response.json()

                        # API возвращает список напрямую
                        if isinstance(json_data, list):
                            offers = json_data
                        elif isinstance(json_data, dict):
                            offers = json_data.get('offers', [])
                        else:
                            offers = []

                        logger.info(f"📊 Получено {len(offers)} предложений конкурентов")
                        return offers
                    else:
                        resp_text = await response.text()
                        logger.warning(f'⚠️ Не удалось получить предложения конкурентов: HTTP {response.status}')
                        logger.debug(f"Response: {resp_text[:500]}")  # Первые 500 символов
                        return []

        except Exception as e:
            logger.error(f"❌ Ошибка получения предложений конкурентов: {e}")
            traceback.print_exc()
            return []

async def main():
    bot = StarvellBot()

    tasks = [
        asyncio.create_task(bot.get_updates()),
        asyncio.create_task(bot.get_extra_orders()),
        asyncio.create_task(bot.dumper()),
        asyncio.create_task(bot.bumper())
    ]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
        for task in tasks:
            task.cancel()