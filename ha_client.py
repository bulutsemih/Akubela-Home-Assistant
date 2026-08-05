import json
import logging

from ws_base import WSClientBase
from config import HA_URL, HA_ACCESS_TOKEN

logger = logging.getLogger(__name__)


class HAClient(WSClientBase):

    def __init__(self):
        ws_url = HA_URL.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.rstrip("/") + "/api/websocket"
        super().__init__("HA", ws_url, use_ssl=ws_url.startswith("wss://"))

    async def _raw_connect(self):
        first = await self.ws.recv()
        logger.debug("[HA] handshake: %s", first)

        await self.ws.send(json.dumps({
            "type": "auth",
            "access_token": HA_ACCESS_TOKEN,
        }))

        resp = json.loads(await self.ws.recv())
        if resp.get("type") != "auth_ok":
            raise ConnectionError(f"HA auth failed: {resp}")

    async def on_reconnected(self):
        await self.subscribe_state_changed()

    async def subscribe_state_changed(self):
        await self.send_command({"type": "subscribe_events", "event_type": "state_changed"})
        logger.info("[HA] subscribed to state_changed")

    async def get_states(self):
        resp = await self.send_command({"type": "get_states"})
        return resp.get("result", []) or []

    async def call_service(self, domain, service, entity_id, **kwargs):
        service_data = {"entity_id": entity_id}
        service_data.update(kwargs)

        return await self.send_command({
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": service_data,
        })

    async def events(self):
        """Асинхронный генератор событий HA (event_type + data), например state_changed.
        Каждый вызов независим - можно слушать из нескольких мест одновременно."""
        q = self.subscribe_events_queue()
        while True:
            msg = await q.get()
            if msg.get("type") == "event":
                yield msg["event"]
