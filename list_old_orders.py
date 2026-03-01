import asyncio
import logging
import re
from datetime import datetime, timezone

import aiogram
import aiohttp
from aiogram import Bot

from config_manager import ConfigManager


class StarvellBot:
    def __init__(self, cc, bot, admin):

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

        self.bot = bot
        self.cc = cc
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

        self.ws_connected = asyncio.Event()

        self.event_pattern = re.compile(r'^42(/[\w\-]+)?,?\[(.+)\]$')\

        self.categories = []
        self.game_id = None

    async def get_all_orders_12h(self):
        resp = await self.get_orders()

        if resp:
            now = datetime.now(timezone.utc)
            old_orders = []

            for order in resp:
                created_at = datetime.fromisoformat(order['createdAt'].replace('Z', '+00:00'))
                age = now - created_at

                if age.total_seconds() > 12 * 3600:
                    old_orders.append(f"#{order['id'].split('-')[-1][-8:].upper()}")

            if old_orders:
                text = "⚠️ Заказы старше 12 часов:\n\n" + "\n".join(old_orders)
            else:
                text = "✅ Нет зависших заказов"

            for admin in self.admin:
                await self.bot.send_message(chat_id=admin, text=text)


    async def get_orders(self):

        url = "https://starvell.com/api/orders/list"
        headers = self.headers
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
            async with session.post(url, headers=headers, json=payload) as response:

                json_data = await response.json()
                if response.status == 200:
                    return json_data
                else:
                    print('ошибка, сорян')
                    return None


async def main():
    BOT_TOKEN = "8298689795:AAEuT7KxgcxPPKfYBE2JFkup_yNj-0gfH6k" #БОТ ТОКЕН
    ADMIN_IDS = [1094682920]  # Замените на ваши Telegram ID

    bot = Bot(token=BOT_TOKEN)
    cc = ConfigManager()
    ss = StarvellBot(cc, bot, ADMIN_IDS)
    dp = aiogram.Dispatcher()

    await asyncio.gather(
        dp.start_polling(bot),
        ss.get_all_orders_12h()
    )



if __name__ == '__main__':
    asyncio.run(main())
