# 🏗️ Архитектура StarvellBot Manager

## Обзор системы

StarvellBot Manager - это система управления ботом через Telegram с разделением на независимые службы. Система состоит из четырех основных компонентов:

## 📦 Компоненты

### 1. Telegram Manager (`telegram_manager.py`)

**Назначение**: Интерфейс управления через Telegram бота

**Основные функции**:
- Приём команд от администратора
- Отображение статуса служб
- Редактирование конфигурации
- Управление службами (запуск/остановка/перезапуск)

**Команды**:
```
/start - приветствие и список команд
/services - панель управления службами
/config - редактор конфигурации
/status - статус всех служб
/help - справка
```

**Особенности**:
- FSM (Finite State Machine) для многошаговых диалогов
- Inline-клавиатуры для удобного управления
- Валидация прав доступа (только ADMIN_IDS)
- Маскировка чувствительных данных

---

### 2. Service Manager (`service_manager.py`)

**Назначение**: Управление жизненным циклом служб

**Службы**:

#### 🔄 Orders Monitor
- **Компоненты**: 
  - WebSocket подключение (`get_updates()`)
  - Резервная проверка (`get_extra_orders()`)
- **Работает**: Параллельно обе задачи
- **Частота**: WebSocket - постоянно, резервная проверка - каждые 100 сек

#### 💰 Dumper
- **Функция**: Автоматическое понижение цен
- **Логика**: 
  1. Получает список своих предложений
  2. Для каждого получает конкурентов
  3. Анализирует позицию
  4. Если не первый - снижает цену
  5. Проверяет минимальный курс
- **Частота**: Каждые 30 секунд

#### 🚀 Bumper
- **Функция**: Поднятие предложений
- **Логика**: Вызывает API bump для всех активных категорий
- **Частота**: Каждые 5 минут (300 секунд)

**Методы**:
```python
start_service(service_id)    # Запустить службу
stop_service(service_id)     # Остановить службу
restart_service(service_id)  # Перезапустить службу
restart_all()                # Перезапустить все
get_status(service_id)       # Получить статус
get_all_status()             # Получить статусы всех служб
```

**Обработка ошибок**:
- Автоматическое логирование ошибок
- Сохранение последней ошибки для отображения
- Graceful shutdown при остановке

---

### 3. Config Manager (`config_manager.py`)

**Назначение**: Управление конфигурацией с сохранением

**Структура конфигурации**:

```json
{
    "starvell": {
        "cookie_session": "...",
        "my_id": 12345
    },
    "fragment": {
        "fragment_hash": "...",
        "fragment_cookie": "...",
        "fragment_show_sender": "1"
    },
    "ton": {
        "ton_api_key": "...",
        "mnemonic": ["word1", "word2", ...],
        "destination_address": "...",
        "is_testnet": false
    },
    "messages": {
        "universal": "...",
        "refund_msg": "...",
        "hello": "..."
    },
    "performance": {
        "min_star_rate": 1.10,
        "max_concurrent_orders": 3
    }
}
```

**Методы**:
```python
load()                                    # Загрузить из JSON
save()                                    # Сохранить в JSON
get_value(section_id, param_id)          # Получить значение
set_value(section_id, param_id, value)   # Установить значение
export_to_config_py()                    # Экспорт в config.py
generate_config_py()                     # Сгенерировать config.py
```

**Типы данных**:
- `string` - строка
- `int` - целое число
- `float` - число с плавающей точкой
- `bool` - булево (true/false)
- `list` - список (например, для mnemonic)

**Валидация**:
- Автоматическое преобразование типов
- Проверка обязательных полей
- Значения по умолчанию

---

### 4. Main (`main.py`)

**Назначение**: Точка входа и оркестрация

**Последовательность запуска**:
```
1. Проверка настроек (BOT_TOKEN, ADMIN_IDS)
2. Инициализация ConfigManager
3. Генерация config.py
4. Импорт StarvellBot
5. Создание экземпляра StarvellBot
6. Инициализация ServiceManager
7. Привязка bot_instance к ServiceManager
8. Инициализация TelegramManager
9. Запуск Telegram бота
```

**Graceful Shutdown**:
- Обработка Ctrl+C
- Автоматическая остановка всех служб
- Закрытие соединений

---

## 🔄 Поток данных

### Запуск службы

```
Пользователь → Telegram Bot
              ↓
          TelegramManager (callback_service_control)
              ↓
          ServiceManager.start_service()
              ↓
          Создание asyncio.Task
              ↓
          _service_wrapper()
              ↓
          service['func']() ← (например, _run_dumper)
              ↓
          bot_instance.dumper()
              ↓
          Служба работает
```

### Редактирование конфигурации

```
Пользователь → /config
              ↓
          Выбор раздела
              ↓
          Выбор параметра
              ↓
          FSM: waiting_for_value
              ↓
          Ввод нового значения
              ↓
          ConfigManager.set_value()
              ↓
          Валидация и преобразование типа
              ↓
          Сохранение в память
              ↓
          Пользователь → "Сохранить"
              ↓
          ConfigManager.save()
              ↓
          Запись в config_data.json
              ↓
          ConfigManager.generate_config_py()
              ↓
          Создание config.py
```

