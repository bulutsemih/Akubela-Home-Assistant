"""
Проверяет ak_api/id_ext/next - метод, который sync_service.py пытается
использовать для получения "правильного" ID устройства от самой
панели (вместо самодельного формата "ha<хэш>", на который жалуется
веб-форма редактирования). Если вернёт пусто - sync_service.py сам
откатится на хэш автоматически, но полезно понять реальный формат
ответа панели, чтобы доработать позже.

Запуск:
    python test_id_ext.py
"""

import asyncio

from akubela_client import AkubelaClient


async def main():
    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    print("Запрашиваю ak_api/id_ext/next напрямую (сырой ответ)...")
    raw = await ak.send_command({"type": "ak_api/id_ext/next", "mode": 1})
    print(f"Сырой ответ: {raw!r}")

    print("\nЧерез обёртку id_ext_next():")
    result = await ak.id_ext_next()
    print(f"Результат: {result!r} (тип: {type(result).__name__})")

    if result:
        print("\n[OK] Метод работает.")
    else:
        print("\n[ИНФО] Пусто - sync_service.py в этом случае сам использует хэш "
              "как раньше, ничего не сломается. Но если в сыром ответе выше видно "
              "ID под другим ключом (не 'result') - пришлите его, поправим akubela_client.py.")

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
