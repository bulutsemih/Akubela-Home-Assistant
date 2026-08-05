"""
Аналог device_registry.py, но для сцен.

Схема ak_api/scene/get подтверждена реальным ответом панели:

{
  "scene_id": "6f8a38dbef714df6808e6ae49903a675",
  "name": "anima",
  "icon": 0,
  "scene_id_ext": "00002",
  "area": ["all"],
  "url": "192.168.33.137:8080",
  "with_state": 1
}

В HA сценам логично соответствуют domain "scene" (scene.turn_on) и,
опционально, "script"/"automation" с ручным запуском - но пока
реализован только домен scene, чтобы не плодить неоднозначностей.
"""

import hashlib
import logging

logger = logging.getLogger(__name__)


def make_scene_id_ext(entity_id: str) -> str:
    return "hs" + hashlib.md5(entity_id.encode()).hexdigest()[:10]


def build_scene_spec(domain: str, entity: dict):
    if domain != "scene":
        return None

    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", entity["entity_id"])
    if len(name) > 32:
        name = name[:32]
    return {
        "name": name,
        "area": ["all"],
        "with_state": 1,
        "icon": 0,
    }
