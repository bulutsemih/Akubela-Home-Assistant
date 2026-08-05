"""
Проверяет, реально ли принимает панель device_type "Air Quality Monitor" -
в full_sync он вернул success:true, но не появился в device/get. Пробуем
несколько вариантов написания и сверяем через device/get, какой реально
создаётся.

Запуск:
    python probe_air_quality.py            # создать пробные
    python probe_air_quality.py --cleanup  # удалить пробные
"""

import asyncio
import sys

from akubela_client import AkubelaClient

CANDIDATES = [
    "Air Quality Monitor",
    "AirQualityMonitor",
    "Air Quality Sensor",
    "AirQuality",
    "PM2.5 Sensor",
    "Gas Sensor",  # контрольный - точно рабочий, для сравнения
]


def ext_id(i):
    return f"probe_air_{i}"


async def main():
    cleanup = "--cleanup" in sys.argv

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    if cleanup:
        devices = await ak.device_get()
        ids = [d["device_id"] for d in devices if d.get("device_id_ext", "").startswith("probe_air_")]
        if ids:
            await ak.device_del(ids)
            print(f"Удалено {len(ids)} пробных устройств.")
        else:
            print("Нечего удалять.")
        await ak.close()
        return

    for i, device_type in enumerate(CANDIDATES):
        try:
            resp = await ak.device_add(
                device_id_ext=ext_id(i),
                name=f"AQ Probe: {device_type}",
                device_type=device_type,
                feature=0,
                device_config={},
                area="aliving_room",
            )
            print(f"device_add({device_type!r}) -> success={resp.get('success')}")
        except Exception as e:
            print(f"device_add({device_type!r}) -> ОШИБКА: {e}")

    await asyncio.sleep(1)

    print("\nПроверяю device/get - что из этого реально создалось:")
    devices = await ak.device_get()
    created_ext_ids = {d.get("device_id_ext") for d in devices}

    for i, device_type in enumerate(CANDIDATES):
        created = ext_id(i) in created_ext_ids
        mark = "[СОЗДАЛОСЬ]" if created else "[НЕ появилось]"
        print(f"  {device_type!r}: {mark}")

    print("\nКогда закончите - удалите: python probe_air_quality.py --cleanup")
    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
