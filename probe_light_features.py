"""
Создаёт по одному тестовому Light-устройству на каждое известное
значение feature (0-6, из реального дампа трафика) с понятными
именами - чтобы визуально посмотреть на панели, у какого именно
виджета есть И яркость, И цветовая температура одновременно.

После того как найдёте нужный - скажите номер, и я поправлю
LIGHT_FEATURE_COLOR_TEMP в device_registry.py на правильное значение
(сейчас там ошибочно 2 - у него, судя по вашим словам, нет яркости).

Запуск:
    python probe_light_features.py           # создать
    python probe_light_features.py --cleanup # удалить все тестовые
"""

import asyncio
import sys

from akubela_client import AkubelaClient

FEATURES = list(range(0, 16))

_CT = {"color_temp_unit": 0, "color_temp_min": 2700, "color_temp_max": 6500}

# 0-6 - точно по реальному дампу трафика (0,1,3,5 пустые; 2,4,6 - color_temp).
# 7-15 не проверены - подаём color_temp bounds на всякий случай, панель
# должна проигнорировать лишнее поле, если оно не нужно для этого feature.
DEVICE_CONFIG_BY_FEATURE = {
    0: {}, 1: {}, 2: _CT, 3: {}, 4: _CT, 5: {}, 6: _CT,
}
for f in range(7, 16):
    DEVICE_CONFIG_BY_FEATURE[f] = _CT


def ext_id(feature):
    return f"probe_light_{feature}"


async def main():
    cleanup = "--cleanup" in sys.argv

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    if cleanup:
        devices = await ak.device_get()
        ids_to_del = [
            d["device_id"] for d in devices
            if d.get("device_id_ext", "").startswith("probe_light_")
        ]
        if ids_to_del:
            await ak.device_del(ids_to_del)
            print(f"Удалено {len(ids_to_del)} тестовых устройств.")
        else:
            print("Нечего удалять.")
        await ak.close()
        return

    for feature in FEATURES:
        await ak.device_add(
            device_id_ext=ext_id(feature),
            name=f"Feature {feature}",
            device_type="Light",
            feature=feature,
            device_config=DEVICE_CONFIG_BY_FEATURE[feature],
            area="aliving_room",
        )
        print(f"Создан виджет 'Feature {feature}' (feature={feature})")

    print()
    print("Готово. Откройте панель, найдите 7 виджетов 'Feature 0'..'Feature 6',")
    print("посмотрите, у какого есть И яркость, И цветовая температура -")
    print("и пришлите мне номер.")
    print()
    print("Когда закончите - удалите тестовые: python probe_light_features.py --cleanup")

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
