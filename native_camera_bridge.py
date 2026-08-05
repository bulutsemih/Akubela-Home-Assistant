"""
Камеры/видеодомофоны панели -> HA, через периодический снимок по HTTP.

ОБНОВЛЕНО по факту (probe_native_entities.py на реальной панели):
у камеры X915S нет "monitor.url" (RTSP) в атрибутах get_states() -
зато есть "entity_picture": "/api/camera_proxy/camera.<id>?token=..."
- это СТАНДАРТНЫЙ путь HA для получения снимка с камеры по HTTP. Раз
панель эмулирует HA-протокол (см. ARCHITECTURE.md), она отвечает на
этот путь так же, как отвечал бы настоящий HA - отдаёт JPEG.

Поэтому вместо RTSP (который потребовал бы ffmpeg/opencv как тяжёлую
зависимость) - просто периодически скачиваем снимок по HTTP и
публикуем его как MQTT-камеру (HA MQTT camera platform ожидает именно
сырые байты картинки в топике, никакого JSON).

Если у какого-то устройства всё же есть "monitor.url" (RTSP) - логируем
его отдельно и предлагаем добавить вручную через Generic Camera, т.к.
RTSP-поток лучше отдать штатной интеграции HA, а не пытаться
перекодировать самим.
"""

import asyncio
import json
import logging

import aiohttp
import aiomqtt

from config import (
    MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, MQTT_DISCOVERY_PREFIX,
    AKUBELA_HOST, AKUBELA_USE_SSL,
)
from device_registry import StateStore

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SEC = 10


def _our_device_ids(store: StateStore):
    return {rec["device_id"] for rec in store.entities.values() if rec.get("device_id")}


def _safe_id(entity_id: str) -> str:
    return entity_id.replace(".", "_")


def _akubela_base_url():
    scheme = "https" if AKUBELA_USE_SSL else "http"
    return f"{scheme}://{AKUBELA_HOST}"


class NativeCameraBridge:

    def __init__(self, ak_client, store: StateStore):
        self.ak = ak_client
        self.store = store
        self._mqtt = None
        self._http = None
        self._announced = set()
        self._rtsp_announced = set()

    async def run_forever(self):
        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("[MQTT] NativeCameraBridge упал, перезапуск через 5с")
                await asyncio.sleep(5)

    async def _run_once(self):
        connector = aiohttp.TCPConnector(ssl=False) if AKUBELA_USE_SSL else None
        async with aiohttp.ClientSession(connector=connector) as http, aiomqtt.Client(
            hostname=MQTT_HOST, port=MQTT_PORT,
            username=MQTT_USERNAME or None, password=MQTT_PASSWORD or None,
        ) as mqtt:
            self._mqtt = mqtt
            self._http = http
            logger.info("[MQTT] NativeCameraBridge подключен к %s:%s", MQTT_HOST, MQTT_PORT)

            while True:
                await self._poll_once()
                await asyncio.sleep(SNAPSHOT_INTERVAL_SEC)

    async def _poll_once(self):
        states = await self.ak.get_states()
        for state in states:
            entity_id = state["entity_id"]
            domain, device_id = entity_id.split(".", 1)
            if domain not in ("camera", "doorphone", "domofon"):
                continue
            if device_id in _our_device_ids(self.store):
                continue

            attrs = state.get("attributes") or {}

            monitor = attrs.get("monitor")
            if monitor and monitor.get("url") and entity_id not in self._rtsp_announced:
                self._rtsp_announced.add(entity_id)
                logger.info(
                    "%s: есть RTSP-поток - добавьте вручную через HA -> Настройки -> "
                    "Устройства и службы -> Добавить интеграцию -> Generic Camera -> "
                    "Поток: %s", entity_id, monitor.get("url"),
                )

            entity_picture = attrs.get("entity_picture")
            if entity_picture:
                await self._publish_snapshot(entity_id, attrs, entity_picture)

    async def _publish_snapshot(self, entity_id, attrs, entity_picture):
        object_id = f"akubela_{_safe_id(entity_id)}"
        base = f"{MQTT_DISCOVERY_PREFIX}/camera/{object_id}"

        if entity_id not in self._announced:
            config = {
                "name": attrs.get("friendly_name", entity_id),
                "unique_id": object_id,
                "topic": f"{base}/image",
                "device": {
                    "identifiers": ["akubela_panel_native"],
                    "name": "Akubela HyPanel (родные устройства)",
                    "manufacturer": "Akubela",
                },
            }
            await self._mqtt.publish(f"{base}/config", json.dumps(config), retain=True)
            self._announced.add(entity_id)
            logger.info("[MQTT] camera discovery опубликован: %s (снимок каждые %sс)",
                        entity_id, SNAPSHOT_INTERVAL_SEC)

        url = _akubela_base_url() + entity_picture
        try:
            image_bytes = await self._fetch(url)
        except Exception:
            logger.exception("%s: не удалось скачать снимок (%s)", entity_id, url)
            return

        if image_bytes is None:
            return

        await self._mqtt.publish(f"{base}/image", payload=image_bytes)

    async def _fetch(self, url):
        """
        Пробует получить снимок. Сначала как обычный HA (токен только в
        URL, так это работает у настоящего HA) - если панель ответит
        ошибкой, пробует ещё раз с токеном в заголовке Authorization
        (на случай, если встроенный сервер панели ждёт именно так).
        Логирует тело ошибки при неудаче - раньше писали только код
        статуса, без него было невозможно понять причину 500.
        """
        async with self._http.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                return await resp.read()
            body = await resp.text()
            logger.warning("снимок: статус %s, тело: %s (%s)", resp.status, body[:1500], url)

        token = None
        if "token=" in url:
            token = url.split("token=", 1)[1].split("&", 1)[0]

        if token:
            headers = {"Authorization": f"Bearer {token}"}
            async with self._http.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    logger.info("снимок: сработало с заголовком Authorization")
                    return await resp.read()
                body = await resp.text()
                logger.warning("снимок (с заголовком): статус %s, тело: %s", resp.status, body[:1500])

        return None
