"""
Направление HA -> Akubela для сцен: пушит HA-сцены (domain "scene") в
Akubela через ak_api/scene/add, идемпотентно, с подчисткой вышедшего из
области видимости - тот же паттерн, что sync_service.py для устройств.
"""

import asyncio
import logging

from config import RESYNC_INTERVAL_SEC
from scene_registry import build_scene_spec, make_scene_id_ext
from device_registry import StateStore

logger = logging.getLogger(__name__)


class SceneSyncService:

    def __init__(self, ha_client, ak_client, store: StateStore):
        self.ha = ha_client
        self.ak = ak_client
        self.store = store  # отдельный StateStore, свой файл (см. main.py)

    async def run_forever(self):
        while True:
            try:
                await self.full_sync()
            except Exception:
                logger.exception("scene full_sync failed")
            await asyncio.sleep(RESYNC_INTERVAL_SEC)

    async def full_sync(self):
        logger.info("=== scene sync: HA -> Akubela ===")

        states = await self.ha.get_states()
        existing = await self.ak.scene_get()
        existing_by_ext = {s.get("scene_id_ext"): s for s in existing if s.get("scene_id_ext")}

        pushed = skipped = failed = 0
        seen_entities = set()

        for entity in states:
            entity_id = entity["entity_id"]
            domain = entity_id.split(".", 1)[0]
            spec = build_scene_spec(domain, entity)
            if spec is None:
                continue

            seen_entities.add(entity_id)
            scene_id_ext = make_scene_id_ext(entity_id)
            already = existing_by_ext.get(scene_id_ext)

            if already and already.get("name") == spec["name"]:
                self.store.set(entity_id, scene_id_ext=scene_id_ext, scene_id=already.get("scene_id"))
                skipped += 1
                continue

            try:
                if already:
                    await self.ak.scene_del([already["scene_id"]])
                await self.ak.scene_add(
                    scene_id_ext=scene_id_ext,
                    name=spec["name"],
                    area=spec["area"],
                    with_state=spec["with_state"],
                    icon=spec["icon"],
                )
                self.store.set(entity_id, scene_id_ext=scene_id_ext)
                pushed += 1
                logger.info("pushed scene %s -> scene_id_ext=%s", entity_id, scene_id_ext)
            except Exception:
                failed += 1
                logger.exception("failed to push scene %s", entity_id)

        existing_after = await self.ak.scene_get()
        existing_after_by_ext = {
            s.get("scene_id_ext"): s for s in existing_after if s.get("scene_id_ext")
        }
        for entity_id, rec in list(self.store.entities.items()):
            scene = existing_after_by_ext.get(rec.get("scene_id_ext"))
            if scene:
                rec["scene_id"] = scene.get("scene_id")
        self.store.save()

        for entity_id in list(self.store.entities.keys()):
            if entity_id in seen_entities:
                continue
            rec = self.store.entities[entity_id]
            scene_id = rec.get("scene_id")
            if scene_id:
                try:
                    await self.ak.scene_del([scene_id])
                    logger.info("удалил сцену вне области видимости: %s (%s)", entity_id, scene_id)
                except Exception:
                    logger.exception("не смог удалить сцену %s (%s)", entity_id, scene_id)
            self.store.remove(entity_id)
        self.store.save()

        logger.info(
            "scene sync done: pushed=%s skipped=%s failed=%s total_akubela_scenes=%s",
            pushed, skipped, failed, len(existing_after),
        )
