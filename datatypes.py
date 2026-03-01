from constants import *


def get_label(action: str) -> str:
    return labels.get(action, action)


def get_hint(action: str) -> str:
    return hints.get(action, "")

GIFT_TYPES = [13, 21, 43, 85]

def do_mask(mask_list: list) -> dict:
    return {GIFT_TYPES[i]: mask_list[i] for i in range(len(GIFT_TYPES))}

def do_ids(ids_list: list) -> dict:
    result = {}
    for i, gift_type in enumerate(GIFT_TYPES):
        raw = ids_list[i] if i < len(ids_list) else []
        result[gift_type] = [default[gift_type] if v == 'def' else v for v in raw]
        if not result[gift_type]:
            result[gift_type] = [default[gift_type]]
    return result

class FragmentOrder:

    """
    name - уникальный идентификатор заказа, размещается в описании
    game - раздел на старвелле (определяем как отвечать как выдавать и как сосать хуй)
    order_id - айди зашифр
    amount - КОЛИЧЕСТВО ЗВЕЗД
    quantity - СКОЛЬКО РАЗ ВЫДАЕМ AMOUNT
    user_id - юзер айди на старвелле
    username - НА КАКОЙ ЮЗЕР ВЫДАЕМ
    chat_id - хуй знает что это
    """

    def __init__(self, name: str, game: str, order_id: str, amount: int, quantity: int, user_id: int, username: str, chat_id: str = None):
        self.name = name
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
        self.game = game

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

class StarGiftMask:
    """
    order_id - шифр айди ордера
    mask - МАСКА ВИДА {13: x, 21: y, 43: z, 85: w}
    ids - СЛОВАРЬ ВИДА {13: [id подарка], 21: [def], 43: [def], 85: [def]}
    """
    def __init__(self, order_id: str, mask: list, ids=None):
        self.order_id = order_id
        self.mask = do_mask(mask)
        self.ids = do_ids(ids)

class StarGiftOrder:
    """
    name - уникальный идентификатор заказа, размещается в описании
    game - раздел на старвелле (определяем как отвечать как выдавать и как сосать хуй)
    order_id - айди зашифр
    amount - КОЛИЧЕСТВО ЗВЕЗД
    quantity - СКОЛЬКО РАЗ ВЫДАЕМ AMOUNT
    user_id - юзер айди на старвелле
    username - НА КАКОЙ ЮЗЕР ВЫДАЕМ
    mask - объект StarGiftMask для заказа
    chat_id - хуй знает что это
    """

    def __init__(self, name: str, game: str, gift_name: str, order_id: str, amount: int, quantity: int, user_id: int, username: str, mask: StarGiftMask, chat_id: str = None):
        self.name = name
        self.order_id = order_id
        self.gift_name = gift_name
        self.amount = amount
        self.quantity = quantity
        self.user_id = user_id
        self.username = username
        self.chat_id = chat_id
        self.status = "оплачен"  # CREATED, PROCESSING, COMPLETED, REFUNDED
        self.created_at = None
        self.completed_at = None
        self.refunded_at = None
        self.mask = mask
        self.game = game

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