"""
Изолированный тест: только панель Akubela, без Home Assistant.

Проверяет: подключение по WS, auth по AKUBELA_ACCESS_TOKEN, device/get,
scene/get, и опционально - реальный call_service на тестовую сущность
(например "light.<device_id>" уже зарегистрированного виртуального
устройства).

Запуск:
    python test_akubela_auth.py
    python test_akubela_auth.py light.80bdd7941fdf47348fac367bc0fc7e9f
"""

import asyncio
import logging
import sys

from akubela_client import AkubelaClient
from config import AKUBELA_HOST, AKUBELA_ACCESS_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def _ok(msg):
    print(f"  [OK] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


async def main():
    print("=" * 60)
    print("AKUBELA AUTH TEST")
    print(f"  AKUBELA_HOST = {AKUBELA_HOST}")
    print(f"  AKUBELA_ACCESS_TOKEN = {AKUBELA_ACCESS_TOKEN[:12]}...{AKUBELA_ACCESS_TOKEN[-6:]}"
          if len(AKUBELA_ACCESS_TOKEN) > 20 else "  AKUBELA_ACCESS_TOKEN = (пустой/дефолтный - замените!)")
    print("=" * 60)

    if AKUBELA_ACCESS_TOKEN.startswith("REPLACE_ME"):
        _fail("AKUBELA_ACCESS_TOKEN не задан (переменная окружения AKUBELA_ACCESS_TOKEN)")
        sys.exit(1)

    ak = AkubelaClient()

    try:
        await asyncio.wait_for(ak.connect(), timeout=15)
        _ok(f"WS-подключение и авторизация прошли: {ak.url}")
    except asyncio.TimeoutError:
        _fail("не удалось подключиться/авторизоваться за 15 сек - проверьте "
              "AKUBELA_HOST/AKUBELA_ACCESS_TOKEN (токен мог истечь при перезагрузке панели)")
        sys.exit(1)

    try:
        devices = await ak.device_get()
        _ok(f"device/get вернул {len(devices)} устройств")
        for d in devices[:10]:
            print(f"      {d.get('device_id_ext')}: {d.get('name')} "
                  f"({d.get('device_type')}/{d.get('feature')}) -> device_id={d.get('device_id')}")
    except Exception as e:
        _fail(f"device/get упал: {e}")
        sys.exit(1)

    try:
        scenes = await ak.scene_get()
        _ok(f"scene/get вернул {len(scenes)} сцен")
    except Exception as e:
        _fail(f"scene/get упал: {e}")

    test_entity = sys.argv[1] if len(sys.argv) > 1 else None
    if test_entity:
        domain = test_entity.split(".", 1)[0]
        try:
            resp = await ak.call_service(domain, "turn_on", test_entity)
            _ok(f"call_service {domain}.turn_on на {test_entity}: {resp}")
        except Exception as e:
            _fail(f"call_service на {test_entity} упал: {e}")

    await ak.close()
    print("=" * 60)
    print("AKUBELA AUTH TEST: ВСЁ ОК")


if __name__ == "__main__":
    asyncio.run(main())
