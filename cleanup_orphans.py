"""
Показывает устройства на панели Akubela, которые НЕ зарегистрированы
нашим бриджем (нет в bridge_state.json) - как правило, это старые
тестовые устройства из ручных экспериментов с ak_api/device/add.

По умолчанию - только показывает список (dry-run). Чтобы реально
удалить - передайте --delete.

Запуск:
    python cleanup_orphans.py            # только посмотреть
    python cleanup_orphans.py --delete   # удалить после подтверждения
"""

import asyncio
import sys

from akubela_client import AkubelaClient
from device_registry import StateStore
from config import STATE_FILE


async def main():
    delete = "--delete" in sys.argv

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    store = StateStore(STATE_FILE)
    our_device_ids = {rec["device_id"] for rec in store.entities.values() if rec.get("device_id")}

    devices = await ak.device_get()
    orphans = [d for d in devices if d.get("device_id") not in our_device_ids]

    print(f"\nВсего устройств на панели: {len(devices)}")
    print(f"Наших (из bridge_state.json): {len(our_device_ids)}")
    print(f"Осиротевших (не наши): {len(orphans)}\n")

    for d in orphans:
        print(f"  {d.get('device_id_ext'):>8}  {d.get('name'):<20} "
              f"{d.get('device_type')}/{d.get('feature')}  device_id={d.get('device_id')}")

    if not orphans:
        print("Нечего чистить.")
        await ak.close()
        return

    if not delete:
        print(f"\nЭто был dry-run. Чтобы удалить все {len(orphans)} устройств(а) выше, "
              f"запустите: python cleanup_orphans.py --delete")
        await ak.close()
        return

    confirm = input(f"\nТочно удалить все {len(orphans)} устройств(а) выше? (yes/no): ")
    if confirm.strip().lower() != "yes":
        print("Отменено.")
        await ak.close()
        return

    device_ids = [d["device_id"] for d in orphans]
    resp = await ak.device_del(device_ids)
    print("Результат:", resp)

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
