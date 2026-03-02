# -*- coding: utf-8 -*-
import json
import asyncio
import logging
import re
import traceback
from pathlib import Path
from threading import Lock
from datatypes import *
from fragment_api import FragmentConfig
logger = logging.getLogger(__name__)

# ============================================================
# РЕЕСТР ПОЛЕЙ — единственное место для добавления новых полей
# ============================================================
# section  : раздел в JSON (для группировки)
# type     : Python-тип (str / int / float / bool / list)
# label    : название для UI
# hint     : подсказка пользователю
# sensitive: маскировать значение в UI
#
# Чтобы добавить поле — одна строка здесь ↓
# ============================================================
FIELDS: dict[str, dict] = {
    # --- Starvell ---
    "cookie_session"       : {"section": "starvell",    "type": str,   "label": "Session Cookie",             "hint": "Cookie сессии для Starvell",                "sensitive": True},
    "my_id"                : {"section": "starvell",    "type": int,   "label": "User ID",                    "hint": "ID пользователя на Starvell"},
    "friends"              : {"section": "starvell",    "type": list,  "label": "Список друзей",              "hint": "Введите список друзей через пробел, кого не будем перебивать",           "sub": int},

    # --- Fragment ---
    "fragment_hash"        : {"section": "fragment",    "type": str,   "label": "Fragment Hash",              "hint": "Hash для Fragment API",                     "sensitive": True},
    "fragment_cookie"      : {"section": "fragment",    "type": str,   "label": "Fragment Cookie",            "hint": "Cookie для Fragment API",                   "sensitive": True},
    "fragment_show_sender" : {"section": "fragment",    "type": str,   "label": "Show Sender",                "hint": "Показывать отправителя: 0 или 1"},

    # --- TON ---
    "ton_api_key"          : {"section": "ton",         "type": str,   "label": "TON API Key",                "hint": "API ключ для TON API",                      "sensitive": True},
    "mnemonic"             : {"section": "ton",         "type": list,  "label": "Wallet Mnemonic",            "hint": "24 слова через пробел",                     "sensitive": True,           "sub": str},
    "destination_address"  : {"section": "ton",         "type": str,   "label": "Destination Address",        "hint": "Адрес кошелька получателя (или 'null')"},
    "is_testnet"           : {"section": "ton",         "type": bool,  "label": "Testnet Mode",               "hint": "true / false"},

    # --- Messages ---
    "universal"            : {"section": "messages",    "type": str,   "label": "Универсальный ответ",        "hint": "Текст ответа на отзывы"},
    "refund_msg"           : {"section": "messages",    "type": str,   "label": "Сообщение о возврате",       "hint": "Текст при возврате средств"},
    "hello"                : {"section": "messages",    "type": str,   "label": "Приветствие",                "hint": "Приветственное сообщение для клиентов"},
    "thx_msg"              : {"section": "messages",    "type": str,   "label": "Благодарность",              "hint": "Благодарность за подтверждение заказа"},

    # --- Performance ---
    "min_star_rate"        : {"section": "performance", "type": float, "label": "Минимальный курс звезды",    "hint": "Например: 1.15"},
    "max_concurrent_orders": {"section": "performance", "type": int,   "label": "Макс. одновременных заказов","hint": "Например: 50"},
    "is_online"            : {"section": "performance", "type": int,   "label": "Демпинг по онлайну",         "hint": "0 — все заказы, 1 — только онлайн"},

    # --- TG API ---
    "sessions"             : {"section": "tgapi",       "type": list,  "label": "Сессии для выдачи",          "hint": "в строчку все сессии просто"},
}


#
# ADVANCED = {
#     'starorders'          : {"section": "advanced",    "type": dict,  "label": "Помечание ордеров",          "hint": "Введенные айди в будущем будем использовать при создании на starvell для конкретного обнаружения в категории другое количество", "sub": [str, int]}
# }
# steps_to_get = {
#     'advanced': {
#         'starorders': {
#             1: {"section": "advanced",    "type": [[str], [dict]],   "label": "Доступные разделы", "hint": "Какой раздел будем редачить?", "actions": [Action('add', 'Введите название нового раздела'), Action('back')],
#                 "stars": {"section": "advanced",    "type": [[str], [dict]],  "label": "Ваши id предложений", "hint": "Выберите предложение для работы или создайте новое", "actions": [Action('add'), Action('remove'), Action('back')]}
#                 "advgifts"},
#         }
#     }
# }

