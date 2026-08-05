"""
С тех пор как sync_service.py перестал автоматически "чинить" уже
созданные устройства (чтобы не затирать ручные правки через веб-морду
Akubela), нужен способ сознательно откатить конкретное устройство к
дефолту из кода - например, если вы улучшили маппинг в
device_registry.py и хотите применить его к уже созданному виджету, а
не только к новым.

Удаляет устройство с панели и его запись из bridge_state.json - при
следующем full_sync оно пересоздастся заново с текущими дефолтами кода.

Запуск:
    python force_resync.py light.bridge_test_devices_virtualnye_bridge_test_light
    python force_resync.py --list           # посмотреть все зарегистрированные entity_id
"""

import asyncio
import sys

from akubela_client import AkubelaClient
from device_registry import StateStore
from config import STATE_FILE


async def main():
    store = StateStore(STATE_FILE)

    if "--list" in sys.argv or len(sys.argv) < 2:
        print("Зарегистрированные устройства:")
        for entity_id, rec in store.entities.items():
            print(f"  {entity_id} -> {rec.get('ak_domain')}.{rec.get('device_id')} "
                  f"({rec.get('device_type')}/{rec.get('feature')})")
        if len(sys.argv) < 2:
            print("\nУкажите entity_id: python force_resync.py <entity_id>")
        return

    entity_id = sys.argv[1]
    rec = store.get(entity_id)
    if not rec:
        print(f"{entity_id} не найден в {STATE_FILE}")
        return

    device_id = rec.get("device_id")

    ak = AkubelaClient()
    await ak.connect()

    if device_id:
        try:
            await ak.device_del([device_id])
            print(f"Удалено с панели: {entity_id} ({device_id})")
        except Exception as e:
            print(f"Не удалось удалить с панели (возможно, уже удалено): {e}")

    store.remove(entity_id)
    store.save()
    print(f"Удалено из {STATE_FILE}. При следующем full_sync (test_connect.py / main.py) "
          f"пересоздастся с текущими дефолтами кода.")

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