---

## 🛡️ Безопасность

### Контроль доступа
```python
def _check_admin(self, user_id: int) -> bool:
    return user_id in self.admin_ids
```
- Проверка при каждой команде
- Проверка при callback'ах
- Отказ в доступе для неавторизованных пользователей

### Маскировка данных
```python
if param_info.get('sensitive'):
    display_value = "*" * 20
```
- Токены отображаются как `********************`
- Приватные ключи не показываются
- Cookie маскируются

### Валидация ввода
```python
def _validate_and_convert(self, value: str, param_info: dict) -> Any:
    # Преобразование и валидация типов
    # Проверка корректности значений
```

---

## ⚡ Асинхронность

### Параллельное выполнение

**Orders Monitor**:
```python
tasks = [
    asyncio.create_task(bot.get_updates()),
    asyncio.create_task(bot.get_extra_orders())
]
await asyncio.gather(*tasks)
```

**Независимые службы**:
- Каждая служба - отдельная asyncio.Task
- Могут работать параллельно
- Независимая остановка/перезапуск

### Управление задачами
```python
# Запуск
service['task'] = asyncio.create_task(...)

# Остановка
service['task'].cancel()
await asyncio.wait_for(service['task'], timeout=5.0)
```

---

## 📊 Мониторинг

### Статус служб
Для каждой службы отслеживается:
- `running: bool` - запущена ли
- `started_at: datetime` - время запуска
- `last_error: str` - последняя ошибка
- `task: asyncio.Task` - ссылка на задачу

### Логирование
```python
logger.info(f"▶️ Запуск службы {service['name']}...")
logger.error(f"❌ Ошибка в службе {service['name']}: {error_msg}")
logger.warning(f"⏸ Служба {service['name']} остановлена")
```

Логи пишутся:
- В консоль (с цветами для уровней)
- В файл `bot.log`

---

## 🔧 Расширение системы

### Добавление новой службы

1. **В ServiceManager (`service_manager.py`)**:
```python
self.services['new_service'] = {
    'name': 'Название службы',
    'description': 'Описание',
    'task': None,
    'running': False,
    'started_at': None,
    'last_error': None,
    'func': self._run_new_service
}

async def _run_new_service(self):
    await self.bot_instance.new_method()
```

2. **В StarvellBot (`starvell_bot.py`)**:
```python
async def new_method(self):
    while True:
        # Ваша логика
        await asyncio.sleep(interval)
```

### Добавление параметра конфигурации

**В ConfigManager (`config_manager.py`)**:
```python
'section_name': {
    'name': 'Название раздела',
    'params': {
        'param_name': {
            'name': 'Название параметра',
            'description': 'Описание',
            'type': 'string',  # string, int, float, bool, list
            'required': True,
            'sensitive': False,  # Маскировать ли
            'default': 'значение'
        }
    }
}
```

---

## 🐛 Отладка

### Включение debug-логов
```python
logging.basicConfig(level=logging.DEBUG)
```

### Проверка статуса службы
```python
status = service_manager.get_status('service_id')
print(status)
```

### Ручной запуск службы
```python
await service_manager.start_service('orders_monitor')
```

### Проверка конфигурации
```python
config_manager = ConfigManager()
print(config_manager.export_to_config_py())
```

---

## 📝 Best Practices

1. **Всегда сохраняйте конфигурацию** после изменений через `/config` → "Сохранить"

2. **Перезапускайте службы** после изменения конфигурации для применения

3. **Мониторьте логи** - все важные события логируются

4. **Используйте /status** регулярно для проверки здоровья служб

5. **Не запускайте несколько экземпляров** одновременно - это может привести к конфликтам

6. **Делайте backup** файла `config_data.json` - там вся конфигурация

7. **Проверяйте uptime служб** - если служба часто падает, проверьте last_error

---

## 🚀 Производительность

### Рекомендуемые настройки

**Для высокой нагрузки**:
```json
{
    "performance": {
        "min_star_rate": 1.05,
        "max_concurrent_orders": 5
    }
}
```

**Для стабильной работы**:
```json
{
    "performance": {
        "min_star_rate": 1.10,
        "max_concurrent_orders": 3
    }
}
```

### Оптимизация

- **Dumper**: Частота проверки 30 сек оптимальна
- **Bumper**: Частота 5 минут рекомендована API
- **Orders Monitor**: WebSocket + резервная проверка обеспечивает надёжность

---

## 📚 Зависимости

- **aiogram 3.4.1** - Telegram Bot API
- **aiohttp 3.9.1** - Async HTTP клиент
- **aiosqlite 0.19.0** - Async SQLite

Все остальное - стандартная библиотека Python.

---

**Система готова к расширению и масштабированию!** 🎉
