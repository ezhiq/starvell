# -*- coding: utf-8 -*-
"""
Config Manager для StarvellBot
Управление конфигурацией с сохранением в JSON
"""

import json
import logging
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigManager:
    """Менеджер конфигурации"""
    
    def __init__(self, config_path: str = "config_data.json"):
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self.schema = self._build_schema()
        
        # Загружаем конфигурацию
        self.load()
    
    def _build_schema(self) -> Dict[str, dict]:
        """Построить схему конфигурации с описаниями"""
        return {
            'starvell': {
                'name': '🔐 Starvell API',
                'params': {
                    'cookie_session': {
                        'name': 'Session Cookie',
                        'description': 'Cookie сессии для авторизации на Starvell',
                        'type': 'string',
                        'sensitive': True,
                        'required': True
                    },
                    'my_id': {
                        'name': 'User ID',
                        'description': 'ID вашего пользователя на Starvell',
                        'type': 'int',
                        'required': True
                    }
                }
            },
            'fragment': {
                'name': '⭐ Fragment API',
                'params': {
                    'fragment_hash': {
                        'name': 'Fragment Hash',
                        'description': 'Hash для Fragment API',
                        'type': 'string',
                        'sensitive': True,
                        'required': True
                    },
                    'fragment_cookie': {
                        'name': 'Fragment Cookie',
                        'description': 'Cookie для Fragment API',
                        'type': 'string',
                        'sensitive': True,
                        'required': True
                    },
                    'fragment_show_sender': {
                        'name': 'Show Sender',
                        'description': 'Показывать отправителя (0/1)',
                        'type': 'string',
                        'required': True,
                        'default': '1'
                    }
                }
            },
            'ton': {
                'name': '💎 TON Wallet',
                'params': {
                    'ton_api_key': {
                        'name': 'TON API Key',
                        'description': 'API ключ для TON API',
                        'type': 'string',
                        'sensitive': True,
                        'required': True
                    },
                    'mnemonic': {
                        'name': 'Wallet Mnemonic',
                        'description': 'Seed фраза кошелька (24 слова через пробел)',
                        'type': 'list',
                        'sensitive': True,
                        'required': True
                    },
                    'destination_address': {
                        'name': 'Destination Address',
                        'description': 'Адрес кошелька получателя',
                        'type': 'string',
                        'required': True
                    },
                    'is_testnet': {
                        'name': 'Testnet Mode',
                        'description': 'Использовать тестовую сеть (true/false)',
                        'type': 'bool',
                        'required': True,
                        'default': False
                    }
                }
            },
            'messages': {
                'name': '💬 Сообщения',
                'params': {
                    'universal': {
                        'name': 'Универсальный ответ',
                        'description': 'Текст для ответа на отзывы',
                        'type': 'string',
                        'required': True,
                        'default': 'Спасибо за покупку! Буду рад видеть вас снова! 😊'
                    },
                    'refund_msg': {
                        'name': 'Сообщение о возврате',
                        'description': 'Текст при возврате средств',
                        'type': 'string',
                        'required': True,
                        'default': '❌ К сожалению, ваш заказ был отменен и средства возвращены.\n\nЕсли у вас возникли вопросы, пожалуйста, свяжитесь с поддержкой.'
                    },
                    'hello': {
                        'name': 'Приветствие',
                        'description': 'Приветственное сообщение для новых клиентов',
                        'type': 'string',
                        'required': True,
                        'default': '👋 Здравствуйте! Добро пожаловать!\n\nЯ бот для автоматической выдачи Telegram Stars.\nВаш заказ обрабатывается, пожалуйста, подождите.\n\nЕсли у вас есть вопросы, используйте команду /help'
                    }
                }
            },
            'performance': {
                'name': '⚙️ Производительность',
                'params': {
                    'min_star_rate': {
                        'name': 'Минимальный курс звезды',
                        'description': 'Минимальный курс звезды в рублях для автопонижения',
                        'type': 'float',
                        'required': True,
                        'default': 1.10
                    },
                    'is_online': {
                        'name': 'Демпингуем по онлайну?',
                        'description': '0 - Демпингуем все заказы, 1 - Демпингуем по онлайну',
                        'type': 'int',
                        'required': True,
                        'default': 0
                    },
                    'max_concurrent_orders': {
                        'name': 'Макс. одновременных заказов',
                        'description': 'Максимальное количество одновременных заказов',
                        'type': 'int',
                        'required': True,
                        'default': 3
                    }
                }
            }
        }
    
    def load(self):
        """Загрузить конфигурацию из файла"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                logger.info(f"✅ Конфигурация загружена из {self.config_path}")
            except Exception as e:
                logger.error(f"Ошибка загрузки конфигурации: {e}")
                self.config = {}
        else:
            logger.warning(f"Файл конфигурации {self.config_path} не найден, используются значения по умолчанию")
            self.config = self._get_defaults()
    
    def save(self):
        """Сохранить конфигурацию в файл"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            logger.info(f"✅ Конфигурация сохранена в {self.config_path}")
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")
            raise
    
    def _get_defaults(self) -> Dict[str, Any]:
        """Получить значения по умолчанию"""
        defaults = {}
        
        for section_id, section_data in self.schema.items():
            defaults[section_id] = {}
            
            for param_id, param_info in section_data['params'].items():
                if 'default' in param_info:
                    defaults[section_id][param_id] = param_info['default']
                else:
                    defaults[section_id][param_id] = None
        
        return defaults
    
    def get_sections(self) -> Dict[str, str]:
        """Получить список разделов"""
        return {
            section_id: section_data['name']
            for section_id, section_data in self.schema.items()
        }
    
    def get_section_name(self, section_id: str) -> Optional[str]:
        """Получить название раздела"""
        if section_id in self.schema:
            return self.schema[section_id]['name']
        return None
    
    def get_section_params(self, section_id: str) -> Optional[Dict[str, dict]]:
        """Получить параметры раздела"""
        if section_id in self.schema:
            return self.schema[section_id]['params']
        return None
    
    def get_param_info(self, section_id: str, param_id: str) -> Optional[dict]:
        """Получить информацию о параметре"""
        if section_id in self.schema:
            params = self.schema[section_id]['params']
            if param_id in params:
                return params[param_id]
        return None
    
    def get_value(self, section_id: str, param_id: str) -> Any:
        """Получить значение параметра"""
        if section_id in self.config and param_id in self.config[section_id]:
            return self.config[section_id][param_id]
        
        # Возвращаем значение по умолчанию
        param_info = self.get_param_info(section_id, param_id)
        if param_info and 'default' in param_info:
            return param_info['default']
        
        return None
    
    def set_value(self, section_id: str, param_id: str, value: Any):
        """Установить значение параметра"""
        if section_id not in self.schema:
            raise ValueError(f"Неизвестный раздел: {section_id}")
        
        param_info = self.get_param_info(section_id, param_id)
        if not param_info:
            raise ValueError(f"Неизвестный параметр: {param_id}")
        
        # Валидация и преобразование типа
        validated_value = self._validate_and_convert(value, param_info)
        
        # Создаём раздел если не существует
        if section_id not in self.config:
            self.config[section_id] = {}
        
        # Устанавливаем значение
        self.config[section_id][param_id] = validated_value
        
        logger.info(f"Параметр {section_id}.{param_id} установлен")
    
    def _validate_and_convert(self, value: str, param_info: dict) -> Any:
        """Валидировать и преобразовать значение"""
        param_type = param_info.get('type', 'string')
        
        try:
            if param_type == 'int':
                return int(value)
            
            elif param_type == 'float':
                return float(value)
            
            elif param_type == 'bool':
                if isinstance(value, bool):
                    return value
                if value.lower() in ('true', '1', 'yes', 'да'):
                    return True
                elif value.lower() in ('false', '0', 'no', 'нет'):
                    return False
                else:
                    raise ValueError(f"Некорректное boolean значение: {value}")
            
            elif param_type == 'list':
                if isinstance(value, list):
                    return value
                # Разбиваем строку по пробелам
                return value.split()
            
            elif param_type == 'string':
                return str(value)
            
            else:
                return value
        
        except Exception as e:
            raise ValueError(f"Ошибка преобразования значения: {e}")
    
    def export_to_config_py(self) -> str:
        """Экспортировать в формат config.py для совместимости"""
        lines = []
        lines.append("# -*- coding: utf-8 -*-")
        lines.append("# Автоматически сгенерированный файл конфигурации")
        lines.append("# Редактируйте через Telegram бота для сохранения изменений\n")
        
        # Starvell
        if 'starvell' in self.config:
            lines.append("# ============================================================================")
            lines.append("# STARVELL API")
            lines.append("# ============================================================================\n")
            
            cookie_session = self.config['starvell'].get('cookie_session')
            my_id = self.config['starvell'].get('my_id')
            
            lines.append(f"cookie_session = {repr(cookie_session)}")
            lines.append(f"my_id = {my_id}\n")
        
        # Fragment
        if 'fragment' in self.config:
            lines.append("# ============================================================================")
            lines.append("# FRAGMENT API")
            lines.append("# ============================================================================\n")
            
            fragment_hash = self.config['fragment'].get('fragment_hash')
            fragment_cookie = self.config['fragment'].get('fragment_cookie')
            fragment_show_sender = self.config['fragment'].get('fragment_show_sender')
            
            lines.append(f"fragment_hash = {repr(fragment_hash)}")
            lines.append(f"fragment_cookie = {repr(fragment_cookie)}")
            lines.append(f"fragment_url = 'https://fragment.com/api'")
            lines.append(f"fragment_show_sender = {repr(fragment_show_sender)}\n")
        
        # TON
        if 'ton' in self.config:
            lines.append("# ============================================================================")
            lines.append("# TON WALLET")
            lines.append("# ============================================================================\n")
            
            ton_api_key = self.config['ton'].get('ton_api_key')
            mnemonic = self.config['ton'].get('mnemonic')
            destination_address = self.config['ton'].get('destination_address')
            is_testnet = self.config['ton'].get('is_testnet')
            
            lines.append(f"ton_api_key = {repr(ton_api_key)}")
            lines.append(f"mnemonic = {repr(mnemonic)}")
            lines.append(f"destination_address = {repr(destination_address)}")
            lines.append(f"is_testnet = {is_testnet}\n")
        
        # Messages
        if 'messages' in self.config:
            lines.append("# ============================================================================")
            lines.append("# СООБЩЕНИЯ")
            lines.append("# ============================================================================\n")
            
            universal = self.config['messages'].get('universal')
            refund_msg = self.config['messages'].get('refund_msg')
            hello = self.config['messages'].get('hello')
            
            lines.append(f"universal = {repr(universal)}")
            lines.append(f"refund_msg = {repr(refund_msg)}")
            lines.append(f"hello = {repr(hello)}\n")
        
        # Performance
        if 'performance' in self.config:
            lines.append("# ============================================================================")
            lines.append("# ПРОИЗВОДИТЕЛЬНОСТЬ")
            lines.append("# ============================================================================\n")
            
            min_star_rate = self.config['performance'].get('min_star_rate')
            max_concurrent_orders = self.config['performance'].get('max_concurrent_orders')
            is_online = self.config['performance'].get('is_online')
            
            lines.append(f"min_star_rate = {min_star_rate}")
            lines.append(f"max_concurrent_orders = {max_concurrent_orders}\n")
            lines.append(f"is_online = {is_online}\n")
        
        return '\n'.join(lines)
    
    def generate_config_py(self, output_path: str = "config.py"):
        """Сгенерировать config.py файл"""
        content = self.export_to_config_py()
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"✅ config.py сгенерирован: {output_path}")
        except Exception as e:
            logger.error(f"Ошибка генерации config.py: {e}")
            raise


if __name__ == "__main__":
    # Тест
    manager = ConfigManager()
    
    # Установить значение
    manager.set_value('starvell', 'cookie_session', 'test_session_123')
    manager.set_value('starvell', 'my_id', '12345')
    manager.set_value('performance', 'min_star_rate', '1.25')
    
    # Сохранить
    manager.save()
    
    # Экспорт
    print(manager.export_to_config_py())
