# -*- coding: utf-8 -*-
"""
Service Manager для StarvellBot
Управляет запуском/остановкой служб (Orders Monitor, Dumper, Bumper)
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional, Callable
import traceback

logger = logging.getLogger(__name__)


class ServiceManager:
    """Менеджер служб StarvellBot"""
    
    def __init__(self):
        self.services: Dict[str, dict] = {
            'orders_monitor': {
                'name': 'Orders Monitor',
                'description': 'Получение и обработка заказов (WebSocket + резервная проверка)',
                'task': None,
                'running': False,
                'started_at': None,
                'last_error': None,
                'func': None  # Будет установлена позже
            },
            'dumper': {
                'name': 'Price Dumper',
                'description': 'Автоматическое понижение цен',
                'task': None,
                'running': False,
                'started_at': None,
                'last_error': None,
                'func': None
            },
            'bumper': {
                'name': 'Offer Bumper',
                'description': 'Поднятие предложений каждые 5 минут',
                'task': None,
                'running': False,
                'started_at': None,
                'last_error': None,
                'func': None
            }
        }
        
        self.bot_instance = None
    
    def set_bot_instance(self, bot):
        """Установить экземпляр StarvellBot для доступа к его методам"""
        self.bot_instance = bot
        
        # Установить функции для запуска служб
        self.services['orders_monitor']['func'] = self._run_orders_monitor
        self.services['dumper']['func'] = self._run_dumper
        self.services['bumper']['func'] = self._run_bumper
    
    async def _run_orders_monitor(self):
        """Запуск мониторинга заказов (WebSocket + резервная проверка)"""
        if not self.bot_instance:
            raise RuntimeError("Bot instance not set")
        
        # Запускаем обе задачи параллельно
        tasks = [
            asyncio.create_task(self.bot_instance.get_updates()),
            asyncio.create_task(self.bot_instance.get_extra_orders()),
            asyncio.create_task(self.bot_instance.check_balance_and_cancel())
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Orders Monitor cancelled")
            for task in tasks:
                task.cancel()
            raise
    
    async def _run_dumper(self):
        """Запуск автопонижения цен"""
        if not self.bot_instance:
            raise RuntimeError("Bot instance not set")
        
        await self.bot_instance.dumper()
    
    async def _run_bumper(self):
        """Запуск поднятия предложений"""
        if not self.bot_instance:
            raise RuntimeError("Bot instance not set")
        
        await self.bot_instance.bumper()
    
    async def _service_wrapper(self, service_id: str):
        """Обёртка для запуска службы с обработкой ошибок"""
        service = self.services[service_id]
        
        try:
            logger.info(f"▶️ Запуск службы {service['name']}...")
            service['running'] = True
            service['started_at'] = datetime.now()
            service['last_error'] = None
            
            # Запуск функции службы
            await service['func']()
            
        except asyncio.CancelledError:
            logger.info(f"⏸ Служба {service['name']} остановлена")
            raise
        
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"❌ Ошибка в службе {service['name']}: {error_msg}")
            traceback.print_exc()
            service['last_error'] = error_msg
        
        finally:
            service['running'] = False
            service['task'] = None
    
    async def start_service(self, service_id: str) -> bool:
        """Запустить службу"""
        if service_id not in self.services:
            logger.error(f"Служба {service_id} не найдена")
            return False
        
        service = self.services[service_id]
        
        # Проверка что служба уже не запущена
        if service['running']:
            logger.warning(f"Служба {service['name']} уже запущена")
            return False
        
        # Проверка что функция установлена
        if not service['func']:
            logger.error(f"Функция для службы {service['name']} не установлена")
            return False
        
        try:
            # Создаём задачу
            service['task'] = asyncio.create_task(
                self._service_wrapper(service_id),
                name=f"service_{service_id}"
            )
            
            # Даём время на запуск
            await asyncio.sleep(0.5)
            
            logger.info(f"✅ Служба {service['name']} запущена")
            return True
        
        except Exception as e:
            logger.error(f"Ошибка запуска службы {service['name']}: {e}")
            traceback.print_exc()
            return False
    
    async def stop_service(self, service_id: str) -> bool:
        """Остановить службу"""
        if service_id not in self.services:
            logger.error(f"Служба {service_id} не найдена")
            return False
        
        service = self.services[service_id]
        
        if not service['running'] or not service['task']:
            logger.warning(f"Служба {service['name']} не запущена")
            return False
        
        try:
            # Отменяем задачу
            service['task'].cancel()
            
            # Ждём завершения
            try:
                await asyncio.wait_for(service['task'], timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout при остановке службы {service['name']}")
            except asyncio.CancelledError:
                pass
            
            service['running'] = False
            service['task'] = None
            
            logger.info(f"⏸ Служба {service['name']} остановлена")
            return True
        
        except Exception as e:
            logger.error(f"Ошибка остановки службы {service['name']}: {e}")
            traceback.print_exc()
            return False
    
    async def restart_service(self, service_id: str) -> bool:
        """Перезапустить службу"""
        logger.info(f"🔄 Перезапуск службы {service_id}...")
        
        # Останавливаем
        if self.services[service_id]['running']:
            stop_success = await self.stop_service(service_id)
            if not stop_success:
                return False
            
            # Даём время на полную остановку
            await asyncio.sleep(1.0)
        
        # Запускаем
        return await self.start_service(service_id)
    
    async def restart_all(self) -> Dict[str, bool]:
        """Перезапустить все запущенные службы"""
        results = {}
        
        # Определяем какие службы были запущены
        running_services = [
            service_id for service_id, info in self.services.items()
            if info['running']
        ]
        
        # Перезапускаем каждую
        for service_id in running_services:
            results[service_id] = await self.restart_service(service_id)
        
        return results
    
    def get_status(self, service_id: str) -> Optional[dict]:
        """Получить статус службы"""
        if service_id not in self.services:
            return None
        
        service = self.services[service_id]
        
        return {
            'name': service['name'],
            'description': service['description'],
            'running': service['running'],
            'started_at': service['started_at'],
            'last_error': service['last_error']
        }
    
    def get_all_status(self) -> Dict[str, dict]:
        """Получить статус всех служб"""
        return {
            service_id: self.get_status(service_id)
            for service_id in self.services.keys()
        }
    
    async def stop_all(self):
        """Остановить все службы"""
        for service_id in self.services.keys():
            if self.services[service_id]['running']:
                await self.stop_service(service_id)
