"""
Изолированный тест: только Home Assistant, без Akubela.

Проверяет: подключение по WS, auth по HA_ACCESS_TOKEN, get_states,
и на закуску - реальный call_service (light.turn_on) на тестовую
сущность, если она передана аргументом.

Запуск:
    python test_ha_auth.py
    python test_ha_auth.py light.c98b58fc174249518b85dbc848b4793c
"""

import asyncio
import logging
import sys

from ha_client import HAClient
from config import HA_URL, HA_ACCESS_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def _ok(msg):
    print(f"  [OK] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


async def main():
    print("=" * 60)
    print("HA AUTH TEST")
    print(f"  HA_URL = {HA_URL}")
    print(f"  HA_ACCESS_TOKEN = {HA_ACCESS_TOKEN[:12]}...{HA_ACCESS_TOKEN[-6:]}"
          if len(HA_ACCESS_TOKEN) > 20 else "  HA_ACCESS_TOKEN = (пустой/дефолтный - замените!)")
    print("=" * 60)

    if HA_ACCESS_TOKEN.startswith("REPLACE_ME"):
        _fail("HA_ACCESS_TOKEN не задан (переменная окружения HA_ACCESS_TOKEN)")
        sys.exit(1)

    ha = HAClient()

    try:
        await asyncio.wait_for(ha.connect(), timeout=15)
        _ok(f"WS-подключение и авторизация прошли: {ha.url}")
    except asyncio.TimeoutError:
        _fail("не удалось подключиться/авторизоваться за 15 сек - проверьте HA_URL/HA_ACCESS_TOKEN")
        sys.exit(1)

    try:
        states = await ha.get_states()
        _ok(f"get_states вернул {len(states)} сущностей")
        by_domain = {}
        for s in states:
            d = s["entity_id"].split(".", 1)[0]
            by_domain[d] = by_domain.get(d, 0) + 1
        for d in sorted(by_domain, key=lambda k: -by_domain[k])[:10]:
            print(f"      {d}: {by_domain[d]}")
    except Exception as e:
        _fail(f"get_states упал: {e}")
        sys.exit(1)

    test_entity = sys.argv[1] if len(sys.argv) > 1 else None
    if test_entity:
        try:
            resp = await ha.call_service("light", "turn_on", test_entity)
            _ok(f"call_service light.turn_on на {test_entity}: {resp}")
        except Exception as e:
            _fail(f"call_service на {test_entity} упал: {e}")

    await ha.close()
    print("=" * 60)
    print("HA AUTH TEST: ВСЁ ОК" if not test_entity else "HA AUTH TEST: ВСЁ ОК (проверьте состояние сущности вручную)")


if __name__ == "__main__":
    asyncio.run(main())
