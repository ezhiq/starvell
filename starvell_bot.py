import traceback
from asyncio import CancelledError
from datetime import timezone

import aiohttp
import asyncio
import json
import logging
import re

import aiosqlite
import ccxt
from aiohttp import ContentTypeError
from config import cc
from constants import converted
from datatypes import FragmentOrder, StarGiftOrder, StarGiftMask
from fragment_api import FragmentConfig
from stars_api_giver import StarsAPIGiver
from stars_distributor import StarsDistributor, TONWalletConfig
import config as gi
from bs4 import BeautifulSoup
import re

class StarvellBot:
    def __init__(self, admin):

        cookie_session = cc.get('cookie_session')

        self.logger = logging.getLogger('StarvellBot')
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

        self.bot = gi.bot
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
        self._init_gift_distributor()

        self.ws_connected = asyncio.Event()

        self.event_pattern = re.compile(r'^42(/[\w\-]+)?,?\[(.+)\]$')\

        self.categories = []
        self.game_id = None

    def get_id(self, text):
        id_match = re.search(r'id:\s*([a-zA-Z0-9_]+)', text)
        return id_match.group(1) if id_match else None

    def _init_gift_distributor(self):
        self.api_giver = StarsAPIGiver()

        stars_api = self.api_giver
        gi.api_giver = stars_api


    async def bumping(self, game_id, categories):

        try:
            url = "https://starvell.com/api/offers/bump"
            payload = {"gameId": game_id, "categoryIds": [categories]}

            async with aiohttp.ClientSession() as session:
                async with session.post(headers=self.headers, url=url, json=payload) as response:
                    self.logger.info(response.status)
                    if response:
                        if 200 <= response.status < 300:
                            self.logger.info('Успешно подняли предложения')
                            return True
                        else:
                            self.logger.warning('Не удалось поднять предложения - скорее всего они уже подняты')

        except Exception as e:
            traceback.print_exc()
            self.logger.error('Не удалось поднять предложения')


    async def bumper(self):

        while True:
            game_id = 14
            categories = [181, 182]
            for category in categories:
                await self.bumping(game_id, category)

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
    #                 self.logger.error('ошибочка')
    #                 traceback.print_exc()
    #                 return None

    async def get_orders(self):
        url = "https://starvell.com/api/orders/list"
        payload = {
            "filter": {
                "status": "CREATED",
                "userType": "seller"
            },
            "limit": 20,
            "offset": 0,
            "orderBy": {
                "field": "createdAt",
                "order": "DESC"
            },
            "with": {
                "buyer": True
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=payload) as response:
                if response.status == 200:
                    return await response.json()
                return None

    async def get_all_orders_12h(self):
        from datetime import datetime
        resp = await self.get_orders()

        if not resp:
            return "❌ Не удалось получить заказы"

        now = datetime.now(timezone.utc)
        old_orders = []

        for order in resp:
            created_at = datetime.fromisoformat(order['createdAt'].replace('Z', '+00:00'))
            age = now - created_at

            if age.total_seconds() > 12 * 3600:
                old_orders.append(f"#{order['id'].split('-')[-1][-8:].upper()}")

        if old_orders:
            return "⚠️ Заказы старше 12 часов:\n\n" + "\n".join(old_orders)
        else:
            return "✅ Нет зависших заказов"


    async def dumping(self, queue):
        """Автопонижение цен на наши предложения"""

        offers = queue['pageProps']['offers']
        for my_offer in offers:
            description = my_offer['descriptions']['rus']['description']

            id = self.get_id(description)
            if id is None:
                self.logger.error('Отсутствует айди в описании предложения при демпинге')
                continue

            game = cc.get_parent(id)
            if game is None:
                self.logger.error('отсутствует категория демпинге')
                continue

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

            if game in ['stars', 'advstars', 'advgifts', 'giftsapi']:
                min_star_rate_fragment = cc.get('min_star_rate')
                min_star_rate_gifts = cc.get('min_star_rate_gifts')
                is_online = cc.get('is_online')
                my_id = cc.get('my_id')

                courses = {
                    "advgifts" : min_star_rate_gifts,
                    "gifts" : min_star_rate_gifts,
                    "stars" : min_star_rate_fragment,
                    "advstars" : min_star_rate_fragment,
                }

                min_star_rate = courses.get(game_id)

                # Извлекаем количество звёзд из названия подкатегории
                data = cc.find_by_key(id)['data']['special_data']

                stars_count = int(data['stars'])

                if stars_count in converted:
                    formal = converted[stars_count]
                    my_course = my_price / converted[stars_count]
                else:
                    formal = stars_count
                    my_course = my_price / stars_count
                #СЕЙВИМ ПРЕДЛОЖЕНИЕ

                if my_course < min_star_rate:
                    await self.update_order_price(my_offer, round(min_star_rate * formal * 1.01, 1))
                    await asyncio.sleep(1)
                    continue

                if my_course > min_star_rate*1.5:
                    await self.update_order_price(my_offer, round(min_star_rate * formal * 1.1, 1))
                    await asyncio.sleep(1)
                    continue


                self.logger.info(f"📊 Обрабатываем предложение {my_offer_id}: {stars_count} звёзд по {my_price}₽")

                # Получаем конкурентов
                competitors = await self.get_him_offers(game_id, category_id, sub_category_id)

                if not competitors:
                    self.logger.warning(f"⚠️ Не удалось получить конкурентов для {my_offer_id}")
                    continue

                raw_competitors = competitors

                # Для advstars и advgifts — фильтруем по количеству звёзд
                if game in ['advstars', 'advgifts']:
                    STARS_ATTR_ID = '6a2ce94f-d18a-46b5-8adc-73ded5fa965e'

                    def get_stars_count(offer):
                        for attr in offer.get('attributes', []):
                            if attr.get('id') == STARS_ATTR_ID:
                                return attr.get('numericValue')
                        return None

                    raw_competitors = [c for c in competitors if get_stars_count(c) == stars_count]
                    self.logger.info(
                        f"   advgifts: отфильтровали конкурентов по {stars_count} звёзд → {len(raw_competitors)} шт.")

                if is_online:
                    raw_competitors = [c for c in raw_competitors if c.get("user", {}).get("isOnline") == True]

                competitors = raw_competitors
                competitors = [c for c in competitors if c.get("user", {}).get("id") != my_id]


                if not competitors:
                    self.logger.info(f"✅ Наше предложение {my_offer_id} единственное в категории")
                    continue

                # === ЛОГИКА ПОНИЖЕНИЯ ===
                if game in ['stars', 'giftsapi', 'advstars', 'advgifts']:

                    our_position = None
                    cheaper_competitor = None
                    first_competitor = raw_competitors[0] if competitors else None
                    second_competitor = competitors[0] if len(competitors) >= 1 else None

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

                            seller_id = comp["user"]["id"]
                            friends = cc.get("friends")

                            if seller_id in friends:
                                self.logger.info(f'Не перебиваем друга {seller_id}')
                                continue

                            # Проверяем курс
                            star_rate = convert_price / formal

                            if star_rate < min_star_rate:
                                self.logger.warning(f"⚠️ Не перебиваем {seller}: курс {star_rate:.2f}₽ < минимум {min_star_rate}₽")
                                continue

                            self.logger.info(f"🎯 Перебиваем конкурента: {my_price}₽ → {optimal_price}₽ (курс {star_rate:.2f}₽)")
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
                        self.logger.info(f"💰 Оптимизируем первое место: {my_price}₽ → {him_price - 0.1}₽")
                        await self.update_order_price(my_offer, round(him_price - 0.1, 1))
                        await asyncio.sleep(1)  # Защита от rate limit

                else:
                    continue
            else:
                continue

    async def dumper(self):
        """Периодическая проверка и обновление цен"""

        # Ждём подключения WebSocket
        await self.ws_connected.wait()
        self.logger.info("✅ WebSocket готов, запускаем автопонижение цен")

        while True:
            try:
                self.logger.info("🔄 Проверяем цены...")

                # Получаем наши предложения
                data = await self.get_my_offers()

                if not data:
                    self.logger.warning("⚠️ Не удалось получить наши предложения")
                    await asyncio.sleep(300)
                    continue

                # Парсим предложения
                queue = data

                self.logger.info(f"📦 Найдено {len(queue)} наших предложений")

                # Обрабатываем
                await self.dumping(queue)

                self.logger.info("✅ Проверка цен завершена")

            except Exception as e:
                self.logger.error(f"❌ Ошибка в dumper: {e}")
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
                        self.logger.info(f"✅ Цена обновлена: {price}₽ (ID: {order_id})")
                        return True
                    else:
                        resp_text = await response.text()
                        self.logger.error(f"❌ Ошибка обновления цены {order_id}: HTTP {response.status}")
                        self.logger.error(f"Response: {resp_text}")
                        return False

        except Exception as e:
            self.logger.error(f"❌ Ошибка при обновлении цены {order_id}: {e}")
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
                        self.logger.info(f"✅ Статус обновлен: {status} (ID: {id})")
                        return True
                    else:
                        resp_text = await response.text()
                        self.logger.error(f"❌ Ошибка обновления статуса {id}: HTTP {response.status}")
                        self.logger.error(f"Response: {resp_text}")
                        return False

        except Exception as e:
            self.logger.error(f"❌ Ошибка при обновлении цены {id}: {e}")
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
                        self.logger.info(f"{e}")

                    if 200 <= response.status < 300:

                        self.logger.info('Успешно парсили ордеры')
                        json_data = await response.json()
                        return json_data
                    else:
                        self.logger.warning(f'Не удалось парсить ордеры - обновляем build_id и пробуем снова - atttempt {attempt}')
                        continue


    def _init_fragment_distributor(self):
        fragment_hash, fragment_cookie, fragment_show_sender, ton_api_key, mnemonic, destination_address = cc.get('fragment_hash'), cc.get('fragment_cookie'), cc.get('fragment_show_sender'), cc.get('ton_api_key'), cc.get('mnemonic'), cc.get('destination_address')

        fragment_config = FragmentConfig(
            hash=fragment_hash,
            cookie=fragment_cookie,
            show_sender=fragment_show_sender
        )

        ton_config = TONWalletConfig(
            api_key=ton_api_key,
            mnemonic=mnemonic,
            destination_address=destination_address,
            is_testnet=False
        )

        self.stars_distributor = StarsDistributor(
            fragment_config, ton_config
        )

    async def get_updates(self):
        await self.api_giver.init_sessions()
        while True:
            try:
                async with aiohttp.ClientSession(headers=self.headers) as session:
                    async with session.ws_connect(self.ws_url, heartbeat=25) as ws:
                        self.logger.info("🟢 WebSocket connected")
                        self.ws_connected.set()

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self.handle(ws, msg.data)

            except Exception as e:
                self.logger.error(f"🔴 WebSocket disconnected: {e}")
                self.ws_connected.clear()
                await asyncio.sleep(5)  # Ждем перед переподключением
                self.logger.info("🔄 Reconnecting...")

    async def handle(self, ws, data: str):
        self.logger.info(f"⬅️  {data}")

        # Engine.IO handshake
        if data.startswith("0"):
            for ns in self.namespaces:
                await ws.send_str(f"40{ns}")
                self.logger.warning(f"➡️  Connecting to namespace: {ns}")
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
                    self.logger.error(f"⚠️  Parse error: {e}")
                    self.logger.error(f"Raw: {data}")
            else:
                self.logger.warning(f"⚠️  Failed to parse Socket.IO event: {data}")

    async def route_event(self, event: str, data: dict, namespace: str):
        """Роутинг событий к соответствующим обработчикам"""

        # События из /user-notifications
        if namespace == "/user-notifications":
            if event == "sale_update":
                await self.on_sale_update(data)
            else:
                self.logger.info(f"📩 [/user-notifications] {event}")

        # События из /orders
        elif namespace == "/orders":
            if event == "order_subscribe":
                self.logger.debug(f"🔔 Subscribed to order: {data.get('orderId')}")
            elif event == "order_refunded":
                await self.on_order_refunded(data)
            else:
                self.logger.info(f"📩 [/orders] {event}: {data}")

        # События из /chats
        elif namespace == "/chats":
            if event == "message_created":
                await self.on_message_created(data)
            elif event == "chat_read":
                self.logger.debug(f"✓ Chat read: {data.get('chatId')}")
            elif event == "typing_subscribe":
                self.logger.debug(f"⌨️  Typing subscribe: {data.get('chatId')}")
            elif event == "typing_unsubscribe":
                self.logger.debug(f"⌨️  Typing unsubscribe: {data.get('chatId')}")
            else:
                self.logger.info(f"📩 [/chats] {event}")

        # События из /user-presence
        elif namespace == "/user-presence":
            if event == "user_presence_update":
                user_id = data.get("userId")
                is_online = data.get("isOnline")
                self.logger.debug(f"👤 User {user_id}: {'🟢 online' if is_online else '🔴 offline'}")
            else:
                self.logger.debug(f"[/user-presence] {event}")

        else:
            self.logger.info(f"📩 [{namespace}] {event}: {data}")

    # ========== ОБРАБОТЧИКИ ЗАКАЗОВ ==========

    async def on_sale_update(self, data: dict):
        """Обработка обновления продаж (ГЛАВНОЕ СОБЫТИЕ!)"""
        order_id = data.get("orderId")
        delta = data.get("delta")

        if delta > 0:
            self.logger.info("🛒 NEW ORDER!")
            self.logger.info(f"Order ID: {order_id}")
            #ДОБАВИМ В БАЗУ ДАННЫХ - ДЛЯ БЕЗОПАСНОСТИ
            conn = await aiosqlite.connect("orders.db")
            cursor = await conn.cursor()
            await cursor.execute('insert or ignore into orders(id, status) values (?, ?)', (order_id, 'оплачен',))
            await conn.commit()
            await conn.close()
            if order_id not in self.orders:
                await self.process_new_order(order_id)

        elif delta < 0:
            self.logger.warning(f"💸 Order refunded: {order_id} (delta: {delta})")

    async def on_order_refunded(self, data: dict):
        """Обработка возврата заказа"""
        order_id = data.get("orderId")
        self.logger.warning(f"🔙 Order {order_id} has been refunded")

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
                            self.logger.error(f"❌ Failed to get order details: HTTP {resp.status}. Пробуем снова")
                            continue

                        data = await resp.json()
                        return data

            except Exception as e:
                self.logger.error(f"❌ Error getting order details: {e}")
                traceback.print_exc()
                return None

        return None


    async def on_message_created(self, data: dict):
        try:
            msg_type = data.get("type")
            universal = cc.get('universal')
            refund_msg = cc.get('refund_msg')
            thx_msg = cc.get('thx_msg')
            my_id = cc.get("my_id")
            hello = cc.get('hello')

            if msg_type == "NOTIFICATION":
                metadata = data.get("metadata", {})
                notification_type = metadata.get("notificationType")
                order_id = metadata.get("orderId")

                if notification_type == "REVIEW_CREATED":
                    self.logger.info(f"📝 Новый отзыв для заказа {order_id}")

                    # Получаем детали заказа чтобы найти reviewId
                    order_details = await self.get_order_details(order_id)

                    if not order_details:
                        self.logger.error(f"❌ Не удалось получить детали заказа {order_id}")
                        return

                    # reviewId находится в pageProps.review.id
                    review_data = order_details.get('pageProps', {}).get('review')

                    if not review_data:
                        self.logger.error(f"❌ reviewId не найден в деталях заказа {order_id}")
                        return

                    review_id = review_data.get('id')

                    if not review_id:
                        self.logger.error(f"❌ review.id отсутствует")
                        return

                    self.logger.info(f"✅ Найден reviewId: {review_id}")

                    # Ответить на отзыв
                    res = await self.answer_review(review_id, universal)

                    if res:
                        self.logger.info(f'✅ Ответили на отзыв {review_id}')
                    else:
                        self.logger.warning(f'❌ Не удалось ответить, повтор...')
                        await asyncio.sleep(1)
                        res = await self.answer_review(review_id, universal)
                        if res:
                            self.logger.info(f'✅ Ответили на отзыв {review_id} (попытка 2)')
                        else:
                            self.logger.error(f'❌ Не удалось ответить после 2 попыток')

                if notification_type == "ORDER_REFUND":
                    try:
                        await self.send_chat_message(data.get("chatId"), refund_msg)
                    except Exception as e:
                        self.logger.error(f'Ошибка при отправке уведомления о возврате {order_id}')

                if notification_type == "ORDER_COMPLETED":
                    # === РАБОТА С БД ===
                    conn = await aiosqlite.connect("orders.db")
                    try:
                        cursor = await conn.cursor()

                        await cursor.execute(
                            """
                            UPDATE orders 
                            SET status = ?
                            WHERE id = ?
                            """,
                            ("закрыт", order_id,)
                        )

                        await conn.commit()
                        self.logger.info(f"✅ Order {order_id} updated in database")

                        await self.send_chat_message(data.get("chatId"), thx_msg)
                    except Exception as e:
                        self.logger.error(f"❌ Ошибка ДБ: {e}")
                        traceback.print_exc()
                    finally:
                        await conn.close()



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
                                game = self.orders[order_id].game

                                if game in ['stars', 'advgifts', 'stargifts', 'advstars', 'giftsapi']:
                                    if 'да' in content.lower() or '+' in content.lower():
                                        await self.send_chat_message(chat_id,
                                                                     '🐬 Ваш заказ был добавлен в очередь, ожидайте')
                                        self.users[user_id]['state'] = 'FREE'
                                        self.users[user_id]['order'] = None
                                        asyncio.create_task(self.countdown_hello(user_id))

                                        # await self.fragment_giver(order)
                                        self.orders[order_id].mark_completed()

                                        if game in ['stars', 'advstars']:
                                            res = await self.fragment_giver(self.orders[order_id])
                                        elif game in ['advgifts', 'stargifts', 'giftsapi']:
                                            res = await self.stars_giver(self.orders[order_id])
                                        else:
                                            res = None

                                        if res:
                                            await self.send_complete(order_id)
                                            await self.send_chat_message(chat_id,
                                                                         '✅ Звезды отправлены на ваш аккаунт.🙏 Не забудьте подтвердить заказ.🙂 Мне было бы очень приятно получить отзыв <3')
                                            self.users[user_id]['active'] = False
                                            self.users[user_id]['limit'] = 0
                                            return
                                        else:
                                            await self.send_chat_message(chat_id,
                                                                         '❌ Произошла ошибка, напишите /вызов и скоро подключится администратор')
                                            self.users[user_id]['active'] = False
                                            self.users[user_id]['limit'] = 0
                                            self.users[user_id]['order'] = None
                                            self.users[user_id]['state'] = 'FREE'
                                            self.users[user_id]['hello'] = True
                                            return

                                    elif 'нет' in content.lower() or '-' in content.lower():
                                        await self.send_chat_message(chat_id,
                                                                     'Начинаем заново..\n'
                                                                     '👤 Введите актуальный @username')
                                        self.users[user_id]['state'] = 'CHOOSING_USERNAME'
                                        return


                            elif self.users[user_id]['state'] == 'CHOOSING_USERNAME':
                                order_id = self.users[user_id]['order'].order_id
                                game = self.orders[order_id].game

                                if game in ['stars', 'advgifts', 'stargifts', 'advstars', 'giftsapi']:
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

                                    if game in ['stargifts']:

                                        start_order_msg = (
                                            f"🧾 Ваш заказ:\n"
                                            f"#️⃣ ID: {order.order_id}\n"
                                            f"👤 Username: {order.username}\n"
                                            f"🎁 Подарок: {order.gift_name} | {order.quantity} шт.\n"
                                            f"\n"
                                            f"Все верно?\n"
                                            f"✅ Да (+) | Нет (-) ❌"
                                        )
                                    else:
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
            self.logger.error(f"❌ Error in on_message_created: {e}")
            traceback.print_exc()

    async def countdown_hello(self, user_id):
        cd = cc.get("hello_cooldown")
        await asyncio.sleep(cd)
        try:
            self.users[user_id]['hello'] = False
        except:
            pass

    async def stars_giver(self, order):
        res, ok = await self.api_giver.do_order(order)

        if not ok:
            traceback.print_exc()
            await self.refund_order(order.order_id)
            return False

        return ok

    async def fragment_giver(self, order):
        '''Выдача Stars (НОВАЯ РЕАЛИЗАЦИЯ)'''

        amount = order.quantity * order.amount
        success = False

        for attempt in range(3):
            success, error, tx_data = await self.stars_distributor.distribute_stars(
                username=order.username,
                quantity=amount,
                order_id=order.order_id
            )

            if success:
                # Успех
                try:

                    await self.send_chat_message(
                        order.chat_id,
                        f"✅ Заказ выполнен!\n"
                        f"🔗 {tx_data['tonviewer_url']}\n"
                        f"⭐️ Stars: {order.amount * order.quantity}\n"
                        f"🔑 Ref ID: {tx_data['ref_id']}"
                    )
                    order.mark_completed()
                    return True

                except:
                    return True

        if not success:
            traceback.print_exc()

            # Ошибка - вернуть деньги
            await self.send_chat_message(
                order.chat_id,
                f"❌ Ошибка, возвращаем деньги на баланс StarVell"
            )
            await self.refund_order(order.order_id)
            return False

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
                            self.logger.warning(f'Попытка {attempt} - пробуем еще раз')
                            id = await self.get_build_id()
                        else:
                            json_data = await resp.json()
                            return json_data
            except:
                self.logger.error(f'Попытка {attempt} - пробуем еще раз')
        return


    async def check_balance_and_cancel(self):
        await asyncio.sleep(15)
        while True:
            try:
                exchange = ccxt.binance()  # или bybit(), okx(), huobi()
                ticker = exchange.fetch_ticker('TON/USDT')
                price = ticker['last']

                ton_balance = await self.stars_distributor.check_wallet_balance()
                ton_balance = float(ton_balance)

                usdt_balance = float(price) * ton_balance

                star_balance = self.api_giver.get_max()
                sessions = self.api_giver.get_session_status()

            except CancelledError:
                raise

            except:
                await asyncio.sleep(50)
                traceback.print_exc()
                continue

            try:
                my_offers = await self.get_my_offers()
                my_offers = my_offers['pageProps']['offers']

                stars_offers = {}

                for offer in my_offers:

                    if not offer.get('isActive'):
                        continue

                    offer_id = offer.get('id')

                    description = offer['descriptions']['rus']['description']

                    id = self.get_id(description)
                    if id is None:
                        self.logger.error('Отсутствует айди в описании предложения при демпинге')
                        continue

                    game = cc.get_parent(id)
                    if game is None:
                        self.logger.error('отсутствует категория демпинге')
                        continue

                    # Извлекаем количество звёзд из названия подкатегории
                    data = cc.find_by_key(id)['data']['special_data']
                    stars_count = int(data['stars'])
                    stars_offers[stars_count] = [offer_id, game]

                if stars_offers:
                    for count, data in stars_offers.items():
                        id, game = data

                        if game in ['advstars', 'stars']:
                            if count / 100 * 1.5 * 1.1 >= usdt_balance: #ЗАПАС x1.1
                                await self.update_order_status(id, False)

                        if game in ['advgifts', 'giftsapi', 'starsgifts']:

                            if round(count * 1.20) > star_balance:
                                await self.update_order_status(id, False)

                            if sessions == 0:
                                await self.update_order_status(id, False)

                await asyncio.sleep(30)

            except CancelledError:
                raise

            except:
                traceback.print_exc()
                await asyncio.sleep(30)
                continue


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
                        self.logger.error(f"❌ Ошибка при ответе: HTTP {resp.status}")
                        self.logger.error(f" Ответ: {response_text}")
                        return None

                    try:
                        json_data = json.loads(response_text)
                        self.logger.info(f"✅ Успешно ответили: {review_id}")
                        return json_data
                    except json.JSONDecodeError as e:
                        self.logger.error(f"❌ Invalid JSON response: {e}")
                        return None

        except Exception as e:
            self.logger.error(f"❌ Ошибка при ответе на отзыв: {e}")
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

    async def send_complete(self, order_id: str):

        payload = {"id": order_id}
        url = f"https://starvell.com/api/orders/{order_id}/mark-seller-completed"

        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(url, json=payload) as resp:
                return True

    async def get_extra_orders(self):
        """Резервная проверка заказов на случай пропуска через WebSocket"""
        from datetime import datetime

        # Запоминаем время запуска
        UTC_TZ = timezone.utc
        start_time = datetime.now(UTC_TZ)

        await self.ws_connected.wait()
        self.logger.info("✅ WebSocket готов, запускаем проверку заказов")

        while True:
            try:
                data = await self.get_orders_info()

                if not data:
                    self.logger.warning("⚠️ Не удалось получить список заказов")
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
                        self.logger.info('пропустили')
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
                        self.logger.error(f"❌ Ошибка парсинга времени заказа {order_id}: {e}")
                        continue

                    # Заказ пропущен! Обрабатываем
                    self.logger.warning(f"⚠️ ПРОПУЩЕННЫЙ ЗАКАЗ ОБНАРУЖЕН: {order_id}")
                    self.logger.warning(f"   Создан: {created_at_str}")
                    self.logger.warning(f"   Статус: {order_status}")
                    self.logger.warning(f"   Обрабатываем...")

                    # Проверяем в БД
                    conn = await aiosqlite.connect("orders.db")
                    cursor = await conn.cursor()
                    await cursor.execute('INSERT OR IGNORE INTO orders(id, status) VALUES (?, ?)',
                                         (order_id, 'оплачен'))
                    await conn.commit()
                    await conn.close()

                    # Обрабатываем как обычный заказ
                    await self.process_new_order(order_id)

                    self.logger.info(f"✅ Пропущенный заказ {order_id} успешно обработан")

            except Exception as e:
                self.logger.error(f"❌ Ошибка в get_extra_orders: {e}")
                traceback.print_exc()

            # Проверяем каждые 100 секунд
            await asyncio.sleep(10)

    async def process_new_order(self, order_id: str):

        try:
            """Обработка нового заказа"""
            self.logger.info(f"⚙️  Processing order {order_id}...")

            details = await self.get_order_details(order_id)

            if not details:
                self.logger.error(f"❌ Не удалось получить детали заказа {order_id}")
                return

            # Извлекаем данные из ответа API
            page_props = details.get("pageProps", {})
            order_data = page_props.get("order", {})
            description = order_data['offerDetails']['descriptions']['rus']['description']

            id = self.get_id(description)
            if id is None:
                self.logger.error('Отсутствует айди в описании предложения')
                await self.refund_order(order_id)
                return

            game = cc.get_parent(id)
            if game is None:
                self.logger.error('отсутствует категория')
                await self.refund_order(order_id)
                return

            data = cc.find_by_key(id)['data']['special_data']

            chat_data = page_props.get("chat", {})

            # === ОСНОВНЫЕ ДАННЫЕ ЗАКАЗА ===
            total_price = order_data.get("totalPrice")
            quantity = order_data.get("quantity")

            # === ДАННЫЕ ПОКУПАТЕЛЯ ===
            buyer = order_data.get("buyer", {})
            buyer_id = buyer.get("id")

            # === ID ЧАТА ===
            chat_id = chat_data.get("id")

            start_order_msg = 'К вашему заказу скоро подключится администратор 👤'
            order = None

            try:
                if game in ['stars', 'advgifts', 'stargifts', 'advstars', 'giftsapi']:
                    # === КОЛИЧЕСТВО ЗВЁЗД ===
                    offer_details = order_data.get("offerDetails", {})
                    sub_category = offer_details.get("subCategory", {})
                    sub_category_name = sub_category.get("name", "")

                    stars_amount = data['stars']

                    self.logger.info(f"⭐ Звёзд в заказе: {stars_amount}")

                    # === USERNAME ИЗ ЗАКАЗА ===
                    order_args = order_data.get("orderArgs", [])
                    entered_username = None

                    if order_args:
                        entered_username = order_args[0].get("value", "").strip()

                    self.logger.info(f"📝 Введённый username: {entered_username}")

                    # === ЛОГИРОВАНИЕ ===
                    self.logger.info("=" * 60)
                    self.logger.info(f"📦 Order ID: {order_id}")
                    self.logger.info(f"💰 Total Price: {total_price}")
                    self.logger.info(f"🔢 Quantity: {quantity}")
                    self.logger.info(f"⭐ Stars: {stars_amount}")
                    self.logger.info(f"👤 Buyer ID: {buyer_id}")
                    self.logger.info(f"📝 Telegram Username (entered): {entered_username}")
                    self.logger.info(f"💬 Chat ID: {chat_id}")
                    self.logger.info("=" * 60)

                    if buyer_id in self.users:
                        if self.users[buyer_id]['state'] != 'FREE':
                            return

                    # === РАБОТА С БД ===
                    conn = await aiosqlite.connect("orders.db")
                    try:
                        cursor = await conn.cursor()

                        if game in ['giftsapi', 'advgifts', 'stargifts']:
                            way = 'api'
                        else:
                            way = 'fragment'

                        await cursor.execute(
                            """
                            UPDATE orders 
                            SET amount = ?, quantity = ?, username = ?, chat_id = ?, name = ?, game = ?, way = ?
                            WHERE id = ?
                            """,
                            (stars_amount, quantity, entered_username, chat_id, id, game, way, order_id,)
                        )

                        await conn.commit()
                        self.logger.info(f"✅ Order {order_id} updated in database")

                    except Exception as e:
                        self.logger.error(f"❌ Ошибка ДБ: {e}")
                        traceback.print_exc()
                    finally:
                        await conn.close()

                    # === СОЗДАЁМ ОБЪЕКТ ЗАКАЗА ===
                    final_username = entered_username

                    if game in ['stars', 'advstars']:
                        try:

                            order = FragmentOrder(
                                name=id,
                                game=game,
                                order_id=order_id,
                                amount=stars_amount,
                                quantity=quantity,
                                user_id=buyer_id,
                                username=final_username,
                                chat_id=chat_id
                            )
                        except (IndexError, TypeError, KeyError):
                            await self.refund_order(order_id)
                            return

                    elif game in ['advgifts', 'stargifts', 'giftsapi']:

                        balance = self.api_giver.get_max()

                        true_amount = 0
                        if stars_amount == 13:
                            true_amount = 15
                        elif stars_amount == 21:
                            true_amount = 25
                        elif stars_amount == 43:
                            true_amount = 50
                        elif stars_amount == 85:
                            true_amount = 100

                        if balance < true_amount * quantity:
                            await self.send_chat_message(chat_id, '⭐ Недостаточно баланса, извините')
                            await self.refund_order(order_id)
                            await self.turn_off_orders(target_id=id)

                        try:

                            mask = data['mask']
                            ids = data['ids']

                            if 'name' in data:
                                gift_name = data['name']
                            else:
                                gift_name = 'Отсутствует'

                            order = StarGiftOrder(
                                name=id,
                                game=game,
                                gift_name=gift_name,
                                order_id=order_id,
                                amount=stars_amount,
                                quantity=quantity,
                                user_id=buyer_id,
                                username=final_username,
                                chat_id=chat_id,
                                mask=StarGiftMask(
                                    order_id=order_id,
                                    mask=mask,
                                    ids=ids
                                )
                            )

                        except (IndexError, TypeError, KeyError):
                            await self.refund_order(order_id)
                            return
                    else:
                        order = None

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

                    if game in ['stargifts']:
                        start_order_msg = (
                        f"🧾 Ваш заказ:\n"
                        f"#️⃣ ID: {order.order_id}\n"
                        f"👤 Username: {order.username}\n"
                        f"🎁 Подарок : {order.gift_name} | {order.quantity} шт.\n"
                        f"\n"
                        f"Все верно?\n"
                        f"✅ Да (+) | Нет (-) ❌"
                    )
                    else:
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
                self.orders[order_id] = order

            except:
                await self.refund_order(order_id)
                # === ИНИЦИАЛИЗАЦИЯ ПОЛЬЗОВАТЕЛЯ ===
                if buyer_id not in self.users:
                    self.users[buyer_id] = {}

                self.users[buyer_id]['active'] = False
                self.users[buyer_id]['limit'] = 0
                self.users[buyer_id]['order'] = None
                self.users[buyer_id]['state'] = 'FREE'
                self.users[buyer_id]['hello'] = True
                return

        except:
            traceback.print_exc()
            self.logger.error(f'Ошибка при процессинге {order_id}')
            await self.refund_order(order_id)

    async def turn_off_orders(self, categories=None, target_id=None):

        my_offers = await self.get_my_offers()
        my_offers = my_offers['pageProps']['offers']

        for offer in my_offers:

            if not offer.get('isActive'):
                continue

            offer_id = offer.get('id')

            description = offer['descriptions']['rus']['description']
            id = self.get_id(description)
            if id is None:
                self.logger.error('Отсутствует айди в описании предложения при демпинге')
                continue

            game = cc.get_parent(id)
            if game is None:
                self.logger.error('отсутствует категория демпинге')
                continue

            if categories:
                if game in categories:
                    await self.update_order_status(offer_id, False)

            if target_id:
                if id == target_id:
                    await self.update_order_status(offer_id, False)


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
            self.logger.error(f'Ошибка при рефаунде заказа {order_id}: {e}')
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

                        print(offers)
                        self.logger.info(f"📊 Получено {len(offers)} предложений конкурентов")
                        return offers
                    else:
                        resp_text = await response.text()
                        self.logger.warning(f'⚠️ Не удалось получить предложения конкурентов: HTTP {response.status}')
                        self.logger.debug(f"Response: {resp_text[:500]}")  # Первые 500 символов
                        return []

        except Exception as e:
            self.logger.error(f"❌ Ошибка получения предложений конкурентов: {e}")
            traceback.print_exc()
            return []

# async def main():
#     bot = StarvellBot()
#
#     tasks = [
#         asyncio.create_task(bot.get_updates()),
#         asyncio.create_task(bot.get_extra_orders()),
#         asyncio.create_task(bot.dumper()),
#         asyncio.create_task(bot.bumper())
#     ]
#
#     try:
#         await asyncio.gather(*tasks)
#     except KeyboardInterrupt:
#         self.logger.info("🛑 Shutting down...")
#         for task in tasks:
#             task.cancel()