# Человекочитаемые названия секций для UI
SECTION_LABELS: dict[str, str] = {
    "starvell"   : "🔐 Starvell API",
    "fragment"   : "⭐ Fragment API",
    "ton"        : "💎 TON Wallet",
    "messages"   : "💬 Сообщения",
    "performance": "⚙️ Производительность",
    "tgapi"      : "Работа с тг апи"
}

def parse_schema(value: str) -> tuple[dict, bool]:
    """stars:int mask:list[int] ids:list[list] → {"stars": "int", ...}"""
    VALID_TYPES = {'int', 'float', 'str', 'bool', 'list', 'list[int]', 'list[str]', 'list[list]', 'list[float]'}
    result = {}
    for part in value.strip().split():
        if ':' not in part:
            return {}, False
        key, type_str = part.split(':', 1)
        if type_str not in VALID_TYPES:
            return {}, False
        result[key] = type_str
    return result, True

def validate_by_schema(value: dict, schema: dict) -> tuple[bool, str]:
    """
    Валидирует dict по схеме вида:
    {"stars": "int", "mask": "list[int]", "ids": "list[list]"}
    """
    TYPE_MAP = {
        'int': int,
        'float': float,
        'str': str,
        'bool': bool,
        'list': list,
        'dict': dict,
    }

    for field, type_str in schema.items():
        if field not in value:
            return False, f"Отсутствует обязательное поле: {field!r}"

        val = value[field]
        type_str = type_str.strip()

        # list[int], list[str], list[list] и т.д.
        if type_str.startswith('list[') and type_str.endswith(']'):
            if not isinstance(val, list):
                return False, f"Поле {field!r}: ожидается list, получено {type(val).__name__}"

            inner_type_str = type_str[5:-1]  # достаём то что внутри list[...]
            inner_type = TYPE_MAP.get(inner_type_str)

            if inner_type and inner_type is not list:
                for i, el in enumerate(val):
                    if not isinstance(el, inner_type):
                        return False, f"Поле {field!r}[{i}]: ожидается {inner_type_str}, получено {type(el).__name__} ({el!r})"
            elif inner_type is list:
                for i, el in enumerate(val):
                    if not isinstance(el, list):
                        return False, f"Поле {field!r}[{i}]: ожидается list, получено {type(el).__name__} ({el!r})"

        else:
            expected_type = TYPE_MAP.get(type_str)
            if expected_type is None:
                return False, f"Неизвестный тип в схеме: {type_str!r}"
            if not isinstance(val, expected_type):
                return False, f"Поле {field!r}: ожидается {type_str}, получено {type(val).__name__} ({val!r})"

    return True, "ok"

def parse_value(value: str):
    """
    Парсит строку от пользователя в Python объект.
    Поддерживает:
    - JSON: {"stars": 50, "mask": [0,0,1,0]}
    - key:value формат: stars: 50, mask: [0,0,1,0]
    - Вложенные списки: ids: [["def"], [434645]]
    Возвращает (parsed_value, True) или (error_str, False)
    """
    value = value.strip()

    # Попытка 1: чистый JSON (dict или list)
    try:
        parsed = json.loads(value)
        return parsed, True
    except json.JSONDecodeError:
        pass

    # Попытка 2: key: value формат
    # Нормализуем переносы строк в запятые
    normalized = value.replace('\n', ', ')
    result = {}

    # Разбиваем только по запятым НЕ внутри скобок
    parts = _split_by_comma_top_level(normalized)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if ':' not in part:
            return f'Неверный формат: {part!r}. Ожидается "ключ: значение"', False

        k, v = part.split(':', 1)
        k, v = k.strip(), v.strip()

        if not k:
            return 'Пустой ключ', False

        # Парсим значение
        parsed_v, ok = _parse_single_value(v)
        if not ok:
            return f'Не удалось распарсить значение {v!r} для ключа {k!r}', False

        result[k] = parsed_v

    if not result:
        return 'Пустой ввод', False

    return result, True


def _split_by_comma_top_level(s: str) -> list:
    """Разбивает строку по запятым, игнорируя запятые внутри [] и {}"""
    parts = []
    depth = 0
    current = []
    for ch in s:
        if ch in '[{':
            depth += 1
            current.append(ch)
        elif ch in ']}':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts


