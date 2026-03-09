# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import re
import traceback
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import cc
import config as gi
from config_manager import cast, validate_by_schema, parse_schema, parse_value
from constants import allowed_fields
from datatypes import get_label

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

router = Router()


class ConfigEditStates(StatesGroup):
    waiting_for_value = State()
    waiting_for_value_advanced = State()


# ----------------------------------------------------------
# HELPERS
# ----------------------------------------------------------

def _check_admin(user_id: int, admin_ids: list) -> bool:
    return user_id in admin_ids

def _build_advanced_back(section_id) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"adv_back_{section_id}"))
    return keyboard.as_markup()

def _build_services_keyboard() -> InlineKeyboardMarkup:
    status = gi.service_manager.get_all_status()
    buttons = []
    for service_id, info in status.items():
        emoji = "🟢" if info['running'] else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{emoji} {info['name']}",
            callback_data=f"service_select_{service_id}"
        )])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="service_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _build_service_control_keyboard(service_id: str, is_running: bool) -> InlineKeyboardMarkup:
    buttons = []
    if is_running:
        buttons.append([InlineKeyboardButton(text="⏸ Остановить",    callback_data=f"service_stop_{service_id}")])
        buttons.append([InlineKeyboardButton(text="🔄 Перезапустить", callback_data=f"service_restart_{service_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="▶️ Запустить",     callback_data=f"service_start_{service_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="service_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _build_advanced_keyboard_config() -> InlineKeyboardMarkup:
    buttons = []
    for section_id, dic in cc.fields_adv.items():
        buttons.append([InlineKeyboardButton(
            text=f"{dic['label']}",
            callback_data=f"adv_1_section_{section_id}"
        )])
    buttons.append([InlineKeyboardButton(
        text="◀️ Назад",         callback_data="config_back"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def make_path(keys):
    return "-".join(str(key) for key in keys)

def _build_advanced_keyboard(keys, actions=None) -> InlineKeyboardMarkup:
    """
    ЕДИНЫЙ ФОРМАТ КНОПКИ adv_action_path
    :param keys:
    :param actions:
    :return:
    """
    path = make_path(keys)
    back_path = make_path(keys[:-1])  # убираем последний элемент

    keyboard = InlineKeyboardBuilder()
    meta = cc.data.copy()

    if actions is None:
        for key in keys:
            try:
                meta = meta[key]
            except KeyError:
                traceback.print_exc()
                keyboard.row(InlineKeyboardButton(text="Не удалось загрузить данные - несуществующий путь по ключам", callback_data=f"adv_back_{back_path}"))
                return keyboard.as_markup()

        data = meta['data']
        others = meta.copy()
        del others['data']

        if cc.get_type(data['type'][1]) == [dict]:
            for section in others.keys():
                keyboard.row(InlineKeyboardButton(
                    text=f"{section}", callback_data=f"adv_section_{path}-{section}"
                ))

        for action in data['actions']:
            keyboard.row(InlineKeyboardButton(
                text=f"{get_label(action[0])}", callback_data=f"adv_{action[0]}_{path}"
            ))


        if keys == ['advanced']:
            keyboard.row(InlineKeyboardButton(
                text="◀️ Назад", callback_data="config_back"
            ))

        else:
            keyboard.row(InlineKeyboardButton(
                text="◀️ Назад", callback_data=f"adv_back_{back_path}"
            ))

    else:

        for action in actions:
            if action[0] == 'remove' and action[1] == 'Подтверждение':
                keyboard.row(InlineKeyboardButton(
                    text=f"Да", callback_data=f"adv_{action[0]}true_{path}"
                ))

                keyboard.row(InlineKeyboardButton(
                    text="Нет", callback_data=f"adv_section_{path}"
                ))

            else:
                keyboard.row(InlineKeyboardButton(
                    text=get_label(action[0]), callback_data=f"adv_{action[0]}_{path}"
                ))

    return keyboard.as_markup()


def _build_advanced_menu(section_id) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="➕ Добавить", callback_data=f"adv_add_{section_id}"))
    keyboard.row(InlineKeyboardButton(text="➖ Удалить",  callback_data=f"adv_remove_{section_id}"))
    keyboard.row(InlineKeyboardButton(text="◀️ Назад",         callback_data="config_back"))
    return keyboard.as_markup()


def _build_config_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for section_id, section_label in cc.section_labels.items():
        buttons.append([InlineKeyboardButton(
            text=section_label,
            callback_data=f"config_section_{section_id}"
        )])
    buttons.append([InlineKeyboardButton(
        text='Продвинутые настройки',
        callback_data=f"adv_section_advanced"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _build_section_keyboard(section_id: str) -> InlineKeyboardMarkup:
    buttons = []
    for key, meta in cc.fields_in_section(section_id).items():
        buttons.append([InlineKeyboardButton(
            text=meta["label"],
            callback_data=f"edit_field:{key}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="config_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_add_msg(step, field, hint, unique_hints=None):

    if unique_hints is None:
        unique_hints = {}

    msg_text = (f'Добавление нового поля <b>{field}</b>\n\n'
                f'Подсказка - {hint}\n\n'
                f'{f'Уникальная подсказка:\n<code>{unique_hints[field]}</code>\n\n' if field in unique_hints else ''}'
                f'Оставить без значения: none или -')

    return msg_text

def process_add_msg(keys, field, value, pos=None):

    fields_types = {
        'actions': list,
        'special_data': dict,
        'label': str,
        'schema': dict,
        'hint': str,
        'id': str,
    }

    key_type = str
    cur = keys.copy()

    if value.lower() in ('none', '-'):
        return 'Отсутствует', True

    if field == 'id':

        if cc.find_by_key(value) is not None:
            return f'Имя {value!r} уже существует в конфиге', False

        if '_' in value:
            return 'Недопустимый формат - нельзя использовать _ в ключах', False

        default_data = {
            "type": [["string"], ["dict"]],
            "label": value,
            "hint": "",
            "actions": [
                ["add", "Добавить"],
                ["remove", "Удалить"]
            ],
            "special_data": {}
        }
        cur.append(value)
        cur.append('data')
        ok, result = cc.edit_dict(cur, key_type, dict, default_data)
        if ok:
            return value, True
        else:
            return 'Ошибка при создании раздела', False

    # для остальных полей путь: keys + [pos, 'data', field]
    if pos is None:
        return 'Отсутствует pos', False

    cur.append(pos)
    cur.append('data')
    cur.append(field)

    if field == 'actions':
        actions = []
        for line in value.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" - ", 1)
            if len(parts) != 2:
                return f'Неверный формат строки: {line!r}\nОжидается: "action - описание"', False
            action, description = parts[0].strip(), parts[1].strip()
            if action not in {'add', 'remove'}:
                return f'Неизвестное действие: {action!r}', False
            if not description:
                return f'Пустое описание для {action!r}', False
            actions.append([action, description])

        ok, result = cc.edit_dict(cur, key_type, list, actions)

    elif field == 'special_data':
        cast_val, ok = parse_value(value)
        if not ok:
            return f'Ошибка парсинга: {cast_val}', False
        if not isinstance(cast_val, dict):
            return 'Ожидается словарь. Пример: stars: 50, mask: [0,0,1,0]', False

        # Читаем схему
        schema = None
        try:
            node = cc.data.copy()
            for k in keys:
                node = node[k]
            schema = node['data'].get('schema')
        except (KeyError, TypeError):
            pass

        if schema:
            valid, err = validate_by_schema(cast_val, schema)
            if not valid:
                return f'Ошибка валидации: {err}', False

        ok, result = cc.edit_dict(cur, str, dict, cast_val)
        if ok:
            return cast_val, True
        return f'Ошибка записи: {result}', False

    elif field == 'schema':
        schema_val, ok = parse_schema(value)
        if not ok:
            return 'Неверный формат схемы. Пример: stars:int mask:list[int] ids:list[list]', False
        ok, result = cc.edit_dict(cur, key_type, dict, schema_val)

    else:
        ok, result = cc.edit_dict(cur, key_type, fields_types[field], value)

    if ok:
        return result, True
    else:
        return 'Ошибка при записи данных в конфиг', False

# ----------------------------------------------------------
# КОМАНДЫ
# ----------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🤖 <b>StarvellBot Manager</b>\n\n"
        "Добро пожаловать в панель управления!\n\n"
        "/services — управление службами\n"
        "/config — редактирование конфигурации\n"
        "/status — статус всех служб\n"
        "/stats - статистика по продажам и сессиям\n"
        "/list - список неподтвержденных заказов старше 12ч\n"
        "/help — справка",
        parse_mode="HTML"
    )

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    import sqlite3

    try:
        conn = sqlite3.connect("orders.db")
        cursor = conn.cursor()

        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE way = 'fragment' AND status = 'закрыт'")
        fragment_stars = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE way = 'api' AND status = 'закрыт'")
        api_stars = cursor.fetchone()[0]

        conn.close()
    except Exception as e:
        await message.answer(f"❌ Ошибка базы данных: {e}", parse_mode="HTML")
        return

    text = "📊 <b>Статистика</b>\n\n"
    text += "⭐ <b>Выдано звёзд:</b>\n"
    text += f"   Fragment: <b>{fragment_stars}</b>\n"
    text += f"   API: <b>{api_stars}</b>\n"
    text += f"   Итого: <b>{fragment_stars + api_stars}</b>\n\n"

    api_giver = getattr(gi, 'api_giver', None)
    if api_giver:
        text += "🔌 <b>Сессии выдачи:</b>\n"
        for session, is_active in api_giver.is_active.items():
            emoji = "🟢" if is_active else "🔴"
            balance = api_giver.balances.get(session, "?")
            floodwait = api_giver.floodwaits.get(session)
            fw_text = f" | ⏳ FloodWait: {floodwait}s" if floodwait else ""
            text += f"   {emoji} <code>{session}</code> — {balance}⭐{fw_text}\n"

        active = sum(1 for v in api_giver.is_active.values() if v)
        total = len(api_giver.is_active)
        text += f"\nАктивных: <b>{active}/{total}</b>\n"

        if api_giver.balances:
            total_balance = sum(api_giver.balances.values())
            max_balance = max(api_giver.balances.values())
            min_balance = min(api_giver.balances.values())
            text += f"\n💰 <b>Балансы сессий:</b>\n"
            text += f"   Суммарно: <b>{total_balance}⭐</b>\n"
            text += f"   Макс: <b>{max_balance}⭐</b> | Мин: <b>{min_balance}⭐</b>\n"

        floodwaits = {s: fw for s, fw in api_giver.floodwaits.items() if fw}
        if floodwaits:
            text += "\n⚠️ <b>Флудвейты:</b>\n"
            for session, fw in floodwaits.items():
                text += f"   <code>{session}</code>: {fw}s\n"
    else:
        text += "🔌 <b>Сессии:</b> API Giver не инициализирован\n"

    await message.answer(text, parse_mode="HTML")

@router.message(Command("list"))
async def cmd_list(message: Message):
    text = await gi.service_manager.bot_instance.get_all_orders_12h()
    await message.answer(text)

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🤖 <b>Справка по командам</b>\n\n"
        "<b>Управление службами:</b>\n"
        "/services — панель управления службами\n"
        "/status — статус всех служб\n\n"
        "<b>Конфигурация:</b>\n"
        "/config — редактировать настройки\n\n"
        "<b>Редактирование конфига:</b>\n"
        "Выберите раздел → параметр → введите новое значение.\n"
        "Изменения сохраняются автоматически.",
        parse_mode="HTML"
    )

@router.message(Command("status"))
async def cmd_status(message: Message):
    status = gi.service_manager.get_all_status()
    text = "📊 <b>Статус служб:</b>\n\n"

    for service_name, info in status.items():
        emoji = "🟢" if info['running'] else "🔴"
        text += f"{emoji} <b>{service_name}</b>\n"
        text += f"   Статус: {'Запущена' if info['running'] else 'Остановлена'}\n"

        if info['running'] and info['started_at']:
            uptime = datetime.now() - info['started_at']
            h = int(uptime.total_seconds() // 3600)
            m = int((uptime.total_seconds() % 3600) // 60)
            text += f"   Uptime: {h}ч {m}м\n"

        if info['last_error']:
            text += f"   ⚠️ Ошибка: {info['last_error']}\n"
        text += "\n"

    await message.answer(text, parse_mode="HTML")

@router.message(Command("services"))
async def cmd_services(message: Message):
    await message.answer(
        "⚙️ <b>Управление службами</b>\n\nВыберите службу:",
        reply_markup=_build_services_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("config"))
async def cmd_config(message: Message):
    await message.answer(
        "⚙️ <b>Редактирование конфигурации</b>\n\nВыберите раздел:",
        reply_markup=_build_config_keyboard(),
        parse_mode="HTML"
    )


# ----------------------------------------------------------
# СЛУЖБЫ
# ----------------------------------------------------------

@router.callback_query(F.data == "service_refresh")
async def cb_service_refresh(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=_build_services_keyboard())
    await callback.answer("✅ Обновлено")

@router.callback_query(F.data == "service_back")
async def cb_service_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚙️ <b>Управление службами</b>\n\nВыберите службу:",
        reply_markup=_build_services_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("service_select_"))
async def cb_service_select(callback: CallbackQuery):
    service_id = callback.data.removeprefix("service_select_")
    status = gi.service_manager.get_status(service_id)
    if not status:
        await callback.answer("❌ Служба не найдена", show_alert=True)
        return

    emoji = "🟢" if status['running'] else "🔴"
    text = f"{emoji} <b>{status['name']}</b>\n\nСтатус: {'Запущена' if status['running'] else 'Остановлена'}\n"
    if status['running'] and status['started_at']:
        uptime = datetime.now() - status['started_at']
        h = int(uptime.total_seconds() // 3600)
        m = int((uptime.total_seconds() % 3600) // 60)
        text += f"Uptime: {h}ч {m}м\n"
    if status['last_error']:
        text += f"\n⚠️ Последняя ошибка:\n{status['last_error']}"

    await callback.message.edit_text(
        text, reply_markup=_build_service_control_keyboard(service_id, status['running']), parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("service_start_"))
async def cb_service_start(callback: CallbackQuery):
    service_id = callback.data.removeprefix("service_start_")
    try:
        success = await gi.service_manager.start_service(service_id)
        if success:
            status = gi.service_manager.get_status(service_id)
            await callback.message.edit_text(
                f"🟢 <b>{status['name']}</b>\n\nСтатус: Запущена",
                reply_markup=_build_service_control_keyboard(service_id, True),
                parse_mode="HTML"
            )
            await callback.answer("✅ Запущена", show_alert=True)
        else:
            await callback.answer("❌ Не удалось запустить", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка запуска {service_id}: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("service_stop_"))
async def cb_service_stop(callback: CallbackQuery):
    service_id = callback.data.removeprefix("service_stop_")
    try:
        success = await gi.service_manager.stop_service(service_id)
        if success:
            status = gi.service_manager.get_status(service_id)
            await callback.message.edit_text(
                f"🔴 <b>{status['name']}</b>\n\nСтатус: Остановлена",
                reply_markup=_build_service_control_keyboard(service_id, False),
                parse_mode="HTML"
            )
            await callback.answer("✅ Остановлена", show_alert=True)
        else:
            await callback.answer("❌ Не удалось остановить", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка остановки {service_id}: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("service_restart_"))
async def cb_service_restart(callback: CallbackQuery):
    service_id = callback.data.removeprefix("service_restart_")
    try:
        await callback.answer("🔄 Перезапуск...")
        success = await gi.service_manager.restart_service(service_id)
        if success:
            status = gi.service_manager.get_status(service_id)
            await callback.message.edit_text(
                f"🟢 <b>{status['name']}</b>\n\nСтатус: Перезапущена",
                reply_markup=_build_service_control_keyboard(service_id, True),
                parse_mode="HTML"
            )
        else:
            await callback.answer("❌ Не удалось перезапустить", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка перезапуска {service_id}: {e}")
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


# ----------------------------------------------------------
# КОНФИГ — навигация
# ----------------------------------------------------------

@router.callback_query(F.data == "config_back")
async def cb_config_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚙️ <b>Редактирование конфигурации</b>\n\nВыберите раздел:",
        reply_markup=_build_config_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("config_section_"))
async def cb_config_section(callback: CallbackQuery):
    section_id = callback.data.removeprefix("config_section_")
    section_label = cc.section_labels.get(section_id, section_id)
    await callback.message.edit_text(
        f"⚙️ <b>{section_label}</b>\n\nВыберите параметр:",
        reply_markup=_build_section_keyboard(section_id),
        parse_mode="HTML"
    )
    await callback.answer()


# ----------------------------------------------------------
# КОНФИГ — редактирование поля (единый хендлер)
# ----------------------------------------------------------

@router.callback_query(F.data.startswith("edit_field:"))
async def cb_edit_field(callback: CallbackQuery, state: FSMContext):
    key = callback.data.removeprefix("edit_field:")
    meta = cc.fields.get(key)
    if not meta:
        await callback.answer(f"❌ Неизвестное поле: {key}", show_alert=True)
        return

    current = cc.get(key)
    display = ("*" * 20) if meta.get("sensitive") else str(current)

    await state.update_data(field_key=key, section_id=meta["section"])
    await state.set_state(ConfigEditStates.waiting_for_value)

    await callback.message.edit_text(
        f"✏️ <b>{meta['label']}</b>\n\n"
        f"Текущее значение: <code>{display}</code>\n\n"
        f"{meta.get('hint', 'Введите новое значение')}:",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(StateFilter(ConfigEditStates.waiting_for_value))
async def process_config_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key        = data.get("field_key")
    section_id = data.get("section_id")

    if not key:
        await message.answer("❌ Контекст редактирования потерян")
        await state.clear()
        return

    ok, result = cc.edit(key, message.text.strip())

    if ok:
        meta = cc.fields.get(key, {})
        await message.answer(f"✅ <b>{meta.get('label', key)}</b> сохранено", parse_mode="HTML")
        await message.answer(
            f"⚙️ <b>{cc.section_labels.get(section_id, section_id)}</b>\n\nВыберите параметр:",
            reply_markup=_build_section_keyboard(section_id),
            parse_mode="HTML"
        )
        await state.clear()
    else:
        await message.answer(f"❌ {result}")
        # не сбрасываем state — пользователь вводит заново

# ----------------------------------------------------------
# СЛОЖНЫЕ ДАННЫЕ
# ----------------------------------------------------------

@router.callback_query(F.data.startswith("adv_"))
async def cb_config_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = callback.data.removeprefix("adv_")
    action, keys = data.split("_")
    await state.update_data(keys=keys)
    keys = keys.split("-")

    meta = cc.data.copy()
    for key in keys:
        try:
            meta = meta[key]
        except KeyError:
            await callback.message.edit_text('Ошибка - ключ отсутствует')

    data = meta["data"]
    if action == "section" or action == "back":
        await callback.message.edit_text(
            f"<code>{keys[-1]}</code>\n\n"
            f"✏️ <b>{data['label']}</b>"
            f"{f'\n\nПодсказка: <code>{data['hint']}</code>\n\n' if data['hint'] else ''}"
            f"{f'\n\nДанные для кодинга: <code>{data['special_data']}</code>' if data['special_data'] else ''}",
            reply_markup=_build_advanced_keyboard(keys),
            parse_mode="HTML"
        )

        if action == "back":
            await state.clear()
        return

    else:
        if action == 'add':
            step = 0

            cur_data = allowed_fields[step]

            for field, hint in cur_data.items():
                await callback.message.edit_text(get_add_msg(step, field, hint), reply_markup=_build_advanced_keyboard(keys, actions=[['back', 'Назад']]), parse_mode="HTML")
                await state.update_data(step=0)

        elif action == 'remove':
            await callback.message.edit_text('Вы уверены, что хотите удалить поле?', reply_markup=_build_advanced_keyboard(keys, actions=[['remove', 'Подтверждение']]), parse_mode="HTML")

        elif action == 'removetrue':
            ok, result = cc.edit_dict(keys, str, None, None)
            del keys[-1]
            await state.update_data(keys=keys)
            if ok:
                await callback.message.edit_text(f'Успешно удалили поле {data['label']}', reply_markup=_build_advanced_keyboard(keys, actions=[['back', 'Назад']]), parse_mode="HTML")
            else:
                await callback.message.edit_text(f'Не удалось удалить поле {data['label']}', reply_markup=_build_advanced_keyboard(keys, actions=[['back', 'Назад']]), parse_mode="HTML")
            await state.clear()
            return

        await state.set_state(ConfigEditStates.waiting_for_value_advanced)
        await state.update_data(action=action)


@router.message(StateFilter(ConfigEditStates.waiting_for_value_advanced))
async def process_config_value_advanced_message(message: Message, state: FSMContext):
    data = await state.get_data()
    keys_str = data.get("keys")
    action = data.get("action")

    if not keys_str:
        await message.answer("❌ Контекст редактирования потерян")
        await state.clear()
        return

    keys = keys_str.split("-")  # ← фикс 1: строку → список

    meta = cc.data.copy()
    for key in keys:
        try:
            meta = meta[key]
        except KeyError:
            await message.answer('Ошибка - ключ отсутствует')  # ← фикс 2: answer вместо edit_text
            await state.clear()
            return

    if action == "add":
        step = data.get("step")
        field = ''
        result, ok = None, False
        for field, hint in allowed_fields[step].items():

            if field == 'id':
                pos = None
            else:
                pos = data.get('pos')

                if pos is None:
                    await message.answer("❌ Контекст редактирования потерян")
                    await state.clear()
                    return

            raw_value = message.text.strip()
            result, ok = process_add_msg(keys, field, raw_value, pos=pos)

            if field == 'id':
                await state.update_data(pos=result)

        if ok:

            new_step = step + 1

            try:
                allowed_fields[new_step]
            except (IndexError, KeyError):
                await message.answer('Успешно добавили все новые значения, возвращаем..', reply_markup=_build_advanced_keyboard(keys), parse_mode="HTML")
                await state.clear()
                return

            await state.update_data(step=new_step)

            unique_hints = {
                'special_data': meta['data']['schema']
            }
            for field, hint in allowed_fields[new_step].items():
                text_msg = get_add_msg(new_step, field, hint, unique_hints=unique_hints)
                await message.answer(text_msg, reply_markup=_build_advanced_keyboard(keys, actions=[['back', 'Назад']]), parse_mode="HTML")

        else:
            await message.answer('Ошибка - возвращаем в конфиг и очищаем состояние', reply_markup=_build_advanced_keyboard(keys), parse_mode="HTML")
            await state.clear()


@router.callback_query(StateFilter(ConfigEditStates.waiting_for_value_advanced))
async def process_config_value_advanced_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    keys = data.get("keys")
    action = data.get("action")

    if not keys:
        await callback.message.answer("❌ Контекст редактирования потерян")
        await state.clear()
        return

    if action == "remove":
        ok, result = cc.edit_dict(keys, str, None, None)

        if ok:
            rez = keys.copy()
            del rez[-1]
            await callback.message.edit_text('Успешно удалили значение', reply_markup=_build_advanced_keyboard(rez),
                                             parse_mode="HTML")
        else:
            await callback.message.edit_text('Ошибка при удалении - возвращаем обратно',
                                             reply_markup=_build_advanced_keyboard(keys), parse_mode="HTML")


# ----------------------------------------------------------
# ЗАПУСК
# ----------------------------------------------------------

async def main(bot_token: str, admin_ids: list):
    bot = Bot(token=bot_token)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 Запуск Telegram Manager...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        traceback.print_exc()
    finally:
        await bot.session.close()


if __name__ == "__main__":
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    ADMIN_IDS = [123456789]

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Укажите BOT_TOKEN")
    else:
        asyncio.run(main(BOT_TOKEN, ADMIN_IDS))