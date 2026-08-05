import asyncio
import logging

from config import PUSH_DOMAINS, EXCLUDE_ENTITIES, BRIDGE_PUBLIC_URL, RESYNC_INTERVAL_SEC
from device_registry import build_device_spec, akubela_domain_for, is_own_mqtt_bridge_entity, make_device_id_ext, StateStore

logger = logging.getLogger(__name__)


class SyncService:
    """
    Направление HA -> Akubela: держит виртуальные устройства панели в
    соответствии с реестром сущностей HA.

    Идемпотентность строится на нашем персистентном store
    (bridge_state.json), а не на самостоятельно выдуманном
    device_id_ext: для НОВЫХ устройств ID запрашивается у самой панели
    через ak_api/id_ext/next - это тот же формат, что ожидает веб-морда
    Akubela при ручном редактировании (раньше мы выдумывали свой формат
    "ha<хэш>", из-за чего веб-форма ругалась "Ошибка формата ID
    устройства").

    Уже созданные устройства не трогаются повторно - панель считается
    источником правды (см. полный сброс в force_resync.py, если нужно
    пересоздать конкретное устройство с текущими дефолтами кода).
    """

    def __init__(self, ha_client, ak_client, store: StateStore):
        self.ha = ha_client
        self.ak = ak_client
        self.store = store

    async def run_forever(self):
        while True:
            try:
                await self.full_sync()
            except Exception:
                logger.exception("full_sync failed")
            await asyncio.sleep(RESYNC_INTERVAL_SEC)

    async def full_sync(self):
        logger.info("=== full sync: HA -> Akubela ===")

        states = await self.ha.get_states()
        existing = await self.ak.device_get()
        existing_by_id = {d["device_id"]: d for d in existing if d.get("device_id")}
        # ВАЖНО: помимо поиска "уже есть?" по device_id из нашего локального
        # store (который может быть неполным/устаревшим - см. историю с
        # дублями), ДОПОЛНИТЕЛЬНО проверяем по device_id_ext - он
        # детерминированный (хэш от entity_id, см. make_device_id_ext) и
        # не зависит от того, что записано в bridge_state.json. Это
        # подстраховка: даже если store вообще пуст/рассинхронизирован
        # (например второй процесс бриджа где-то ещё работает и гоняется
        # за тем же файлом), мы всё равно не создадим дубль, если
        # устройство с таким ext уже реально существует на панели.
        existing_by_ext = {d["device_id_ext"]: d for d in existing if d.get("device_id_ext")}

        pushed = skipped = failed = 0

        for entity in states:
            entity_id = entity["entity_id"]
            domain = entity_id.split(".", 1)[0]

            if domain not in PUSH_DOMAINS or entity_id in EXCLUDE_ENTITIES:
                continue

            if is_own_mqtt_bridge_entity(entity_id):
                continue  # это сама Akubela пришла к нам через MQTT - не пушим обратно

            spec = build_device_spec(domain, entity)
            if spec is None:
                continue

            rec = self.store.get(entity_id)
            already = existing_by_id.get(rec.get("device_id")) if rec else None

            if not already:
                # Store не помог - проверяем по детерминированному ext:
                # либо тому, что уже записан в store (но device_id потерян),
                # либо вычисляем его заново, как если бы устройство
                # создавалось впервые (id_ext_next() на этой прошивке не
                # работает - используется хэш, см. ниже, поэтому ext всегда
                # предсказуем ДО создания).
                candidate_ext = (rec.get("device_id_ext") if rec else None) or make_device_id_ext(entity_id)
                already = existing_by_ext.get(candidate_ext)
                if already:
                    logger.info(
                        "%s: локальный store не знал про device_id, но устройство с "
                        "device_id_ext=%s уже есть на панели - подхватываю, не создаю дубль",
                        entity_id, candidate_ext,
                    )

            if already:
                # ВАЖНО: устройство уже существует на панели - НЕ трогаем его
                # device_type/feature/device_config, что бы ни говорил наш код.
                # Панель - источник правды для уже созданных устройств: это
                # позволяет вручную поправить виджет через веб-морду Akubela
                # (например если автовыбор feature не подошёл), и бридж это
                # не откатит на следующей синхронизации.
                self.store.set(
                    entity_id,
                    device_id_ext=already.get("device_id_ext"),
                    device_id=already.get("device_id"),
                    device_type=already.get("device_type"),
                    feature=already.get("feature"),
                    ak_domain=akubela_domain_for(already.get("device_type")),
                )
                skipped += 1
                continue

            # либо новая сущность, либо запись в store была, но устройство
            # с панели пропало (удалили вручную) - создаём заново
            try:
                device_id_ext = await self.ak.id_ext_next()
                if not device_id_ext:
                    # Подтверждено вживую (test_id_ext.py): ak_api/id_ext/next
                    # отвечает success:false ("Unknown error") на этой панели -
                    # метод либо не поддерживается прошивкой, либо ждёт другие
                    # параметры. Откатываемся на наш детерминированный хэш -
                    # работает через WS API, просто веб-форма редактирования
                    # может ругаться на формат ID именно у таких устройств -
                    # см. DEVICE_GUIDE.md.
                    device_id_ext = make_device_id_ext(entity_id)
                    logger.debug("%s: id_ext_next() недоступен, использую хэш %s", entity_id, device_id_ext)

                name = entity.get("attributes", {}).get("friendly_name", entity_id)
                # Akubela's веб-UI выдаёт "Имя слишком длинное" при редактировании
                # устройства, если имя превышает некоторый лимит (замечено на
                # склеенном HA-имени "Устройство (виртуальные) Конкретное имя").
                if len(name) > 32:
                    name = name[:32]

                await self.ak.device_add(
                    device_id_ext=device_id_ext,
                    name=name,
                    device_type=spec["device_type"],
                    feature=spec["feature"],
                    device_config=spec["device_config"],
                    url=BRIDGE_PUBLIC_URL,
                )
                self.store.set(
                    entity_id,
                    device_id_ext=device_id_ext,
                    device_type=spec["device_type"],
                    feature=spec["feature"],
                    ak_domain=akubela_domain_for(spec["device_type"]),
                )
                # ВАЖНО: сохраняем на диск СРАЗУ, не дожидаясь конца всего
                # цикла - иначе, если процесс упадёт/будет отменён
                # посередине full_sync (например asyncio.gather отменил
                # все задачи из-за падения другого, не связанного компонента -
                # см. main.py: _resilient), уже созданные на Akubela
                # устройства "забудутся" при следующем запуске и
                # бридж создаст их заново, плодя дубли.
                self.store.save()
                pushed += 1
                logger.info(
                    "pushed %s -> device_id_ext=%s (%s/%s)",
                    entity_id, device_id_ext, spec["device_type"], spec["feature"],
                )
            except Exception:
                failed += 1
                logger.exception("failed to push %s", entity_id)

        # добираем присвоенные панелью device_id для всех наших новых записей.
        # ВАЖНО: панель не всегда успевает проиндексировать только что
        # созданные device/add к моменту немедленного device_get() -
        # похоже на задержку согласованности на стороне прошивки (та же
        # "задумчивость", что видели при массовом device/del). Если сразу
        # не нашли device_id - не сдаёмся сразу, пробуем ещё несколько раз
        # с паузой. Без этого device_id оставался бы пустым, сохранялся
        # так в bridge_state.json, и на следующем full_sync сущность
        # считалась бы "новой" и пушилась заново - плодя дубли.
        pending = {
            entity_id for entity_id, rec in self.store.entities.items()
            if not rec.get("device_id") and rec.get("device_id_ext")
        }
        existing_after = []
        for attempt in range(5):
            existing_after = await self.ak.device_get()
            existing_after_by_ext = {
                d.get("device_id_ext"): d for d in existing_after if d.get("device_id_ext")
            }
            still_pending = set()
            for entity_id in pending:
                rec = self.store.entities.get(entity_id)
                if not rec:
                    continue
                dev = existing_after_by_ext.get(rec.get("device_id_ext"))
                if dev:
                    rec["device_id"] = dev.get("device_id")
                else:
                    still_pending.add(entity_id)
            pending = still_pending
            if not pending:
                break
            await asyncio.sleep(1.5)
        if pending:
            logger.warning(
                "не удалось получить device_id для %s устройств даже после нескольких попыток "
                "(%s) - панель могла не успеть их проиндексировать, проверьте на следующем full_sync",
                len(pending), sorted(pending),
            )
        self.store.save()

        await self._push_initial_states(states)
        await self._prune_out_of_scope()

        logger.info(
            "sync done: pushed=%s skipped=%s failed=%s total_akubela_devices=%s",
            pushed, skipped, failed, len(existing_after),
        )

    async def _prune_out_of_scope(self):
        """
        Удаляет из Akubela устройства, которые мы сами когда-то создали,
        но сущность больше не в PUSH_DOMAINS или попала в EXCLUDE_ENTITIES
        (например была исключена задним числом, чтобы не дублировать
        данные, которые и так приходят из Akubela по другому каналу - см.
        EXCLUDE_ENTITIES в .env).
        """
        for entity_id in list(self.store.entities.keys()):
            domain = entity_id.split(".", 1)[0]
            out_of_scope = (
                domain not in PUSH_DOMAINS
                or entity_id in EXCLUDE_ENTITIES
                or is_own_mqtt_bridge_entity(entity_id)
            )
            if not out_of_scope:
                continue

            rec = self.store.entities[entity_id]
            device_id = rec.get("device_id")
            if device_id:
                try:
                    await self.ak.device_del([device_id])
                    logger.info("удалил устройство вне области видимости: %s (%s)", entity_id, device_id)
                except Exception:
                    logger.exception("не смог удалить %s (%s)", entity_id, device_id)
            self.store.remove(entity_id)
        self.store.save()

    async def _push_initial_states(self, states):
        """
        Сразу после регистрации/ресинка отправляем текущее HA-состояние
        light/switch в Akubela через call_service (WS), чтобы не ждать
        следующего state_changed в HA - см. state_forwarder.py для live-
        обновлений между полными синками.
        """
        by_entity = {s["entity_id"]: s for s in states}

        for entity_id, rec in self.store.entities.items():
            ak_domain = rec.get("ak_domain")
            if ak_domain not in ("light", "switch", "climate"):
                continue
            device_id = rec.get("device_id")
            entity = by_entity.get(entity_id)
            if not device_id or not entity:
                continue

            state = entity.get("state")
            akubela_entity_id = f"{ak_domain}.{device_id}"

            try:
                if ak_domain == "climate":
                    await self.ak.call_service(ak_domain, "set_hvac_mode", akubela_entity_id, hvac_mode=state)
                elif state == "on":
                    await self.ak.call_service(ak_domain, "turn_on", akubela_entity_id)
                elif state == "off":
                    await self.ak.call_service(ak_domain, "turn_off", akubela_entity_id)
            except Exception:
                logger.exception("failed to push initial state for %s", entity_id)