def _parse_single_value(v: str):
    v = v.strip()

    # Убираем обрамляющие кавычки если пользователь написал "on" вместо on
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1], True

    if v.startswith('[') or v.startswith('{'):
        # Оборачиваем unquoted слова в кавычки внутри списка
        # def → "def", но не трогаем числа, true, false, null
        def quote_unquoted(s):
            return re.sub(
                r'(?<!["\w])([a-zA-Z_]\w*)(?!["\w])',
                lambda m: m.group(1) if m.group(1) in ('true', 'false', 'null') else f'"{m.group(1)}"',
                s
            )

        try:
            return json.loads(v), True
        except json.JSONDecodeError:
            try:
                return json.loads(quote_unquoted(v.replace("'", '"'))), True
            except json.JSONDecodeError:
                return f'Невалидный JSON: {v!r}', False

    if v.lower() == 'true':
        return True, True
    if v.lower() == 'false':
        return False, True
    if v.lower() in ('null', 'none'):
        return None, True

    try:
        return int(v), True
    except ValueError:
        pass

    try:
        return float(v), True
    except ValueError:
        pass

    return v, True

def cast(value, target_type, el_type=None):
    import re
    try:

        if isinstance(value, target_type):
            return value, True

        if target_type is bool:
            if isinstance(value, bool):
                return value, True
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes", "да"):
                    return True, True
                if value.lower() in ("false", "0", "no", "нет"):
                    return False, True
            return None, False

        if target_type is list:
            # Сначала пробуем json.loads для вложенных структур
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed, True
                except (json.JSONDecodeError, ValueError):
                    pass

            try:
                rez = value.replace('[', '').replace(']', '').replace(',', '').split()
            except:
                rez = str(value).split()

            if not isinstance(rez, list):
                rez = str(value).split()

            if el_type:
                try:
                    rez = list(map(el_type, rez))
                except TypeError:
                    return rez, False

            return rez, True

        if target_type is dict:
            if isinstance(value, dict):
                return value, True

            if target_type is dict:
                if isinstance(value, dict):
                    return value, True

            if isinstance(value, str):
                # Пробуем json напрямую
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        return parsed, True
                except (json.JSONDecodeError, ValueError):
                    pass

                # Конвертируем "key: value, key2: value2" → json
                # Заменяем одинарные кавычки на двойные для json.loads
                try:
                    json_str = value.strip()
                    if not json_str.startswith('{'):
                        json_str = '{' + json_str + '}'
                    json_str = json_str.replace("'", '"')
                    # Добавляем кавычки вокруг ключей: word: → "word":
                    json_str = re.sub(r'(\b\w+\b)\s*:', r'"\1":', json_str)
                    # Убираем двойные кавычки если уже были: ""key"" → "key"
                    json_str = re.sub(r'""(\w+)""', r'"\1"', json_str)
                    parsed = json.loads(json_str)
                    if isinstance(parsed, dict):
                        return parsed, True
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(f"[cast dict] json error: {e}")
                    pass

            # Старый regex-парсер как fallback
            import re
            result = {}
            pattern = r'(\w+)\s*:\s*(\[[^\]]*\]|[^,]+?)(?=\s*,\s*\w+\s*:|$)'
            matches = re.findall(pattern, str(value).strip())

            if not matches:
                return None, False

            for key, val in matches:
                key = key.strip()
                val = val.strip()
                if val.startswith('[') and val.endswith(']'):
                    inner = val[1:-1].replace(',', '').split()
                    try:
                        inner = [int(x) for x in inner]
                    except ValueError:
                        try:
                            inner = [float(x) for x in inner]
                        except ValueError:
                            pass
                    result[key] = inner
                else:
                    try:
                        result[key] = int(val)
                    except ValueError:
                        try:
                            result[key] = float(val)
                        except ValueError:
                            result[key] = val

            return result, True

        return target_type(value), True
    except (ValueError, TypeError):
        return None, False


