"""
Создаёт по одному тестовому Cover-устройству на каждое значение feature
(0-9) с короткими именами - чтобы посмотреть в веб-морде Akubela
(https://<IP_панели>/menu/apicontrol -> Устройства -> редактировать),
какая подпись "Выберите функцию" соответствует каждому feature. Нужно
найти "Ход рольставней" (или похожее с поддержкой хода/позиции).

Запуск:
    python probe_cover_features.py            # создать
    python probe_cover_features.py --cleanup  # удалить пробные
"""

import asyncio
import sys

from akubela_client import AkubelaClient

FEATURES = list(range(0, 10))


def ext_id(feature):
    return f"pcov{feature}"


async def main():
    cleanup = "--cleanup" in sys.argv

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    if cleanup:
        devices = await ak.device_get()
        ids = [d["device_id"] for d in devices if d.get("device_id_ext", "").startswith("pcov")]
        if ids:
            await ak.device_del(ids)
            print(f"Удалено {len(ids)} тестовых устройств.")
        else:
            print("Нечего удалять.")
        await ak.close()
        return

    for feature in FEATURES:
        await ak.device_add(
            device_id_ext=ext_id(feature),
            name=f"CovF{feature}",
            device_type="Cover",
            feature=feature,
            device_config={},
            area="aliving_room",
        )
        print(f"Создан 'CovF{feature}' (feature={feature})")

    print()
    print("Готово. В веб-морде Akubela (https://<IP>/menu/apicontrol -> Устройства)")
    print("откройте 'Редактировать' у каждого CovF0..CovF9 по очереди и посмотрите,")
    print("какая подпись стоит в поле 'Выберите функцию'. Найдите feature с 'ход'/позицией.")
    print()
    print("Когда закончите - удалите: python probe_cover_features.py --cleanup")

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
