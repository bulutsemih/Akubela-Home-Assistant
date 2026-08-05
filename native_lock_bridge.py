"""
Родные замки/домофоны панели (не наши виртуальные устройства) -> HA
через MQTT Discovery, С ОБРАТНЫМ УПРАВЛЕНИЕМ - в отличие от
native_sensor_bridge.py, который только читает, тут нужно уметь послать
команду "открыть" обратно в Akubela.

ЧЕСТНО, что подтверждено, а что нет:
- Официальный OpenAPI-документ Akubela описывает ability "doorphone":
  НЕТ обычного состояния (state: "-", "No state"), только действие
  action="unlock" с attribute {"lock": [0]} (индексы конкретных замков
  из attribute.lock - список строк вида ["lock1","lock2"]). Это описание
  REST-эндпоинта (Control Device), а не локального WS ak_api/call_service,
  которым мы пользуемся везде остальном.
- Мы ПРЕДПОЛАГАЕМ, что WS call_service работает аналогично (domain из
  entity_id, service="unlock", service_data={"entity_id":..., "lock":[0]})
  - это неподтверждено. Используйте test_doorphone_unlock.py, чтобы
  проверить на реальном устройстве и увидеть сырой ответ.
- Обычный "lock" (не domofon, настоящий дверной замок со состоянием
  locked/unlocked) должен работать НАДЁЖНЕЕ - для него мы используем
  подтверждённый паттерн call_service(domain, "lock"/"unlock", entity_id),
  тот же, что и для наших собственных устройств (resolve_state_service).

Домофон без состояния публикуется в HA как MQTT button ("нажать, чтобы
открыть"), а не как lock (потому что state у него нет вообще - see доку
выше). Обычный lock с состоянием публикуется как MQTT lock.
"""

import asyncio
import json
import logging

import aiomqtt

from config import MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, MQTT_DISCOVERY_PREFIX, RESYNC_INTERVAL_SEC
from device_registry import StateStore

logger = logging.getLogger(__name__)


def _our_device_ids(store: StateStore):
    return {rec["device_id"] for rec in store.entities.values() if rec.get("device_id")}


def _safe_id(entity_id: str) -> str:
    return entity_id.replace(".", "_")