class ConfigManager:

    fields = FIELDS
    section_labels = SECTION_LABELS
    def __init__(self, path: str = "config_data.json", reload_interval: int = 0):
        self.fields = FIELDS
        self.section_labels = SECTION_LABELS
        self.path = Path(path)
        self._lock = Lock()
        self._last_mtime = 0
        self.data: dict = {}
        self.reload_interval = reload_interval
        self.load()

    # ----------------------------------------------------------
    # I/O
    # ----------------------------------------------------------

    def load(self):
        with self._lock:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                self._last_mtime = self.path.stat().st_mtime
                logger.info(f"✅ Конфигурация загружена из {self.path}")
            else:
                logger.warning(f"Файл {self.path} не найден, создаём пустой конфиг")
                self.data = {}

    def save(self):
        with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
            self._last_mtime = self.path.stat().st_mtime
            logger.info(f"✅ Конфигурация сохранена в {self.path}")

    def start_watcher(self):
        asyncio.create_task(self._watcher())

    async def _watcher(self):
        while True:
            try:
                mtime = self.path.stat().st_mtime
                if mtime != self._last_mtime:
                    logger.info("🔄 Config reloaded from disk")
                    self.load()
            except Exception as e:
                logger.error(f"Config watcher error: {e}")
            await asyncio.sleep(self.reload_interval or 5)

    # ----------------------------------------------------------
    # Чтение
    # ----------------------------------------------------------

    def get_parent(self, target_key: str, data: dict = None, _current_key: str = None) -> str | None:
        """Найти ключ родителя для target_key в cc.data['advanced']"""
        if data is None:
            data = self.data.get('advanced', {})

        for key, value in data.items():
            if key == 'data':
                continue
            if key == target_key:
                return _current_key
            if isinstance(value, dict):
                result = self.get_parent(target_key, value, key)
                if result is not None:
                    return result

        return None

    def find_by_key(self, target_key: str, data: dict = None) -> dict | None:
        """Найти значение по ключу рекурсивно в cc.data['advanced'], пропуская 'data'"""
        if data is None:
            data = self.data.get('advanced', {})

        for key, value in data.items():
            if key == 'data':
                continue
            if key == target_key:
                return value
            if isinstance(value, dict):
                result = self.find_by_key(target_key, value)
                if result is not None:
                    return result

        return None

    def get(self, key: str, default=None):
        """Получить значение по плоскому ключу."""
        meta = self.fields.get(key)
        if not meta:
            meta = self.fields_adv.get(key)
        if not meta:
            return default
        section = meta["section"]
        with self._lock:
            return self.data.get(section, {}).get(key, default)

    def get_section(self, section: str) -> dict:
        """Получить весь раздел."""
        with self._lock:
            return self.data.get(section, {}).copy()

    def all(self) -> dict:
        """Полная копия конфига."""
        with self._lock:
            return {k: v.copy() if isinstance(v, dict) else v for k, v in self.data.items()}

    # ----------------------------------------------------------
    # Запись
    # ----------------------------------------------------------

    def get_type(self, type_name):
        if type(type_name) == str:
            mapping = {
                'string': str,
                'int': int,
                'float': float,
                'bool': bool,
                'list': list,
                'dict': dict,
            }
            return mapping.get(type_name, str)

        elif type(type_name) == list:
            return [self.get_type(item) for item in type_name]

        else:
            return type_name

    def edit_dict(self, keys: list, key_type, val_type, raw_value=None) -> tuple[bool, object]:
        try:
            cast_keys = [key_type(k) for k in keys]
        except (ValueError, TypeError) as e:
            return False, f"Неверный тип ключа в пути: {e}"

        with self._lock:
            *parents, last = cast_keys
            current = self.data
            for k in parents:
                if not isinstance(current, dict):
                    return False, f"Ключ '{k}' не является словарём"
                current = current.setdefault(k, {})

            if raw_value is None:
                if last not in current:
                    return False, f"Ключ '{last}' не найден"
                del current[last]
            else:
                # Если значение уже готовый Python объект — пишем напрямую
                if not isinstance(raw_value, str):
                    current[last] = raw_value
                else:
                    # Если val_type указан и это простой тип — используем старый cast для надёжности
                    if val_type in (str, int, float, bool):
                        cast_val, ok = cast(raw_value, val_type)
                        if not ok:
                            return False, f"Ожидается {val_type.__name__}, получено: {raw_value!r}"
                        current[last] = cast_val
                    else:
                        # dict, list или None — через parse_value
                        parsed, ok = parse_value(raw_value)
                        if not ok:
                            return False, f"Ошибка парсинга: {parsed}"
                        current[last] = parsed

        self.save()
        return True, raw_value

    def edit(self, key: str, raw_value) -> tuple[bool, object]:
        """
        Универсальный метод изменения любого поля.
        Возвращает (True, cast_value) или (False, error_str).
        """
        meta = self.fields.get(key)
        if not meta:
            return False, f"Неизвестное поле: {key!r}"

        sub = None
        if "sub" in meta:
            sub = meta["sub"]

        cast_value, ok = cast(raw_value, meta["type"], sub)
        if not ok:
            return False, f"Ожидается {meta['type'].__name__}, получено: {raw_value!r}. {meta.get('hint', '')}"

        section = meta["section"]
        with self._lock:
            if section not in self.data:
                self.data[section] = {}
            self.data[section][key] = cast_value

        self.save()
        return True, cast_value

    # ----------------------------------------------------------
    # Helpers для UI
    # ----------------------------------------------------------

    def sections(self) -> list[str]:
        """Список секций в порядке SECTION_LABELS."""
        return list(self.section_labels.keys())

    def fields_in_section(self, section: str) -> dict[str, dict]:
        """Все поля из FIELDS, принадлежащие секции."""
        return {k: v for k, v in self.fields.items() if v["section"] == section}