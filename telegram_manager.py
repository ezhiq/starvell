# -*- coding: utf-8 -*-
"""
Telegram Bot для управления StarvellBot
Позволяет запускать/останавливать службы и редактировать конфигурацию
"""

import asyncio
import logging
import json
import traceback
from datetime import datetime
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config_manager import ConfigManager
from service_manager import ServiceManager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Состояния FSM для редактирования конфига
class ConfigEditStates(StatesGroup):
    waiting_for_value = State()


class TelegramManager:
    def __init__(self, bot_token: str, admin_ids: list):
        """
        :param bot_token: Токен Telegram бота
        :param admin_ids: Список ID администраторов
        """
        self.bot = Bot(token=bot_token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.admin_ids = admin_ids
        
        self.config_manager = ConfigManager()
        self.service_manager = ServiceManager()
        
        # Хранилище для состояний редактирования
        self.edit_context: Dict[int, dict] = {}
        
        # Регистрация обработчиков
        self._register_handlers()
    
    def _register_handlers(self):
        """Регистрация всех обработчиков команд и callback'ов"""
        
        # Команды
        self.dp.message(Command("start"))(self.cmd_start)
        self.dp.message(Command("status"))(self.cmd_status)
        self.dp.message(Command("config"))(self.cmd_config)
        self.dp.message(Command("services"))(self.cmd_services)
        self.dp.message(Command("help"))(self.cmd_help)
        
        # Callback кнопки для служб
        self.dp.callback_query(F.data.startswith("service_"))(self.callback_service_control)
        
        # Callback кнопки для конфига
        self.dp.callback_query(F.data.startswith("config_"))(self.callback_config_action)
        
        # Обработка ввода значений конфига
        self.dp.message(StateFilter(ConfigEditStates.waiting_for_value))(self.process_config_value)
    
    def _check_admin(self, user_id: int) -> bool:
        """Проверка прав администратора"""
        return user_id in self.admin_ids
    
    async def cmd_start(self, message: Message):
        """Обработчик команды /start"""
        if not self._check_admin(message.from_user.id):
            await message.answer("❌ У вас нет прав для использования этого бота")
            return
        
        await message.answer(
            "🤖 <b>StarvellBot Manager</b>\n\n"
            "Добро пожаловать в панель управления!\n\n"
            "Доступные команды:\n"
            "/services - управление службами\n"
            "/config - редактирование конфигурации\n"
            "/status - статус всех служб\n"
            "/help - справка",
            parse_mode="HTML"
        )
    
    async def cmd_help(self, message: Message):
        """Обработчик команды /help"""
        if not self._check_admin(message.from_user.id):
            return
        
        help_text = """
🤖 <b>Справка по командам</b>

<b>Управление службами:</b>
/services - открыть панель управления службами
/status - показать статус всех служб

<b>Конфигурация:</b>
/config - редактировать настройки

<b>Службы:</b>
• <b>Orders Monitor</b> - получение и обработка заказов (WebSocket + резервная проверка)
• <b>Dumper</b> - автоматическое понижение цен
• <b>Bumper</b> - поднятие предложений

<b>Управление службами:</b>
✅ Запустить - запускает выбранную службу
⏸ Остановить - останавливает службу
🔄 Перезапустить - перезапускает службу
📊 Статус - показывает текущее состояние

<b>Редактирование конфига:</b>
В разделе /config вы можете изменить:
• Токены и API ключи
• Настройки безопасности
• Сообщения для клиентов
• Параметры производительности
        """
        
        await message.answer(help_text, parse_mode="HTML")
    
    async def cmd_status(self, message: Message):
        """Показать статус всех служб"""
        if not self._check_admin(message.from_user.id):
            return
        
        status = self.service_manager.get_all_status()
        
        status_text = "📊 <b>Статус служб:</b>\n\n"
        
        for service_name, info in status.items():
            emoji = "🟢" if info['running'] else "🔴"
            status_text += f"{emoji} <b>{service_name}</b>\n"
            status_text += f"   Статус: {'Запущена' if info['running'] else 'Остановлена'}\n"
            
            if info['running'] and info['started_at']:
                uptime = datetime.now() - info['started_at']
                hours = int(uptime.total_seconds() // 3600)
                minutes = int((uptime.total_seconds() % 3600) // 60)
                status_text += f"   Uptime: {hours}ч {minutes}м\n"
            
            if info['last_error']:
                status_text += f"   ⚠️ Последняя ошибка: {info['last_error']}\n"
            
            status_text += "\n"
        
        await message.answer(status_text, parse_mode="HTML")
    
    async def cmd_services(self, message: Message):
        """Панель управления службами"""
        if not self._check_admin(message.from_user.id):
            return
        
        keyboard = self._build_services_keyboard()
        
        await message.answer(
            "⚙️ <b>Управление службами</b>\n\n"
            "Выберите службу для управления:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    def _build_services_keyboard(self) -> InlineKeyboardMarkup:
        """Построить клавиатуру для управления службами"""
        status = self.service_manager.get_all_status()
        
        buttons = []
        
        for service_id, info in status.items():
            emoji = "🟢" if info['running'] else "🔴"
            name = info['name']
            buttons.append([
                InlineKeyboardButton(
                    text=f"{emoji} {name}",
                    callback_data=f"service_select_{service_id}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(
                text="🔄 Обновить статус",
                callback_data="service_refresh"
            )
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    def _build_service_control_keyboard(self, service_id: str, is_running: bool) -> InlineKeyboardMarkup:
        """Построить клавиатуру управления конкретной службой"""
        buttons = []
        
        if is_running:
            buttons.append([
                InlineKeyboardButton(
                    text="⏸ Остановить",
                    callback_data=f"service_stop_{service_id}"
                )
            ])
            buttons.append([
                InlineKeyboardButton(
                    text="🔄 Перезапустить",
                    callback_data=f"service_restart_{service_id}"
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text="▶️ Запустить",
                    callback_data=f"service_start_{service_id}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="service_back"
            )
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    async def callback_service_control(self, callback: CallbackQuery):
        """Обработчик callback'ов управления службами"""
        if not self._check_admin(callback.from_user.id):
            await callback.answer("❌ Нет прав", show_alert=True)
            return
        
        data = callback.data
        
        # Обновить список служб
        if data == "service_refresh":
            keyboard = self._build_services_keyboard()
            await callback.message.edit_reply_markup(reply_markup=keyboard)
            await callback.answer("✅ Обновлено")
            return
        
        # Вернуться к списку
        if data == "service_back":
            keyboard = self._build_services_keyboard()
            await callback.message.edit_text(
                "⚙️ <b>Управление службами</b>\n\n"
                "Выберите службу для управления:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Выбор службы
        if data.startswith("service_select_"):
            service_id = data.replace("service_select_", "")
            status = self.service_manager.get_status(service_id)
            
            if not status:
                await callback.answer("❌ Служба не найдена", show_alert=True)
                return
            
            emoji = "🟢" if status['running'] else "🔴"
            status_text = f"{emoji} <b>{status['name']}</b>\n\n"
            status_text += f"Статус: {'Запущена' if status['running'] else 'Остановлена'}\n"
            
            if status['running'] and status['started_at']:
                uptime = datetime.now() - status['started_at']
                hours = int(uptime.total_seconds() // 3600)
                minutes = int((uptime.total_seconds() % 3600) // 60)
                status_text += f"Uptime: {hours}ч {minutes}м\n"
            
            if status['last_error']:
                status_text += f"\n⚠️ Последняя ошибка:\n{status['last_error']}"
            
            keyboard = self._build_service_control_keyboard(service_id, status['running'])
            
            await callback.message.edit_text(
                status_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Запуск службы
        if data.startswith("service_start_"):
            service_id = data.replace("service_start_", "")
            
            try:
                success = await self.service_manager.start_service(service_id)
                
                if success:
                    await callback.answer("✅ Служба запущена", show_alert=True)
                    
                    # Обновить информацию
                    status = self.service_manager.get_status(service_id)
                    keyboard = self._build_service_control_keyboard(service_id, True)
                    
                    await callback.message.edit_text(
                        f"🟢 <b>{status['name']}</b>\n\nСтатус: Запущена",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                else:
                    await callback.answer("❌ Не удалось запустить службу", show_alert=True)
            
            except Exception as e:
                logger.error(f"Ошибка запуска службы {service_id}: {e}")
                await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            
            return
        
        # Остановка службы
        if data.startswith("service_stop_"):
            service_id = data.replace("service_stop_", "")
            
            try:
                success = await self.service_manager.stop_service(service_id)
                
                if success:
                    await callback.answer("✅ Служба остановлена", show_alert=True)
                    
                    # Обновить информацию
                    status = self.service_manager.get_status(service_id)
                    keyboard = self._build_service_control_keyboard(service_id, False)
                    
                    await callback.message.edit_text(
                        f"🔴 <b>{status['name']}</b>\n\nСтатус: Остановлена",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                else:
                    await callback.answer("❌ Не удалось остановить службу", show_alert=True)
            
            except Exception as e:
                logger.error(f"Ошибка остановки службы {service_id}: {e}")
                await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            
            return
        
        # Перезапуск службы
        if data.startswith("service_restart_"):
            service_id = data.replace("service_restart_", "")
            
            try:
                await callback.answer("🔄 Перезапуск...", show_alert=False)
                
                success = await self.service_manager.restart_service(service_id)
                
                if success:
                    status = self.service_manager.get_status(service_id)
                    keyboard = self._build_service_control_keyboard(service_id, True)
                    
                    await callback.message.edit_text(
                        f"🟢 <b>{status['name']}</b>\n\nСтатус: Перезапущена",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                else:
                    await callback.answer("❌ Не удалось перезапустить", show_alert=True)
            
            except Exception as e:
                logger.error(f"Ошибка перезапуска службы {service_id}: {e}")
                await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            
            return
    
    async def cmd_config(self, message: Message):
        """Панель редактирования конфигурации"""
        if not self._check_admin(message.from_user.id):
            return
        
        keyboard = self._build_config_keyboard()
        
        await message.answer(
            "⚙️ <b>Редактирование конфигурации</b>\n\n"
            "Выберите раздел для редактирования:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    def _build_config_keyboard(self) -> InlineKeyboardMarkup:
        """Построить клавиатуру разделов конфигурации"""
        sections = self.config_manager.get_sections()
        
        buttons = []
        
        for section_id, section_name in sections.items():
            buttons.append([
                InlineKeyboardButton(
                    text=section_name,
                    callback_data=f"config_section_{section_id}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(
                text="💾 Сохранить и применить",
                callback_data="config_save"
            )
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    def _build_section_keyboard(self, section_id: str) -> InlineKeyboardMarkup:
        """Построить клавиатуру параметров раздела"""
        params = self.config_manager.get_section_params(section_id)
        
        buttons = []
        
        for param_id, param_info in params.items():
            buttons.append([
                InlineKeyboardButton(
                    text=param_info['name'],
                    callback_data=f"config_edit_{section_id}_{param_id}"
                )
            ])
        
        buttons.append([
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="config_back"
            )
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    async def callback_config_action(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик callback'ов конфигурации"""
        if not self._check_admin(callback.from_user.id):
            await callback.answer("❌ Нет прав", show_alert=True)
            return
        
        data = callback.data
        
        # Вернуться к списку разделов
        if data == "config_back":
            keyboard = self._build_config_keyboard()
            await callback.message.edit_text(
                "⚙️ <b>Редактирование конфигурации</b>\n\n"
                "Выберите раздел для редактирования:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Сохранить конфигурацию
        if data == "config_save":
            try:
                self.config_manager.save()
                await callback.answer("✅ Конфигурация сохранена!", show_alert=True)
                
                # Перезапустить все службы для применения
                restart_text = "\n\n⚠️ Необходимо перезапустить службы для применения изменений"
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🔄 Перезапустить все службы",
                        callback_data="config_restart_all"
                    )],
                    [InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="config_back"
                    )]
                ])
                
                await callback.message.edit_text(
                    "✅ Конфигурация сохранена!" + restart_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            except Exception as e:
                logger.error(f"Ошибка сохранения конфига: {e}")
                await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            
            return
        
        # Перезапустить все службы
        if data == "config_restart_all":
            await callback.answer("🔄 Перезапуск служб...", show_alert=False)
            
            try:
                results = await self.service_manager.restart_all()
                
                success_count = sum(1 for r in results.values() if r)
                total_count = len(results)
                
                await callback.answer(
                    f"✅ Перезапущено {success_count}/{total_count} служб",
                    show_alert=True
                )
                
                keyboard = self._build_config_keyboard()
                await callback.message.edit_text(
                    "⚙️ <b>Редактирование конфигурации</b>\n\n"
                    "Выберите раздел для редактирования:",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            except Exception as e:
                logger.error(f"Ошибка перезапуска служб: {e}")
                await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            
            return
        
        # Выбор раздела
        if data.startswith("config_section_"):
            section_id = data.replace("config_section_", "")
            section_name = self.config_manager.get_section_name(section_id)
            
            keyboard = self._build_section_keyboard(section_id)
            
            await callback.message.edit_text(
                f"⚙️ <b>{section_name}</b>\n\n"
                f"Выберите параметр для редактирования:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Редактирование параметра
        if data.startswith("config_edit_"):
            parts = data.replace("config_edit_", "").split("_", 1)
            section_id = parts[0]
            param_id = parts[1]
            
            param_info = self.config_manager.get_param_info(section_id, param_id)
            current_value = self.config_manager.get_value(section_id, param_id)
            
            # Сохраняем контекст редактирования
            self.edit_context[callback.from_user.id] = {
                'section_id': section_id,
                'param_id': param_id,
                'message_id': callback.message.message_id
            }
            
            # Маскируем чувствительные данные
            display_value = current_value
            if param_info.get('sensitive'):
                display_value = "*" * 20
            
            await state.set_state(ConfigEditStates.waiting_for_value)
            
            await callback.message.edit_text(
                f"✏️ <b>{param_info['name']}</b>\n\n"
                f"Описание: {param_info['description']}\n\n"
                f"Текущее значение:\n<code>{display_value}</code>\n\n"
                f"Отправьте новое значение:",
                parse_mode="HTML"
            )
            await callback.answer()
            return
    
    async def process_config_value(self, message: Message, state: FSMContext):
        """Обработка нового значения параметра конфигурации"""
        if not self._check_admin(message.from_user.id):
            return
        
        user_id = message.from_user.id
        
        if user_id not in self.edit_context:
            await message.answer("❌ Контекст редактирования потерян")
            await state.clear()
            return
        
        context = self.edit_context[user_id]
        section_id = context['section_id']
        param_id = context['param_id']
        new_value = message.text.strip()
        
        try:
            # Валидация и установка нового значения
            self.config_manager.set_value(section_id, param_id, new_value)
            
            await message.answer(
                "✅ Значение обновлено!\n\n"
                "💾 Не забудьте сохранить изменения через /config → Сохранить"
            )
            
            # Показать обновленный раздел
            keyboard = self._build_section_keyboard(section_id)
            section_name = self.config_manager.get_section_name(section_id)
            
            await self.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=context['message_id'],
                text=f"⚙️ <b>{section_name}</b>\n\n"
                     f"Выберите параметр для редактирования:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        
        except ValueError as e:
            await message.answer(f"❌ Ошибка валидации: {str(e)}")
        
        except Exception as e:
            logger.error(f"Ошибка установки значения: {e}")
            await message.answer(f"❌ Ошибка: {str(e)}")
        
        finally:
            del self.edit_context[user_id]
            await state.clear()
    
    async def start(self):
        """Запустить бота"""
        logger.info("🚀 Запуск Telegram Manager...")
        
        try:
            await self.dp.start_polling(self.bot)
        except Exception as e:
            logger.error(f"Ошибка при запуске бота: {e}")
            traceback.print_exc()
        finally:
            await self.bot.session.close()


async def main():
    """Точка входа"""
    
    # Загрузка настроек из файла или переменных окружения
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Замените на ваш токен
    ADMIN_IDS = [123456789]  # Замените на ваши Telegram ID
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Укажите BOT_TOKEN в коде или переменных окружения")
        return
    
    manager = TelegramManager(bot_token=BOT_TOKEN, admin_ids=ADMIN_IDS)
    await manager.start()


if __name__ == "__main__":
    asyncio.run(main())
