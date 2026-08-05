"""
Направление Akubela -> HA для сцен: нажатие сцены на панели должно
запускать соответствующую HA-сцену.

ЧЕСТНО: формат активации сцены с панели НЕ подтверждён реальным пакетом
(в отличие от device state_changed). Подтверждено только обратное
направление - github.com/jaaneo/akubela-homeassistant дёргает
call_service("scene", "turn_on", {"entity_id": "scene.<id>"}), когда HA
активирует сцену НА панели. Это значит, что сцены на WS-канале Akubela
действительно имеют entity_id вида "scene.<scene_id>" - по аналогии с
устройствами, отсюда и two best-effort механизма ниже.

Если ни один не сработает - нажмите сцену на панели во время работы
main.py/test_scene_roundtrip.py и пришлите строки "[RAW]" из лога.
"""

import logging

from device_registry import StateStore

logger = logging.getLogger(__name__)


class SceneControlBridge:

    KNOWN_NON_ACTIVATION_ACTIONS = {"create", "update", "delete"}

    def __init__(self, ha_client, ak_client, store: StateStore):
        self.ha = ha_client
        self.ak = ak_client
        self.store = store

    async def run_forever(self):
        async for event in self.ak.events():
            et = event.get("event_type")

            if et == "ak_scene_event":
                data = event.get("data", {})
                action = data.get("action")
                logger.info("[RAW] ak_scene_event action=%s payload=%s", action, data.get("payload"))
                if action not in self.KNOWN_NON_ACTIVATION_ACTIONS:
                    scene_ids = (data.get("payload") or {}).get("scene_ids") or []
                    for scene_id in scene_ids:
                        await self._activate(scene_id)

            elif et == "state_changed":
                new_state = event.get("data", {}).get("new_state") or {}
                entity_id_ak = new_state.get("entity_id", "")
                if entity_id_ak.startswith("scene."):
                    logger.info("[RAW] scene state_changed: %s", new_state)
                    await self._activate(entity_id_ak.split(".", 1)[1])

    async def _activate(self, scene_id):
        entity_id = None
        for eid, rec in self.store.entities.items():
            if rec.get("scene_id") == scene_id:
                entity_id = eid
                break
        if not entity_id:
            return

        logger.info("Akubela -> HA: активирую сцену %s (%s)", entity_id, scene_id)
        try:
            await self.ha.call_service("scene", "turn_on", entity_id)
        except Exception:
            logger.exception("не удалось активировать сцену %s в HA", entity_id)
