"""
Публикует "родные" сущности Akubela (датчики, подключённые прямо к
панели, например через её собственную MQTT-интеграцию) в HA через
MQTT Discovery - HA сам создаст сущности по discovery-топикам, без
правки configuration.yaml.

Работает поверх того же WS state_changed, что и остальной бридж - панель
транслирует HA-style state_changed для ВСЕХ своих сущностей, включая
нативные (это видно в вашем test_events.py: там были не только наши
виртуальные "light.xxx", но и родная "camera.0c11051f75d9").

Отличает "родное" от "нашего" по device_id: если device_id есть в
bridge_state.json (мы сами его туда положили через device/add) - это
наше виртуальное устройство, пропускаем, чтобы не дублировать.
"""

import asyncio
import json
import logging

import aiomqtt

from config import (
    MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
    MQTT_DISCOVERY_PREFIX, NATIVE_SENSOR_DOMAINS, RESYNC_INTERVAL_SEC,
)
from device_registry import StateStore

logger = logging.getLogger(__name__)


def _our_device_ids(store: StateStore):
    return {rec["device_id"] for rec in store.entities.values() if rec.get("device_id")}


def _safe_id(entity_id: str) -> str:
    return entity_id.replace(".", "_")


class NativeSensorBridge:
    """Akubela (родные сущности) -> HA, через MQTT Discovery."""

    def __init__(self, ak_client, store: StateStore):
        self.ak = ak_client
        self.store = store
        self._mqtt = None
        self._announced = set()

    async def run_forever(self):
        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("[MQTT] NativeSensorBridge упал, перезапуск через 5с")
                await asyncio.sleep(5)

    async def _run_once(self):
        async with aiomqtt.Client(
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            username=MQTT_USERNAME or None,
            password=MQTT_PASSWORD or None,
        ) as mqtt:
            self._mqtt = mqtt
            logger.info("[MQTT] подключен к %s:%s", MQTT_HOST, MQTT_PORT)

            await self._publish_initial_states()

            asyncio.create_task(self._periodic_resync())

            async for event in self.ak.events():
                if event.get("event_type") != "state_changed":
                    continue
                new_state = event.get("data", {}).get("new_state")
                if not new_state:
                    continue
                await self._handle_state(new_state)

    async def _periodic_resync(self):
        # Ловит устройства, добавленные в Akubela ПОСЛЕ старта бриджа
        # (например новый датчик), без необходимости перезапускать процесс.
        while True:
            await asyncio.sleep(RESYNC_INTERVAL_SEC)
            try:
                await self._publish_initial_states()
            except Exception:
                logger.exception("[MQTT] периодическая ресинхронизация упала")

    async def _publish_initial_states(self):
        states = await self.ak.get_states()
        logger.info("[MQTT] нативных состояний на панели: %s", len(states))
        for state in states:
            try:
                await self._handle_state(state)
            except Exception:
                logger.exception("[MQTT] не смог опубликовать %s", state.get("entity_id"))

    async def _handle_state(self, state: dict):
        entity_id = state["entity_id"]
        domain, device_id = entity_id.split(".", 1)

        if domain not in NATIVE_SENSOR_DOMAINS:
            return
        if device_id in _our_device_ids(self.store):
            return  # наше собственное виртуальное устройство, не нативное - пропускаем

        # HA-платформа для MQTT discovery: "sensor" для числовых величин,
        # "binary_sensor" для on/off (например датчик приближения) - у
        # Akubela это тоже разные domain на стороне её WS API.
        platform = "binary_sensor" if domain == "binary_sensor" else "sensor"

        if entity_id not in self._announced:
            await self._announce(entity_id, state, platform)
            self._announced.add(entity_id)

        object_id = f"akubela_{_safe_id(entity_id)}"
        state_topic = f"{MQTT_DISCOVERY_PREFIX}/{platform}/{object_id}/state"
        await self._mqtt.publish(state_topic, payload=str(state.get("state", "")))

    async def _announce(self, entity_id, state, platform):
        attrs = state.get("attributes", {})
        object_id = f"akubela_{_safe_id(entity_id)}"

        config = {
            "name": attrs.get("friendly_name", entity_id),
            "unique_id": object_id,
            "state_topic": f"{MQTT_DISCOVERY_PREFIX}/{platform}/{object_id}/state",
            "device": {
                "identifiers": ["akubela_panel_native"],
                "name": "Akubela HyPanel (родные устройства)",
                "manufacturer": "Akubela",
            },
        }

        if platform == "binary_sensor":
            config["payload_on"] = "on"
            config["payload_off"] = "off"
        else:
            if attrs.get("unit_of_measurement"):
                config["unit_of_measurement"] = attrs["unit_of_measurement"]
            config["state_class"] = "measurement"

        if attrs.get("device_class"):
            config["device_class"] = attrs["device_class"]

        config_topic = f"{MQTT_DISCOVERY_PREFIX}/{platform}/{object_id}/config"
        await self._mqtt.publish(config_topic, payload=json.dumps(config), retain=True)
        logger.info("[MQTT] discovery опубликован: %s -> %s (%s)", entity_id, config_topic, platform)
