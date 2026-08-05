"""
Комплексный тест "поехали пробовать":
1. Подключается к HA и Akubela, авторизуется в обоих.
2. Делает один проход full_sync (регистрирует light/switch/sensor/climate/
   input_boolean из HA в Akubela, если ещё не зарегистрированы).
3. Запускает ЖИВОЙ форвардинг в обе стороны (state_forwarder + control_bridge) -
   те же классы, что использует main.py - и одновременно печатает всё, что
   происходит, 90 секунд. Переключайте устройства в HA и на панели руками.

Запуск:
    python test_roundtrip.py
"""

import asyncio
import logging

from ha_client import HAClient
from akubela_client import AkubelaClient
from device_registry import StateStore
from sync_service import SyncService
from control_bridge import ControlBridge
from state_forwarder import StateForwarder
from scene_sync_service import SceneSyncService
from scene_control_bridge import SceneControlBridge
from config import STATE_FILE, SCENE_STATE_FILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("roundtrip")

LISTEN_SECONDS = 90


async def watch_ha(ha):
    async for event in ha.events():
        if event.get("event_type") != "state_changed":
            continue
        d = event["data"]
        old = d.get("old_state") or {}
        new = d.get("new_state") or {}
        if old.get("state") != new.get("state"):
            print(f"[HA event]      {d['entity_id']}: {old.get('state')!r} -> {new.get('state')!r}")
        old_temp = (old.get("attributes") or {}).get("temperature")
        new_temp = (new.get("attributes") or {}).get("temperature")
        if new_temp is not None and old_temp != new_temp:
            print(f"[HA event]      {d['entity_id']}: temperature {old_temp!r} -> {new_temp!r}")


async def watch_akubela(ak):
    async for event in ak.events():
        et = event.get("event_type")
        if et == "state_changed":
            d = event["data"]
            old = d.get("old_state") or {}
            new = d.get("new_state") or {}
            if old.get("state") != new.get("state"):
                print(f"[Akubela event] {new['entity_id']}: {old.get('state')!r} -> {new.get('state')!r}")
            old_temp = (old.get("attributes") or {}).get("temperature")
            new_temp = (new.get("attributes") or {}).get("temperature")
            if new_temp is not None and old_temp != new_temp:
                print(f"[Akubela event] {new['entity_id']}: temperature {old_temp!r} -> {new_temp!r}")

            # ДИАГНОСТИКА: полный дамп атрибутов света при изменении -
            # нужно, чтобы увидеть реальное имя/формат поля цветовой
            # температуры у Akubela (color_temp пересылка сейчас выключена
            # из-за эхо-цикла, см. FORWARD_LIGHT_COLOR_TEMP в config.py).
            if new.get("entity_id", "").startswith("light.") and old.get("attributes") != new.get("attributes"):
                print(f"[Akubela RAW ATTRS] {new['entity_id']}: {new.get('attributes')}")
        elif et in ("ak_device_event", "ak_scene_event"):
            print(f"[Akubela {et}] action={event['data'].get('action')}")


async def main():
    ha = HAClient()
    ak = AkubelaClient()

    print("Подключаюсь к HA...")
    await ha.connect()
    await ha.subscribe_state_changed()
    print("OK")

    print("Подключаюсь к Akubela...")
    await ak.connect()
    await ak.subscribe_device_events()
    await ak.subscribe_scene_events()
    await ak.subscribe_state_changed()
    print("OK")

    store = StateStore(STATE_FILE)
    scene_store = StateStore(SCENE_STATE_FILE)
    sync = SyncService(ha, ak, store)
    scene_sync = SceneSyncService(ha, ak, scene_store)

    print("Делаю full_sync (устройства)...")
    await sync.full_sync()

    print("Делаю full_sync (сцены)...")
    await scene_sync.full_sync()

    print()
    print("Зарегистрированные у нас устройства (entity_id -> ak_domain.device_id):")
    for entity_id, rec in store.entities.items():
        if rec.get("device_id"):
            print(f"  {entity_id} -> {rec.get('ak_domain')}.{rec['device_id']}")

    print()
    print("Зарегистрированные у нас сцены (entity_id -> scene_id):")
    for entity_id, rec in scene_store.entities.items():
        if rec.get("scene_id"):
            print(f"  {entity_id} -> scene.{rec['scene_id']}")

    print()
    print(f"Запускаю живой форвардинг в обе стороны на {LISTEN_SECONDS} секунд - "
          f"переключайте устройства в HA и на панели, смотрите, что прилетает "
          f"и что реально долетает на другую сторону.")
    print("(Ctrl+C чтобы выйти раньше)")
    print()

    control_bridge = ControlBridge(ha, ak, store)
    state_forwarder = StateForwarder(ha, ak, store)
    scene_control = SceneControlBridge(ha, ak, scene_store)

    tasks = [
        asyncio.create_task(watch_ha(ha)),
        asyncio.create_task(watch_akubela(ak)),
        asyncio.create_task(control_bridge.run_forever()),
        asyncio.create_task(state_forwarder.run_forever()),
        asyncio.create_task(scene_control.run_forever()),
    ]

    try:
        await asyncio.sleep(LISTEN_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await ha.close()
        await ak.close()

    print("Готово.")


if __name__ == "__main__":
    asyncio.run(main())
