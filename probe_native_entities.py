"""
Разведка: подключается к Akubela и выводит ВСЕ родные сущности панели
(get_states()) сгруппированные по домену - entity_id, state, attributes.

Нужно перед тем, как строить мост для домофона/камеры, чтобы увидеть
реальную схему (какой домен у X915S, есть ли там "monitor" с RTSP-URL,
как называется состояние и т.д.), а не гадать по официальному документу.

Запуск:
    python probe_native_entities.py                  # все домены
    python probe_native_entities.py camera lock       # только эти домены
    python probe_native_entities.py --search x915      # поиск по подстроке в entity_id/имени
"""

import asyncio
import sys
import json

from akubela_client import AkubelaClient


async def main():
    args = sys.argv[1:]
    search = None
    if "--search" in args:
        idx = args.index("--search")
        search = args[idx + 1].lower()
        args = args[:idx] + args[idx + 2:]
    domains_filter = set(args) if args else None

    ak = AkubelaClient()
    print("Подключаюсь к Akubela...")
    await ak.connect()

    states = await ak.get_states()
    print(f"Всего сущностей на панели: {len(states)}\n")

    by_domain = {}
    for s in states:
        domain = s["entity_id"].split(".", 1)[0]
        by_domain.setdefault(domain, []).append(s)

    print("Домены и количество сущностей:")
    for domain, items in sorted(by_domain.items(), key=lambda x: -len(x[1])):
        print(f"  {domain}: {len(items)}")
    print()

    for domain, items in sorted(by_domain.items()):
        if domains_filter and domain not in domains_filter:
            continue
        for s in items:
            haystack = (s["entity_id"] + " " + str(s.get("attributes", {}).get("friendly_name", ""))).lower()
            if search and search not in haystack:
                continue
            print("=" * 70)
            print(f"entity_id: {s['entity_id']}")
            print(f"state: {s.get('state')!r}")
            print("attributes:")
            print(json.dumps(s.get("attributes", {}), indent=2, ensure_ascii=False))
            print()

    await ak.close()


if __name__ == "__main__":
    asyncio.run(main())