class NativeLockBridge:

    def __init__(self, ak_client, store: StateStore):
        self.ak = ak_client
        self.store = store
        self._mqtt = None
        self._announced = set()
        self._command_topics = {}  # topic -> (entity_id, akubela_entity_id, domain, kind)

    async def run_forever(self):
        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("[MQTT] NativeLockBridge упал, перезапуск через 5с")
                await asyncio.sleep(5)

    async def _run_once(self):
        async with aiomqtt.Client(
            hostname=MQTT_HOST, port=MQTT_PORT,
            username=MQTT_USERNAME or None, password=MQTT_PASSWORD or None,
        ) as mqtt:
            self._mqtt = mqtt
            logger.info("[MQTT] NativeLockBridge подключен к %s:%s", MQTT_HOST, MQTT_PORT)

            await self._publish_initial()

            asyncio.create_task(self._periodic_resync())
            asyncio.create_task(self._listen_commands())

            async for event in self.ak.events():
                if event.get("event_type") != "state_changed":
                    continue
                new_state = event.get("data", {}).get("new_state")
                if not new_state:
                    continue
                await self._handle_state(new_state)

    async def _periodic_resync(self):
        while True:
            await asyncio.sleep(RESYNC_INTERVAL_SEC)
            try:
                await self._publish_initial()
            except Exception:
                logger.exception("[MQTT] периодическая ресинхронизация упала")

    async def _publish_initial(self):
        states = await self.ak.get_states()
        for state in states:
            domain = state["entity_id"].split(".", 1)[0]
            if domain in ("lock", "doorphone", "domofon"):
                try:
                    await self._handle_state(state)
                except Exception:
                    logger.exception("[MQTT] не смог опубликовать %s", state.get("entity_id"))

    async def _handle_state(self, state: dict):
        entity_id = state["entity_id"]
        domain, device_id = entity_id.split(".", 1)

        if domain not in ("lock", "doorphone", "domofon"):
            return
        if device_id in _our_device_ids(self.store):
            return  # наше собственное виртуальное устройство - пропускаем

        has_state = state.get("state") not in (None, "-", "")

        if entity_id not in self._announced:
            if has_state:
                await self._announce_lock(entity_id, state, domain)
            else:
                await self._announce_button(entity_id, state, domain)
            self._announced.add(entity_id)

        if has_state:
            object_id = f"akubela_{_safe_id(entity_id)}"
            state_topic = f"{MQTT_DISCOVERY_PREFIX}/lock/{object_id}/state"
            ha_state = "LOCKED" if state.get("state") == "locked" else "UNLOCKED"
            await self._mqtt.publish(state_topic, payload=ha_state)

    async def _announce_lock(self, entity_id, state, domain):
        attrs = state.get("attributes", {}) or {}
        object_id = f"akubela_{_safe_id(entity_id)}"
        base = f"{MQTT_DISCOVERY_PREFIX}/lock/{object_id}"

        config = {
            "name": attrs.get("friendly_name", entity_id),
            "unique_id": object_id,
            "state_topic": f"{base}/state",
            "command_topic": f"{base}/set",
            "payload_lock": "LOCK",
            "payload_unlock": "UNLOCK",
            "state_locked": "LOCKED",
            "state_unlocked": "UNLOCKED",
            "device": {
                "identifiers": ["akubela_panel_native"],
                "name": "Akubela HyPanel (родные устройства)",
                "manufacturer": "Akubela",
            },
        }
        await self._mqtt.publish(f"{base}/config", json.dumps(config), retain=True)
        await self._mqtt.subscribe(f"{base}/set")
        self._command_topics[f"{base}/set"] = (entity_id, f"{domain}.{entity_id.split('.', 1)[1]}", domain, "lock")
        logger.info("[MQTT] lock discovery опубликован: %s", entity_id)

    async def _announce_button(self, entity_id, state, domain):
        # Домофон без состояния (см. доку "doorphone": state="-") - публикуем
        # как кнопку "открыть дверь", а не как lock, поскольку lock в HA
        # требует персистентного состояния locked/unlocked, которого тут нет.
        attrs = state.get("attributes", {}) or {}
        object_id = f"akubela_{_safe_id(entity_id)}"
        base = f"{MQTT_DISCOVERY_PREFIX}/button/{object_id}"

        config = {
            "name": f"{attrs.get('friendly_name', entity_id)} - открыть дверь",
            "unique_id": object_id,
            "command_topic": f"{base}/set",
            "payload_press": "PRESS",
            "device": {
                "identifiers": ["akubela_panel_native"],
                "name": "Akubela HyPanel (родные устройства)",
                "manufacturer": "Akubela",
            },
        }
        await self._mqtt.publish(f"{base}/config", json.dumps(config), retain=True)
        await self._mqtt.subscribe(f"{base}/set")
        akubela_entity_id = entity_id  # домофон-сущности используем как есть
        self._command_topics[f"{base}/set"] = (entity_id, akubela_entity_id, domain, "button")
        logger.info("[MQTT] button (домофон) discovery опубликован: %s "
                    "- ЭКСПЕРИМЕНТАЛЬНО, см. test_doorphone_unlock.py для калибровки", entity_id)

    async def _listen_commands(self):
        async for message in self._mqtt.messages:
            topic = str(message.topic)
            info = self._command_topics.get(topic)
            if not info:
                continue
            entity_id, akubela_entity_id, domain, kind = info
            payload = message.payload.decode()

            try:
                if kind == "lock":
                    if payload == "LOCK":
                        await self.ak.call_service(domain, "lock", akubela_entity_id)
                    elif payload == "UNLOCK":
                        await self.ak.call_service(domain, "unlock", akubela_entity_id)
                    logger.info("HA -> Akubela: %s %s", entity_id, payload)
                elif kind == "button":
                    # НЕ ПОДТВЕРЖДЕНО - см. предупреждение в шапке файла.
                    resp = await self.ak.call_service(domain, "unlock", akubela_entity_id, lock=[0])
                    logger.info("HA -> Akubela: %s открыть дверь -> ответ %s", entity_id, resp)
            except Exception:
                logger.exception("не удалось выполнить команду для %s", entity_id)
