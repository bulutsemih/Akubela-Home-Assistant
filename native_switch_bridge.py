"""
Родные переключатели/реле панели (не наши виртуальные устройства) -> HA
через MQTT Discovery, С ОБРАТНЫМ УПРАВЛЕНИЕМ - например реле, добавленное
через Modbus RTU прямо в Akubela, минуя нас.

В отличие от домофона (native_lock_bridge.py, где команда открытия не
подтверждена документом), здесь всё ПОДТВЕРЖДЕНО: switch.turn_on/
turn_off - тот же самый паттерн call_service, который мы уже много раз
проверили на собственных виртуальных Switch-устройствах (Klapan,
Akubela Test и т.д.) - разница только в том, что устройство родное для
панели, а не создано нами через device/add.

Синхронизация - двухуровневая, как и у остальных частей бриджа:
  1. Live: подписка на state_changed у Akubela - изменения долетают
     мгновенно, как и у наших собственных устройств.
  2. Периодическая: раз в RESYNC_INTERVAL_SEC заново проходим
     get_states() и объявляем через MQTT Discovery устройства, которых
     ещё не видели - чтобы реле/переключатель, добавленный в Akubela
     ПОСЛЕ старта бриджа, появился в HA без перезапуска процесса.
"""

import asyncio
import json
import logging

import aiomqtt

from config import (
    MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
    MQTT_DISCOVERY_PREFIX, RESYNC_INTERVAL_SEC,
)
from device_registry import StateStore

logger = logging.getLogger(__name__)

NATIVE_SWITCH_DOMAINS = {"switch"}


def _our_device_ids(store: StateStore):
    return {rec["device_id"] for rec in store.entities.values() if rec.get("device_id")}


def _safe_id(entity_id: str) -> str:
    return entity_id.replace(".", "_")


class NativeSwitchBridge:

    def __init__(self, ak_client, store: StateStore):
        self.ak = ak_client
        self.store = store
        self._mqtt = None
        self._announced = set()
        self._unpublished_ghosts = set()
        self._last_published = {}  # entity_id -> последнее опубликованное в HA состояние
        self._command_topics = {}  # topic -> (entity_id, domain)

    async def run_forever(self):
        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("[MQTT] NativeSwitchBridge упал, перезапуск через 5с")
                await asyncio.sleep(5)

    async def _run_once(self):
        async with aiomqtt.Client(
            hostname=MQTT_HOST, port=MQTT_PORT,
            username=MQTT_USERNAME or None, password=MQTT_PASSWORD or None,
        ) as mqtt:
            self._mqtt = mqtt
            logger.info("[MQTT] NativeSwitchBridge подключен к %s:%s", MQTT_HOST, MQTT_PORT)

            await self._sync_once()

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
                await self._sync_once()
            except Exception:
                logger.exception("[MQTT] периодическая ресинхронизация упала")

    async def _sync_once(self):
        states = await self.ak.get_states()
        for state in states:
            domain = state["entity_id"].split(".", 1)[0]
            if domain not in NATIVE_SWITCH_DOMAINS:
                continue
            try:
                await self._handle_state(state)
            except Exception:
                logger.exception("[MQTT] не смог опубликовать %s", state.get("entity_id"))

    async def _handle_state(self, state: dict):
        entity_id = state["entity_id"]
        domain, device_id = entity_id.split(".", 1)

        if domain not in NATIVE_SWITCH_DOMAINS:
            return
        if device_id in _our_device_ids(self.store):
            return  # наше собственное виртуальное устройство - пропускаем

        # Некоторые каналы (незадействованные/неоконфигурированные слоты
        # того же модуля реле) не имеют реального состояния и своего
        # имени - панель отдаёт для них state=None/"unknown" и имя по
        # умолчанию, совпадающее с именем самой панели ("Akubela HyPanel
        # (родные устройства)"). Публиковать такие в HA бессмысленно -
        # получаются "сущности-призраки" без функции. Пропускаем.
        raw_state = state.get("state")
        if raw_state in (None, "", "unknown", "unavailable"):
            if entity_id not in self._unpublished_ghosts:
                self._unpublished_ghosts.add(entity_id)
                object_id = f"akubela_{_safe_id(entity_id)}"
                config_topic = f"{MQTT_DISCOVERY_PREFIX}/switch/{object_id}/config"
                # Пустой payload на топик конфига - официальный способ MQTT
                # Discovery сказать HA "удали эту сущность", если она была
                # создана раньше (до этого фильтра).
                await self._mqtt.publish(config_topic, payload="", retain=True)
                logger.info("%s: нет реального состояния (%r) - убрал из HA (если была создана раньше)",
                            entity_id, raw_state)
            return

        if entity_id not in self._announced:
            await self._announce(entity_id, state, domain)
            self._announced.add(entity_id)

        object_id = f"akubela_{_safe_id(entity_id)}"
        state_topic = f"{MQTT_DISCOVERY_PREFIX}/switch/{object_id}/state"
        ha_state = "ON" if raw_state == "on" else "OFF"
        if self._last_published.get(entity_id) != ha_state:
            self._last_published[entity_id] = ha_state
            await self._mqtt.publish(state_topic, payload=ha_state)
            logger.info("Akubela -> HA: %s %s", entity_id, ha_state)

    async def _announce(self, entity_id, state, domain):
        attrs = state.get("attributes", {}) or {}
        object_id = f"akubela_{_safe_id(entity_id)}"
        base = f"{MQTT_DISCOVERY_PREFIX}/switch/{object_id}"

        config = {
            "name": attrs.get("friendly_name", entity_id),
            "unique_id": object_id,
            "state_topic": f"{base}/state",
            "command_topic": f"{base}/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": {
                "identifiers": ["akubela_panel_native"],
                "name": "Akubela HyPanel (родные устройства)",
                "manufacturer": "Akubela",
            },
        }
        await self._mqtt.publish(f"{base}/config", json.dumps(config), retain=True)
        await self._mqtt.subscribe(f"{base}/set")
        self._command_topics[f"{base}/set"] = (entity_id, domain)
        logger.info("[MQTT] switch discovery опубликован: %s", entity_id)

    async def _listen_commands(self):
        async for message in self._mqtt.messages:
            topic = str(message.topic)
            info = self._command_topics.get(topic)
            if not info:
                continue
            entity_id, domain = info
            payload = message.payload.decode()

            try:
                service = "turn_on" if payload == "ON" else "turn_off"
                await self.ak.call_service(domain, service, entity_id)
                logger.info("HA -> Akubela: %s %s", entity_id, payload)
            except Exception:
                logger.exception("не удалось выполнить команду для %s", entity_id)
