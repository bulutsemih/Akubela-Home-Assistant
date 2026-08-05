"""
Находит и чистит "осиротевшие" retained MQTT discovery-конфиги нашего
устройства "Akubela HyPanel (родные устройства)" - те, чей источник на
Akubela уже не существует (например от старых экспериментов/пробников),
но конфиг остался висеть в брокере, потому что ни один наш мост его
больше не видит и, соответственно, не может сам отправить пустой
payload для удаления.

Работает независимо от Akubela - смотрит прямо в MQTT-брокер: собирает
все retained discovery-конфиги с identifiers=["akubela_panel_native"],
затем спрашивает Akubela, какие из соответствующих entity_id реально
существуют сейчас, и предлагает удалить те, для которых источника нет.

Запуск:
    python cleanup_mqtt_ghosts.py             # только показать сводку по именам
    python cleanup_mqtt_ghosts.py --verbose    # + полный список каждой сироты
    python cleanup_mqtt_ghosts.py --delete     # удалить (спросит подтверждение, если >20 шт)
    python cleanup_mqtt_ghosts.py --delete --yes   # удалить без вопросов
"""

import asyncio
import json
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt

from config import MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, MQTT_DISCOVERY_PREFIX
from akubela_client import AkubelaClient

PLATFORMS = ["sensor", "binary_sensor", "switch", "lock", "camera", "button"]
COLLECT_TIMEOUT_SEC = 3


async def collect_our_configs(mqtt):
    """Собирает все retained /config под нашим device identifier."""
    found = {}  # topic -> config dict

    async def _collect():
        async for message in mqtt.messages:
            topic = str(message.topic)
            if not message.payload:
                continue
            try:
                config = json.loads(message.payload.decode())
            except Exception:
                continue
            device = config.get("device", {})
            if "akubela_panel_native" in (device.get("identifiers") or []):
                found[topic] = config

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)  # даём таску начать слушать до подписки
    for platform in PLATFORMS:
        await mqtt.subscribe(f"{MQTT_DISCOVERY_PREFIX}/{platform}/+/config")
    await asyncio.sleep(COLLECT_TIMEOUT_SEC)  # даём время брокеру отдать все retained
    task.cancel()
    return found


def expected_topics_for_live_entities(states):
    """
    То же вычисление object_id, что и в native_*_bridge.py:
    f"akubela_{entity_id.replace('.', '_')}" - чтобы понять, каким
    ЖИВЫМ сущностям Akubela сейчас соответствуют какие topic'и.
    """
    expected = set()
    for state in states:
        entity_id = state["entity_id"]
        object_id = f"akubela_{entity_id.replace('.', '_')}"
        for platform in PLATFORMS:
            expected.add(f"{MQTT_DISCOVERY_PREFIX}/{platform}/{object_id}/config")
    return expected


async def main():
    delete = "--delete" in sys.argv

    print("Подключаюсь к Akubela, чтобы узнать текущий список живых сущностей...")
    ak = AkubelaClient()
    await ak.connect()
    states = await ak.get_states()
    live_topics = expected_topics_for_live_entities(states)
    await ak.close()
    print(f"Живых сущностей на панели: {len(states)}")

    print(f"\nПодключаюсь к MQTT {MQTT_HOST}:{MQTT_PORT} и собираю retained-конфиги "
          f"нашего устройства (жду {COLLECT_TIMEOUT_SEC}с)...")
    async with aiomqtt.Client(
        hostname=MQTT_HOST, port=MQTT_PORT,
        username=MQTT_USERNAME or None, password=MQTT_PASSWORD or None,
    ) as mqtt:
        found = await collect_our_configs(mqtt)
        print(f"Найдено конфигов нашего устройства в MQTT: {len(found)}\n")

        orphans = {t: c for t, c in found.items() if t not in live_topics}
        alive = {t: c for t, c in found.items() if t in live_topics}

        print(f"Живые (есть соответствующая сущность на Akubela сейчас): {len(alive)}")
        print(f"Осиротевшие (источника на Akubela больше нет): {len(orphans)}\n")

        if not orphans:
            print("Осиротевших конфигов не найдено - чистить нечего.")
            return

        # Сводка по именам - чтобы было видно "что именно" удаляется,
        # а не просто список хэшей unique_id. Если видите тут знакомые
        # имена вроде "Klapan"/"Akubela Test" с большими числами - это
        # и есть дубли от циклов "упал посередине full_sync, не
        # сохранил, создал заново" (см. ARCHITECTURE.md).
        by_name = {}
        for config in orphans.values():
            name = config.get("name") or "(без имени)"
            by_name[name] = by_name.get(name, 0) + 1

        print("Сводка осиротевших по именам:")
        for name, count in sorted(by_name.items(), key=lambda x: -x[1]):
            print(f"  {name!r}: {count}")

        show_details = "--verbose" in sys.argv
        if show_details:
            print()
            for topic, config in orphans.items():
                print(f"  СИРОТА: {topic}")
                print(f"    name: {config.get('name')!r}, unique_id: {config.get('unique_id')!r}")

        if delete:
            if len(orphans) > 20 and "--yes" not in sys.argv:
                answer = input(
                    f"\nЭто много ({len(orphans)}) - точно удалить ВСЕ перечисленные выше "
                    f"из HA? [yes/no]: "
                )
                if answer.strip().lower() != "yes":
                    print("Отменено.")
                    return
            print(f"\nУдаляю {len(orphans)} осиротевших конфигов...")
            for topic in orphans:
                await mqtt.publish(topic, payload="", retain=True)
            print("Готово. Обновите страницу HA через несколько секунд.")
        else:
            print(f"\nЭто было ТОЛЬКО показано (dry-run, {len(orphans)} шт). "
                  f"Добавьте --verbose для полного списка. Чтобы удалить по-настоящему:")
            print("    python cleanup_mqtt_ghosts.py --delete")


if __name__ == "__main__":
    asyncio.run(main())
