"""
Клиент к встроенному WS API панели Akubela (PS51 / HyPanel).

Протокол подтверждён двумя независимыми источниками:
1. Сниффинг трафика веб-морды панели (SEND/RECV дамп WebSocket).
2. https://github.com/jaaneo/akubela-homeassistant - независимая
   интеграция, описывающая тот же handshake (auth_required/auth/auth_ok)
   на том же пути /api/websocket.

Все команды ak_api/* ниже (device/add, device/get, device/del,
scene/add, scene/get, scene/del, config/set, id_ext/next) взяты из
реального дампа трафика - формат запросов и структура успешного ответа
проверены. Значения "feature" для Light откалиброваны частично (см.
README, раздел "Что не подтверждено").
"""

import json
import logging

from ws_base import WSClientBase
from config import AKUBELA_HOST, AKUBELA_USE_SSL, AKUBELA_ACCESS_TOKEN

logger = logging.getLogger(__name__)


class AkubelaClient(WSClientBase):

    def __init__(self):
        scheme = "wss" if AKUBELA_USE_SSL else "ws"
        url = f"{scheme}://{AKUBELA_HOST}/api/websocket"
        super().__init__("Akubela", url, use_ssl=AKUBELA_USE_SSL)

    async def _raw_connect(self):
        first = await self.ws.recv()
        logger.debug("[Akubela] handshake: %s", first)

        await self.ws.send(json.dumps({
            "type": "auth",
            "access_token": AKUBELA_ACCESS_TOKEN,
        }))

        resp = json.loads(await self.ws.recv())
        if resp.get("type") != "auth_ok":
            raise ConnectionError(f"Akubela auth failed: {resp}")

    async def on_reconnected(self):
        await self.subscribe_device_events()
        await self.subscribe_scene_events()
        await self.subscribe_state_changed()

    async def subscribe_device_events(self):
        await self.send_command({"type": "subscribe_events", "event_type": "ak_device_event"})
        logger.info("[Akubela] subscribed to ak_device_event")

    async def subscribe_scene_events(self):
        await self.send_command({"type": "subscribe_events", "event_type": "ak_scene_event"})
        logger.info("[Akubela] subscribed to ak_scene_event")

    async def subscribe_state_changed(self):
        """
        Панель прозрачно проксирует протокол HA (ha_version 4.0.1) - те же
        state_changed события, что и настоящий HA. Подтверждено логом
        test_events.py (виртуальное устройство "Klapan" меняло state on/off)
        и независимой интеграцией github.com/jaaneo/akubela-homeassistant.
        Это основной канал для направления Akubela -> HA (Phase 2),
        надёжнее чем гадать формат HTTP callback.
        """
        await self.send_command({"type": "subscribe_events", "event_type": "state_changed"})
        logger.info("[Akubela] subscribed to state_changed")

    async def call_service(self, domain, service, entity_id, **kwargs):
        """
        Управление любой сущностью на панели (включая наши виртуальные
        устройства - у них entity_id вида "{domain}.{device_id}", это видно
        в ak_device_event.payload.attributes[].entity_id) - тем же
        call_service, что использует github.com/jaaneo/akubela-homeassistant
        для управления световыми/switch-сущностями панели.
        """
        data = {"entity_id": entity_id}
        data.update(kwargs)
        return await self.send_command({
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": data,
        })

    # ---------------- devices ----------------

    async def device_add(self, device_id_ext, name, device_type, feature=0,
                          area="aliving_room", url=None, device_config=None):
        resp = await self.send_command({
            "type": "ak_api/device/add",
            "device_id_ext": device_id_ext,
            "name": name,
            "area": area,
            "url": url or "",
            "device_type": device_type,
            "feature": feature,
            "device_config": device_config or {},
        })
        if not resp.get("success"):
            raise RuntimeError(f"device/add failed for {device_id_ext}: {resp}")
        return resp

    async def device_del(self, device_ids):
        return await self.send_command({
            "type": "ak_api/device/del",
            "device_ids": list(device_ids),
        })

    async def device_get(self):
        resp = await self.send_command({"type": "ak_api/device/get"})
        return resp.get("result", []) or []

    # ---------------- scenes ----------------

    async def scene_add(self, scene_id_ext, name, url=None, area=None,
                         with_state=1, icon=0):
        resp = await self.send_command({
            "type": "ak_api/scene/add",
            "scene_id_ext": scene_id_ext,
            "name": name,
            "url": url or "",
            "area": area or ["all"],
            "with_state": with_state,
            "icon": icon,
        })
        if not resp.get("success"):
            raise RuntimeError(f"scene/add failed for {scene_id_ext}: {resp}")
        return resp

    async def scene_del(self, scene_ids):
        return await self.send_command({
            "type": "ak_api/scene/del",
            "scene_ids": list(scene_ids),
        })

    async def scene_get(self):
        resp = await self.send_command({"type": "ak_api/scene/get"})
        return resp.get("result", []) or []

    # ---------------- config ----------------

    async def set_api_server(self, url, mode=0):
        """Регистрирует наш HTTP-сервер как получателя callback'ов панели."""
        return await self.send_command({
            "type": "ak_api/config/set",
            "config_type": "api_server_info",
            "param": {"mode": mode, "url": url},
        })

    async def set_api_whitelist(self, ip_list, enable=0):
        return await self.send_command({
            "type": "ak_api/config/set",
            "config_type": "api_white_list",
            "param": {"list": list(ip_list), "enable": enable},
        })

    async def set_api_control_enable(self, enable=True):
        return await self.send_command({
            "type": "ak_api/config/set",
            "config_type": "api_control_enable",
            "param": {"enable": enable},
        })

    async def id_ext_next(self, mode=1):
        resp = await self.send_command({"type": "ak_api/id_ext/next", "mode": mode})
        return resp.get("result")

    async def get_states(self):
        """
        Все сущности панели (родные устройства + наши виртуальные) - тот
        же HA-style get_states, что использует HAClient. Нужен для
        обнаружения "родных" сенсоров, которые мы сами не регистрировали
        (см. native_sensor_bridge.py).
        """
        resp = await self.send_command({"type": "get_states"})
        return resp.get("result", []) or []

    # ---------------- events ----------------

    async def events(self):
        """Асинхронный генератор событий панели (ak_device_event, ak_scene_event, ...).
        Каждый вызов независим - можно слушать из нескольких мест одновременно."""
        q = self.subscribe_events_queue()
        while True:
            msg = await q.get()
            if msg.get("type") == "event":
                yield msg["event"]
