"""
Проверяет команду разблокировки домофона на реальном устройстве - формат
не подтверждён (см. native_lock_bridge.py), поэтому пробуем несколько
вариантов и смотрим сырой ответ панели.

Сначала запустите probe_native_entities.py --search x915 (или как у вас
называется домофон), возьмите оттуда entity_id, передайте сюда.

Запуск:
    python test_doorphone_unlock.py doorphone.xxxxxxxx
"""

import asyncio
import sys

from akubela_client import AkubelaClient

CANDIDATES = [
    ("service=unlock, lock=[0]", "unlock", {"lock": [0]}),
    ("service=unlock, без параметров", "unlock", {}),
    ("service=open, без параметров", "open", {}),
    ("service=turn_on, без параметров", "turn_on", {}),
]


async def main():
    if len(sys.argv) < 2:
        print("Использование: python test_doorphone_unlock.py <entity_id>")
        print("entity_id возьмите из probe_native_entities.py")
        return

    entity_id = sys.argv[1]
    domain = entity_id.split(".", 1)[0]

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    print(f"\nПробую открыть {entity_id} (домен {domain}) разными вариантами.")
    print("Смотрите на реальный домофон/дверь - сработает физически только один (если сработает).\n")

    for label, service, extra in CANDIDATES:
        print(f"--- {label} ---")
        try:
            resp = await ak.call_service(domain, service, entity_id, **extra)
            print(f"  ответ: {resp}")
        except Exception as e:
            print(f"  ОШИБКА: {e}")
        print()
        await asyncio.sleep(3)  # пауза между попытками, чтобы отличить, какая сработала физически

    await ak.close()
    print("Готово. Какой вариант реально открыл дверь? Пришлите label - зафиксируем в native_lock_bridge.py.")


if __name__ == "__main__":
    asyncio.run(main())
