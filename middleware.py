from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery


class AdminMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: list[int]):
        self.admin_ids = admin_ids
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        if user is None or user.id not in self.admin_ids:
            if isinstance(event, Message):
                await event.answer("❌ У вас нет прав для использования этого бота")
            elif isinstance(event, CallbackQuery):
                await event.answer("❌ У вас нет прав для использования этого бота", show_alert=True)
            return

        return await handler(event, data)