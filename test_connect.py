"""
Ручная проверка: подключиться к HA и Akubela, вывести списки состояний
и текущих устройств панели, сделать один проход синхронизации.

Запуск:  python test_connect.py
"""

import asyncio
import logging

from ha_client import HAClient
from akubela_client import AkubelaClient
from device_registry import StateStore
from sync_service import SyncService
from config import STATE_FILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


async def main():
    ha = HAClient()
    ak = AkubelaClient()

    print("Connecting HA...")
    await ha.connect()
    print("Connecting Akubela...")
    await ak.connect()

    states = await ha.get_states()
    print(f"HA states: {len(states)}")

    devices = await ak.device_get()
    print(f"Akubela devices already registered: {len(devices)}")
    for d in devices[:10]:
        print(" ", d)

    store = StateStore(STATE_FILE)
    sync = SyncService(ha, ak, store)
    await sync.full_sync()

    await ha.close()
    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
