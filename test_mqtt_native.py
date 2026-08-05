"""
Изолированный тест: подключение к MQTT-брокеру + публикация нативных
датчиков Akubela через MQTT Discovery, без остального бриджа.

Запуск:
    python test_mqtt_native.py
"""

import asyncio
import logging
import sys

if sys.platform == "win32":
    # aiomqtt (через paho-mqtt) использует add_reader/add_writer, которых
    # нет в дефолтном ProactorEventLoop на Windows - переключаемся на
    # SelectorEventLoop до создания цикла событий.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from akubela_client import AkubelaClient
from device_registry import StateStore
from native_sensor_bridge import NativeSensorBridge
from config import STATE_FILE, MQTT_HOST, MQTT_PORT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

RUN_SECONDS = 30


async def main():
    print("=" * 60)
    print("MQTT NATIVE SENSOR TEST")
    print(f"  MQTT_HOST:PORT = {MQTT_HOST}:{MQTT_PORT}")
    print("=" * 60)

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()
    await ak.subscribe_state_changed()
    print("OK")

    store = StateStore(STATE_FILE)
    bridge = NativeSensorBridge(ak, store)

    print(f"Публикую нативные датчики в MQTT Discovery на {RUN_SECONDS} секунд...")
    print("Проверьте в HA: Настройки -> Устройства -> MQTT -> должно появиться "
          "устройство 'Akubela HyPanel (родные устройства)'")

    task = asyncio.create_task(bridge.run_forever())
    try:
        await asyncio.sleep(RUN_SECONDS)
    finally:
        task.cancel()
        await ak.close()

    print("Готово.")


if __name__ == "__main__":
    asyncio.run(main())
