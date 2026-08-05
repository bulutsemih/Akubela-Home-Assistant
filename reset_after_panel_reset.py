"""
Для ситуации "сбросил панель / бридж упал посередине full_sync и
наплодил дублей" (см. ARCHITECTURE.md - падение одной части процесса
раньше могло обрывать full_sync посередине, не сохранив прогресс).

Безопасно только если вы УВЕРЕНЫ, что все устройства на Akubela сейчас
созданы нашим же бриджем (например сразу после сброса панели) - скрипт
удаляет ВСЕ устройства с панели без разбора, не только дубли.

Делает:
  1. Удаляет ВСЕ устройства с Akubela (device_get + device_del).
  2. Удаляет ВСЕ сцены с Akubela (scene_get + scene_del).
  3. Удаляет локальные bridge_state.json / bridge_scene_state.json.

После этого запустите test_connect.py - всё пересоздастся с нуля,
идемпотентно, без дублей.

Запуск:
    python reset_after_panel_reset.py            # спросит подтверждение
    python reset_after_panel_reset.py --yes       # без вопросов
"""

import asyncio
import os
import sys

from akubela_client import AkubelaClient
from config import STATE_FILE, SCENE_STATE_FILE


async def main():
    confirmed = "--yes" in sys.argv

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    devices = await ak.device_get()
    scenes = await ak.scene_get()

    print(f"\nНайдено на панели: {len(devices)} устройств, {len(scenes)} сцен.")
    if devices:
        names = {}
        for d in devices:
            names[d.get("name", "?")] = names.get(d.get("name", "?"), 0) + 1
        print("По именам:")
        for name, count in sorted(names.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}")

    if not confirmed:
        answer = input(
            f"\nУдалить ВСЕ {len(devices)} устройств и {len(scenes)} сцен с панели "
            f"и локальные файлы состояния ({STATE_FILE}, {SCENE_STATE_FILE})? [yes/no]: "
        )
        if answer.strip().lower() != "yes":
            print("Отменено.")
            await ak.close()
            return

    if devices:
        device_ids = [d["device_id"] for d in devices if d.get("device_id")]
        print(f"\nУдаляю {len(device_ids)} устройств пачками по 10...")
        # ВАЖНО: одним запросом на 126 устройств панель не успевала
        # ответить за 15с (TimeoutError) - похоже, ей нужно время
        # обработать каждое, а не просто принять список. Пачками
        # помельче + пауза между ними - надёжнее. Если конкретная пачка
        # всё равно не ответила вовремя - устройства из неё, скорее
        # всего, всё равно удалились (панель просто не успела
        # подтвердить) - не останавливаемся, идём дальше.
        BATCH = 10
        deleted = 0
        for i in range(0, len(device_ids), BATCH):
            batch = device_ids[i:i + BATCH]
            try:
                await ak.device_del(batch)
                deleted += len(batch)
                print(f"  удалено {deleted}/{len(device_ids)}...")
            except Exception as e:
                print(f"  пачка {i}-{i+len(batch)}: {e!r} (скорее всего всё равно удалилось, продолжаю)")
            await asyncio.sleep(1)

    if scenes:
        scene_ids = [s["scene_id"] for s in scenes if s.get("scene_id")]
        if scene_ids:
            print(f"Удаляю {len(scene_ids)} сцен пачками по 10...")
            for i in range(0, len(scene_ids), 10):
                batch = scene_ids[i:i + 10]
                try:
                    await ak.scene_del(batch)
                except Exception as e:
                    print(f"  пачка сцен {i}-{i+len(batch)}: {e!r} (продолжаю)")
                await asyncio.sleep(1)

    for path in (STATE_FILE, SCENE_STATE_FILE):
        if os.path.exists(path):
            os.remove(path)
            print(f"Удалён локальный файл: {path}")

    print("\nПроверяю, что реально осталось на панели...")
    try:
        remaining_devices = await ak.device_get()
        remaining_scenes = await ak.scene_get()
        print(f"Осталось устройств: {len(remaining_devices)}, сцен: {len(remaining_scenes)}")
        if remaining_devices:
            print("Если что-то осталось - запустите скрипт ещё раз, он доудалит остальное.")
    except Exception as e:
        print(f"Не удалось проверить финальное состояние: {e!r} "
              f"(запустите test_akubela_auth.py, чтобы посмотреть вручную)")

    print("\nГотово. Теперь запустите: python test_connect.py")
    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
