"""
HA -> Akubela (живьём): пробрасывает изменения state light/switch сразу,
не дожидаясь следующего full_sync (который бежит раз в RESYNC_INTERVAL_SEC
и создаёт/чинит устройства, но не занимается их текущим состоянием).

Управление уже зарегистрированным виртуальным устройством делается тем же
call_service на WS-канале Akubela - см. control_bridge.py и
akubela_client.py:call_service().
"""

import logging

from device_registry import StateStore, resolve_state_service
from config import FORWARD_LIGHT_COLOR_TEMP, LIGHT_FORWARD_MIN_INTERVAL_SEC
from loop_guard import LoopGuard
from rate_limited_sender import RateLimitedSender

logger = logging.getLogger(__name__)


class StateForwarder:

    def __init__(self, ha_client, ak_client, store: StateStore):
        self.ha = ha_client
        self.ak = ak_client
        self.store = store
        self._last_sent_mode = {}   # entity_id -> последний отправленный в Akubela режим/on-off
        self._last_sent_current_temp = {}  # entity_id -> последняя отправленная current_temperature (climate)
        self._loop_guard = LoopGuard(name="state_forwarder (HA->Akubela)")
        # НЕ роняют финальное значение при попадании в окно rate-limit -
        # откладывают и досылают актуальное значение, как только окно
        # истечёт (см. rate_limited_sender.py - раньше значения, попавшие
        # в окно, терялись насовсем, и приходилось вводить их повторно).
        self._temp_sender = RateLimitedSender(LIGHT_FORWARD_MIN_INTERVAL_SEC, name="state_forwarder temp")
        self._position_sender = RateLimitedSender(LIGHT_FORWARD_MIN_INTERVAL_SEC, name="state_forwarder position")
        self._light_sender = RateLimitedSender(LIGHT_FORWARD_MIN_INTERVAL_SEC, name="state_forwarder light")

    async def run_forever(self):
        async for event in self.ha.events():
            if event.get("event_type") != "state_changed":
                continue

            entity_id = event["data"]["entity_id"]
            new_state = event["data"].get("new_state")
            if not new_state:
                continue

            rec = self.store.get(entity_id)
            if not rec or not rec.get("device_id"):
                continue  # не наше устройство или ещё не зарегистрировано

            ak_domain = rec.get("ak_domain")
            if ak_domain not in ("light", "switch", "climate", "cover", "lock"):
                continue  # sensor пока не мониторим live, только на full_sync

            # ВАЖНО: домен на стороне Akubela может отличаться от HA-домена
            # источника (например input_boolean.xxx в HA -> switch.<id> в Akubela) -
            # поэтому используем ak_domain, а не entity_id.split(".")[0].
            akubela_entity_id = f"{ak_domain}.{rec['device_id']}"
            state = new_state.get("state")

            # --- режим/состояние (hvac_mode / on-off / open-closed / locked-unlocked) ---
            if self._last_sent_mode.get(entity_id) != state:
                self._last_sent_mode[entity_id] = state
                if not self._loop_guard.allowed(entity_id):
                    continue
                resolved = resolve_state_service(ak_domain, state)
                if resolved is None:
                    logger.info("%s: нет сервиса для state=%r (%s), не пушу", entity_id, state, ak_domain)
                else:
                    service, data = resolved
                    try:
                        await self.ak.call_service(ak_domain, service, akubela_entity_id, **data)
                        logger.info("HA -> Akubela: %s режим -> %s (%s)", entity_id, state, service)

                        # У виртуальной шторы нет мотора/концевика, который
                        # сигнализирует "открытие завершено" - open_cover сам
                        # по себе может не довести панель до финального 'open'
                        # (в тестах 'closed' достигался стабильно, 'open' - нет).
                        # Досылаем позицию как явное подтверждение конечной
                        # точки, сразу вслед за командой режима.
                        if ak_domain == "cover" and state in ("open", "closed"):
                            target_position = 100 if state == "open" else 0
                            await self.ak.call_service(
                                ak_domain, "set_cover_position", akubela_entity_id, position=target_position
                            )
                            logger.info("HA -> Akubela: %s позиция (подтверждение) -> %s", entity_id, target_position)
                    except Exception:
                        logger.exception("не удалось запушить режим %s в Akubela", entity_id)

            # --- целевая температура и текущая (показание датчика) ---
            if ak_domain == "climate":
                attrs = new_state.get("attributes") or {}
                temp = attrs.get("temperature")
                if temp is not None:
                    async def _send_temp(value, akubela_entity_id=akubela_entity_id, entity_id=entity_id):
                        await self.ak.call_service(
                            ak_domain, "set_temperature", akubela_entity_id, temperature=value
                        )
                        logger.info("HA -> Akubela: %s target_temp -> %s", entity_id, value)
                    await self._temp_sender.submit(entity_id, temp, _send_temp)

                current_temp = attrs.get("current_temperature")
                if current_temp is not None and self._last_sent_current_temp.get(entity_id) != current_temp:
                    self._last_sent_current_temp[entity_id] = current_temp
                    try:
                        await self.ak.call_service(
                            ak_domain, "set_temperature", akubela_entity_id, current_temperature=current_temp
                        )
                    except Exception:
                        logger.exception("не удалось запушить current_temperature %s в Akubela", entity_id)

            # --- позиция шторы/жалюзи (cover.set_cover_position) ---
            # ВАЖНО: 0 и 100 НЕ шлём сюда отдельно - их уже покрывают
            # open_cover/close_cover из блока режима выше (панель получает
            # два противоречащих приказа подряд иначе и путается - было
            # хаотичное opening/closing без результата). Позицию шлём
            # только для промежуточных значений (реально двигали ползунком).
            if ak_domain == "cover":
                position = (new_state.get("attributes") or {}).get("current_position")
                if position is not None and 0 < position < 100:
                    async def _send_position(value, akubela_entity_id=akubela_entity_id, entity_id=entity_id):
                        await self.ak.call_service(
                            ak_domain, "set_cover_position", akubela_entity_id, position=value
                        )
                        logger.info("HA -> Akubela: %s позиция -> %s", entity_id, value)
                    await self._position_sender.submit(entity_id, position, _send_position)

            # --- яркость / цветовая температура света ---
            # color_temp - в mired, подтверждено кодом jaaneo (light.py).
            # brightness - ПОДТВЕРЖДЕНО ЖИВЫМ ТЕСТОМ (test_brightness_debug.py):
            # панель для этого виджета (feature=4) реально применяет только
            # brightness_pct (0-100, как в официальном REST-примере Akubela),
            # а не brightness (0-255, стиль HA/jaaneo) - тот вызов возвращал
            # success:true, но яркость не менялась. Обратно панель всегда
            # репортит "brightness" 0-255, поэтому control_bridge.py менять
            # не пришлось - только эту, исходящую сторону.
            if ak_domain == "light" and state == "on":
                attrs = new_state.get("attributes") or {}
                brightness = attrs.get("brightness")
                brightness_pct = round(brightness * 100 / 255) if brightness is not None else None
                color_temp_mired = None

                if FORWARD_LIGHT_COLOR_TEMP:
                    color_temp_kelvin = attrs.get("color_temp_kelvin")
                    color_temp_mired = attrs.get("color_temp")  # уже в mired, если задан напрямую
                    if color_temp_mired is None and color_temp_kelvin:
                        color_temp_mired = round(1_000_000 / color_temp_kelvin)

                if brightness_pct is not None or color_temp_mired is not None:
                    key = (brightness_pct, color_temp_mired)

                    async def _send_light(value, akubela_entity_id=akubela_entity_id, entity_id=entity_id):
                        b_pct, ct_mired = value
                        kwargs = {}
                        if b_pct is not None:
                            kwargs["brightness_pct"] = b_pct
                        if ct_mired is not None:
                            kwargs["color_temp"] = ct_mired
                        await self.ak.call_service(ak_domain, "turn_on", akubela_entity_id, **kwargs)
                        logger.info("HA -> Akubela: %s яркость/цвет -> %s", entity_id, kwargs)

                    await self._light_sender.submit(entity_id, key, _send_light)
                else:
                    logger.info("%s: нет brightness/color_temp в attrs, нечего пушить", entity_id)
