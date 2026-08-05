"""
Диагностика яркости: пробует НЕСКОЛЬКО вариантов имени параметра для
управления яркостью (brightness 0-255 как в HA/jaaneo, brightness_pct
0-100 как в официальном REST-примере Akubela, и пару альтернатив),
и после каждого сразу проверяет через get_states(), какой из них
реально подействовал.

Запуск:
    python test_brightness_debug.py
"""

import asyncio

from akubela_client import AkubelaClient
from device_registry import StateStore
from config import STATE_FILE

LIGHT_ENTITY_ID = "light.bridge_test_devices_virtualnye_bridge_test_light"

# (описание, kwargs для call_service, ожидаемое значение brightness 0-255 после)
CANDIDATES = [
    ("brightness=120 (0-255, как HA/jaaneo)", {"brightness": 120}, 120),
    ("brightness_pct=47 (0-100, как в официальном REST-примере Akubela)", {"brightness_pct": 47}, round(47 * 255 / 100)),
    ("level=120", {"level": 120}, 120),
    ("dimmer=120", {"dimmer": 120}, 120),
    ("dimmer_value=47 (0-100)", {"dimmer_value": 47}, round(47 * 255 / 100)),
]


async def main():
    store = StateStore(STATE_FILE)
    rec = store.get(LIGHT_ENTITY_ID)
    if not rec or not rec.get("device_id"):
        print(f"Не нашёл {LIGHT_ENTITY_ID} в {STATE_FILE} - запустите сначала test_connect.py")
        return

    akubela_entity_id = f"{rec['ak_domain']}.{rec['device_id']}"
    print(f"Akubela entity: {akubela_entity_id}\n")

    ak = AkubelaClient()
    await ak.connect()

    for label, kwargs, expected in CANDIDATES:
        print(f"--- Пробую: {label} ---")
        print(f"  call_service kwargs: {kwargs}")
        try:
            resp = await ak.call_service("light", "turn_on", akubela_entity_id, **kwargs)
            print(f"  ответ: success={resp.get('success')}")
        except Exception as e:
            print(f"  ОШИБКА: {e}")
            print()
            continue

        await asyncio.sleep(1)

        states = await ak.get_states()
        after = next((s for s in states if s["entity_id"] == akubela_entity_id), None)
        actual = (after or {}).get("attributes", {}).get("brightness")
        actual_pct = (after or {}).get("attributes", {}).get("brightness_pct")

        match = "[РАБОТАЕТ]" if actual == expected else "[не то]"
        print(f"  результат: brightness={actual} (attrs.brightness_pct={actual_pct}) "
              f"- ожидали ~{expected} {match}")
        print()

    await ak.close()
    print("Готово. Смотрите строки [РАБОТАЕТ] выше - это и есть правильное имя параметра.")


if __name__ == "__main__":
    asyncio.run(main())
