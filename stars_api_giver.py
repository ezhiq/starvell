import asyncio
import logging
import math

from pyrogram.errors import FloodWait

import config as gi
from pyrogram import Client
from config_manager import ConfigManager
from constants import default
from datatypes import StarGiftOrder, StarGiftMask
from logger import setup_logging

logger = logging.getLogger('TelegramApiGiver')

class StarsAPIGiver:

    def __init__(self):
        self.sessions = {}
        self.flags = {}
        self.balances = {}
        self.is_active = {}

        self.warnings = {}

        self.api_id = 2040
        self.api_hash = 'b18441a1ff607e10a989891a5462e627'

    async def update_balances(self):
        while True:

            for session in self.sessions:
                self.balances[session] = await self.get_star_balance(session)

            await asyncio.sleep(1000)

    async def init_sessions(self):
        sessions = gi.cc.get('sessions')
        self.sessions = sessions
        for session in sessions:
            self.flags[session] = 0
            self.balances[session] = await self.get_star_balance(session)
            self.is_active[session] = True

        asyncio.create_task(self.update_balances())
        logger.info(f"✅ Сессии инициализированы: {list(self.flags.keys())}")


    async def get_star_balance(self, session):
        async with Client(name=session, api_id=self.api_id, api_hash=self.api_hash) as app:
            stars_balance = await app.get_stars_balance()
            return stars_balance

    async def choose_session(self):

        session = None
        if sum(self.flags.values()) == 0:
            balances = self.balances.copy()
            summa = sum(balances.values())

            for session in self.sessions:
                if self.balances[session] > 0:
                    self.flags[session] = math.ceil(summa / self.balances[session])
                else:
                    self.flags[session] = 0

            for session, balance in balances.items():
                if balance < 100 or not self.is_active[session]:
                    self.flags[session] = 0


        for session, flag in self.flags.items():

            if self.balances[session] < 100 or not self.is_active[session]:
                self.flags[session] = 0
                continue

            if flag != 0:
                self.flags[session] -= 1
                session = session
                return session

        return session

    def get_max(self):
        return max(self.balances.values())

    def get_session_status(self):

        cnt = 0
        for session, status in self.is_active.items():
            if status:
                cnt += 1

        return cnt

    async def do_order(self, order):
        amount = order.amount
        mask = order.mask.mask
        ids = order.mask.ids
        quantity = order.quantity
        username = order.username
        needed = mask

        all_stars = 0

        for am, qu in mask.items():
            all_stars += am * qu  * quantity

        logger.info(f"📦 Начинаем заказ: {username} | {quantity} шт. | {all_stars} звёзд всего")
        logger.info(f"   Маска: {mask}")

        change_session = False
        session = await self.choose_session()

        if session is None:
            return "Отсутствуют сессии выдачи", False

        logger.info(f"   Сессия: {session}")

        for current in range(quantity):
            logger.info(f"   🔄 Итерация {current + 1}/{quantity}")

            success = {13: 0, 21: 0, 43: 0, 85: 0}
            result, ok = None, False

            for attempt in range(5):
                if change_session:
                    session = await self.choose_session()
                    logger.info(f"   🔀 Смена сессии → {session}")

                    if session is None:
                        continue

                    change_session = False

                if self.balances[session] < all_stars:
                    logger.warning(
                        f"   ⚠️ Попытка {attempt + 1}: недостаточно баланса в {session} ({self.balances[session]} < {all_stars})")
                    change_session = True
                    continue

                cur = {item: needed[item] - success[item] for item in success}
                logger.info(f"   Попытка {attempt + 1}: отправляем {cur}")

                result, ok, success = await self.send_gifts(session, cur, success, ids, username)

                if ok:
                    logger.info(f"   ✅ Итерация {current + 1} успешна. Прогресс: {success}")
                    break
                else:
                    logger.error(f"   ❌ Попытка {attempt + 1} провалилась: {result}")
                    change_session = True

            if not ok:
                logger.error(f"❌ Заказ провален на итерации {current + 1}: {result}")
                return 'Нам не удалось выдать ваш заказ - возвращаем деньги, извините', False

        logger.info(f"✅ Заказ выполнен: {username} | {quantity} шт.")
        return 'Выдали', True

    async def wait_for_floodwait(self, session, x):
        await asyncio.sleep(x)
        self.is_active[session] = True

    async def send_gifts(self, session, cur, success, ids, username):
        logger.info(f"   🎁 send_gifts: сессия={session} цель={username}")

        async with Client(session, self.api_id, self.api_hash) as app:
            for item, kolvo in cur.items():
                for idx in range(kolvo):
                    try:

                        try:
                            gift_id = ids[item][idx]
                        except (KeyError, IndexError):

                            try:
                                gift_id = ids[item][0]
                            except (KeyError, IndexError):
                                gift_id = default[item]

                        await app.send_gift(chat_id=username, gift_id=gift_id)
                        success[item] += 1
                        self.balances[session] -= item
                        logger.info(f"   ✅ Подарок {gift_id} (тип {item}) → {username}")

                    except (KeyError, IndexError) as e:
                        logger.error(f"   ❌ Ошибка конфига при выдаче типа {item}[{idx}]: {e}")
                        return 'Ошибка при выдаче в конфиге у владельца бота, извините', False, success

                    except FloodWait as e:
                        logger.error(f"   ❌ FloodWait на сессию при выдаче типа {item}[{idx}]: {e}")
                        self.flags[session] = 0
                        self.is_active[session] = False
                        asyncio.create_task(self.wait_for_floodwait(session, e.value))
                        return f'Флудвейт при выдаче {e}', False, success

                    except Exception as e:
                        logger.error(f"   ❌ Неизвестная ошибка на сессию при выдаче типа {item}[{idx}]: {e}")
                        self.flags[session] = 0
                        self.is_active[session] = False
                        return f"Неизвестная ошибка при выдаче {e}", False, success

        return 'Ок', True, success


async def main(order):
    api = StarsAPIGiver()
    await api.init_sessions()  # ← сначала инициализация
    await api.do_order(order)  # ← потом заказ

if __name__ == "__main__":

    cc = ConfigManager()
    logger = setup_logging()
    id = "13stars"
    data = cc.find_by_key(id)['data']['special_data']
    stars_amount = data['stars']

    mask = data['mask']
    ids = data['ids']

    order = StarGiftOrder(
        name=id,
        game="stargifts",
        order_id="customorder",
        amount=stars_amount,
        quantity=2,
        user_id=35356654,
        username="ezhiqq",
        chat_id='343',
        mask=StarGiftMask(
            order_id="customorder",
            mask=mask,
            ids=ids
        )
    )

    asyncio.run(main(order))



