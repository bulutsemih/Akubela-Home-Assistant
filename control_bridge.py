"""
Направление Akubela -> HA, реализовано через подтверждённый механизм:
панель сама транслирует state_changed по WS (та же служебная логика,
что использует github.com/jaaneo/akubela-homeassistant и что видно в
вашем test_events.py: виртуальное устройство "light.<device_id>"
меняло state on/off при управлении с панели).

Раньше эта логика планировалась через HTTP callback (control_server.py) -
формат которого не был подтверждён. Теперь это основной канал, а
control_server.py остаётся резервным / для отладки.
"""

import logging

from device_registry import StateStore, resolve_state_service
from config import FORWARD_LIGHT_COLOR_TEMP, LIGHT_FORWARD_MIN_INTERVAL_SEC
from loop_guard import LoopGuard
from rate_limited_sender import RateLimitedSender

logger = logging.getLogger(__name__)


class ControlBridge:

    def __init__(self, ha_client, ak_client, store: StateStore):
        self.ha = ha_client
        self.ak = ak_client
        self.store = store
        self._last_seen_mode = {}   # entity_id -> последний отправленный в HA hvac_mode/on-off
        self._last_seen_current_temp = {}  # entity_id -> последняя отправленная в HA current_temperature (climate)
        self._loop_guard = LoopGuard(name="control_bridge (Akubela->HA)")
        # НЕ роняют финальное значение при попадании в окно rate-limit -
        # см. rate_limited_sender.py.
        self._temp_sender = RateLimitedSender(LIGHT_FORWARD_MIN_INTERVAL_SEC, name="control_bridge temp")
        self._position_sender = RateLimitedSender(LIGHT_FORWARD_MIN_INTERVAL_SEC, name="control_bridge position")
        self._light_sender = RateLimitedSender(LIGHT_FORWARD_MIN_INTERVAL_SEC, name="control_bridge light")

    async def run_forever(self):
        async for event in self.ak.events():
            if event.get("event_type") != "state_changed":
                continue

            data = event.get("data", {})
            new_state = data.get("new_state")
            old_state = data.get("old_state") or {}
            if not new_state:
                continue

            akubela_entity_id = new_state["entity_id"]  # "light.<device_id>" и т.п.
            _, device_id = akubela_entity_id.split(".", 1)

            entity_id = self._entity_for_device_id(device_id)
            if not entity_id:
                continue  # не наше устройство (физическое/чужое) - игнор

            # ВАЖНО: домен для call_service в HA берём из настоящего HA
            # entity_id (например "input_boolean"), а не из entity_id
            # Akubela ("switch") - они могут не совпадать.
            domain = entity_id.split(".", 1)[0]
            state = new_state.get("state")
            new_attrs = new_state.get("attributes", {}) or {}
            old_attrs = old_state.get("attributes", {}) or {}

            # --- режим/состояние (hvac_mode / on-off / open-closed / locked-unlocked) ---
            state_changed = not (old_state and old_state.get("state") == state)
            if state_changed and self._last_seen_mode.get(entity_id) != state:
                self._last_seen_mode[entity_id] = state
                if not self._loop_guard.allowed(entity_id):
                    continue
                resolved = resolve_state_service(domain, state)
                if resolved is None:
                    logger.debug("не обрабатываю state=%r для %s (%s)", state, entity_id, domain)
                else:
                    service, data = resolved
                    logger.info("Akubela -> HA: %s (%s) режим -> %s", entity_id, akubela_entity_id, state)
                    try:
                        await self.ha.call_service(domain, service, entity_id, **data)
                    except Exception:
                        logger.exception("не удалось применить режим %s в HA", entity_id)

            # --- целевая температура (climate.set_temperature) ---
            if domain == "climate":
                new_temp = new_attrs.get("temperature")
                old_temp = old_attrs.get("temperature")
                if new_temp is not None and new_temp != old_temp:
                    async def _send_temp(value, entity_id=entity_id, akubela_entity_id=akubela_entity_id):
                        await self.ha.call_service(domain, "set_temperature", entity_id, temperature=value)
                        logger.info("Akubela -> HA: %s (%s) target_temp -> %s", entity_id, akubela_entity_id, value)
                    await self._temp_sender.submit(entity_id, new_temp, _send_temp)

                # ПРИМЕЧАНИЕ: current_temperature сюда намеренно НЕ прокидывается -
                # у HA climate.set_temperature нет такого параметра, это read-only
                # атрибут, который для реального устройства пишется отдельным
                # механизмом (например MQTT climate current_temperature_topic), а
                # не общим сервисом. Если понадобится - нужен отдельный маппинг под
                # конкретную интеграцию климата в HA, а не общий call_service.

            # --- позиция шторы/жалюзи (cover.set_cover_position) ---
            # Как и в state_forwarder.py: 0/100 не шлём отдельно (уже покрыты
            # open_cover/close_cover выше).
            if domain == "cover":
                new_pos = new_attrs.get("current_position")
                old_pos = old_attrs.get("current_position")
                if new_pos is not None and 0 < new_pos < 100 and new_pos != old_pos:
                    async def _send_position(value, entity_id=entity_id, akubela_entity_id=akubela_entity_id):
                        await self.ha.call_service(domain, "set_cover_position", entity_id, position=value)
                        logger.info("Akubela -> HA: %s (%s) позиция -> %s", entity_id, akubela_entity_id, value)
                    await self._position_sender.submit(entity_id, new_pos, _send_position)

            # --- яркость / цветовая температура света ---
            # ВАЖНО: предполагаем, что Akubela сообщает color_temp так же, как
            # принимает - в mired (см. пометку в state_forwarder.py) - конвертируем
            # обратно в kelvin, т.к. наш тестовый light в HA настроен на kelvin.
            # Это НЕ подтверждено реальным пакетом - если не сработает, пришлите
            # JSON события при изменении цвета/яркости на панели.
            if domain == "light" and state == "on":
                brightness = new_attrs.get("brightness")
                color_temp_kelvin = None

                if FORWARD_LIGHT_COLOR_TEMP:
                    color_temp_mired = new_attrs.get("color_temp")
                    color_temp_kelvin = round(1_000_000 / color_temp_mired) if color_temp_mired else None

                if brightness is not None or color_temp_kelvin is not None:
                    key = (brightness, color_temp_kelvin)

                    async def _send_light(value, entity_id=entity_id, akubela_entity_id=akubela_entity_id):
                        b, ct_k = value
                        kwargs = {}
                        if b is not None:
                            kwargs["brightness"] = b
                        if ct_k is not None:
                            kwargs["color_temp_kelvin"] = ct_k
                        logger.info("Akubela -> HA: %s (%s) яркость/цвет -> %s", entity_id, akubela_entity_id, kwargs)
                        await self.ha.call_service(domain, "turn_on", entity_id, **kwargs)

                    await self._light_sender.submit(entity_id, key, _send_light)

    def _entity_for_device_id(self, device_id):
        for entity_id, rec in self.store.entities.items():
            if rec.get("device_id") == device_id:
                return entity_id
        return None
