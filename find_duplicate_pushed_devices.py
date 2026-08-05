"""
Находит устройства НА AKUBELA с повторяющимся именем среди тех, что
создал наш же sync_service.py (device_add) - обычно означает, что
full_sync выполнился дважды параллельно для одной и той же HA-сущности
(например два процесса main.py/test_connect.py работали одновременно и
гонялись за bridge_state.json - см. ARCHITECTURE.md).

Сверяется с bridge_state.json: для каждой группы одинаковых имён
оставляет device_id, который РЕАЛЬНО сейчас записан в store для
какой-то сущности, и предлагает удалить остальные как дубли.

Запуск:
    python find_duplicate_pushed_devices.py            # только показать
    python find_duplicate_pushed_devices.py --delete    # удалить лишние
"""

import asyncio
import sys

from akubela_client import AkubelaClient
from device_registry import StateStore
from config import STATE_FILE


async def main():
    delete = "--delete" in sys.argv

    store = StateStore(STATE_FILE)
    tracked_device_ids = {
        rec["device_id"] for rec in store.entities.values() if rec.get("device_id")
    }
    print(f"В {STATE_FILE} сейчас отслеживается {len(tracked_device_ids)} device_id.")

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()
    devices = await ak.device_get()
    print(f"Всего устройств на панели: {len(devices)}\n")

    by_name = {}
    for d in devices:
        by_name.setdefault(d.get("name", "?"), []).append(d)

    duplicates = {name: group for name, group in by_name.items() if len(group) > 1}

    if not duplicates:
        print("Дублей по имени не найдено - чисто.")
        await ak.close()
        return

    to_delete = []
    for name, group in duplicates.items():
        tracked = [d for d in group if d["device_id"] in tracked_device_ids]
        untracked = [d for d in group if d["device_id"] not in tracked_device_ids]

        print(f"'{name}': {len(group)} штук на панели")
        for d in group:
            mark = "[ОТСЛЕЖИВАЕТСЯ в store]" if d["device_id"] in tracked_device_ids else "[лишний]"
            print(f"    device_id={d['device_id']} ({d.get('device_type')}/{d.get('feature')}) {mark}")

        if tracked:
            # Оставляем отслеживаемый(е), остальные - явные дубли от гонки
            to_delete.extend(untracked)
        else:
            # Ни один не в store - оставляем первый по списку, остальные лишние
            # (осторожно: если это не наши устройства вовсе, а совпадение
            # имён у реальных родных устройств - проверьте руками перед --delete)
            to_delete.extend(group[1:])
            print("    (ни один не отслеживается в store - оставляю первый по умолчанию, "
                  "проверьте руками, что это правда наш дубль, а не совпадение имён)")
        print()

    print(f"Итого лишних для удаления: {len(to_delete)}")

    if delete:
        ids = [d["device_id"] for d in to_delete]
        print(f"Удаляю {len(ids)} лишних устройств пачками по 10...")
        for i in range(0, len(ids), 10):
            batch = ids[i:i + 10]
            try:
                await ak.device_del(batch)
                print(f"  удалено {i + len(batch)}/{len(ids)}...")
            except Exception as e:
                print(f"  пачка {i}: {e!r} (продолжаю)")
            await asyncio.sleep(1)
        print("Готово.")
    else:
        print("\nЭто был dry-run. Чтобы удалить лишние по-настоящему:")
        print("    python find_duplicate_pushed_devices.py --delete")

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
