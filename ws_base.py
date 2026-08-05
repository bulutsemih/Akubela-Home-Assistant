"""
Общий надёжный WebSocket-клиент для протокола вида Home Assistant WS API
(и HA, и панель Akubela используют один и тот же стиль: auth handshake,
команды с "id", ответы "type":"result", события "type":"event").

Главное отличие от старого кода проекта: здесь ОДИН читающий таск на
сокет. Команды и события разбираются в одном месте и раздаются либо
через asyncio.Future (по id), либо через asyncio.Queue (события).
Раньше send() и events() независимо дёргали ws.recv() из разных
корутин, и один поток мог "съесть" сообщение, предназначенное другому.
"""

import asyncio
import json
import logging
import ssl as ssl_module

import websockets

logger = logging.getLogger(__name__)


class WSClientBase:

    def __init__(self, name, url, use_ssl=False, connect_timeout=10):
        self.name = name
        self.url = url
        self.use_ssl = use_ssl
        self.connect_timeout = connect_timeout

        self.ws = None
        self._msg_id = 0
        self._pending = {}          # id -> asyncio.Future
        self._event_subscribers = []  # list[asyncio.Queue] - честный fan-out, не общая очередь
        self._reader_task = None
        self._connected = asyncio.Event()
        self._closing = False

    def next_id(self):
        self._msg_id += 1
        return self._msg_id

    async def _raw_connect(self):
        """Переопределяется в наследнике: делает handshake/auth."""
        raise NotImplementedError

    async def on_reconnected(self):
        """Переопределяется в наследнике: пересоздать подписки и т.п."""
        pass

    async def connect(self):
        self._closing = False
        backoff = 2
        while True:
            try:
                ssl_ctx = ssl_module._create_unverified_context() if self.use_ssl else None
                self.ws = await asyncio.wait_for(
                    websockets.connect(
                        self.url, ssl=ssl_ctx, ping_interval=20, ping_timeout=20
                    ),
                    timeout=self.connect_timeout,
                )
                await self._raw_connect()
                self._connected.set()
                self._reader_task = asyncio.create_task(self._reader_loop())
                logger.info("[%s] connected: %s", self.name, self.url)
                return
            except Exception as e:
                logger.warning("[%s] connect failed (%s), retry in %ss", self.name, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _reader_loop(self):
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[%s] non-json frame: %r", self.name, raw[:200])
                    continue

                msg_id = msg.get("id")

                if msg.get("type") == "event":
                    await self._dispatch_event(msg)
                elif msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                else:
                    # непрошенное сообщение - тоже раздаём подписчикам, пригодится для отладки
                    await self._dispatch_event(msg)
        except websockets.ConnectionClosed as e:
            logger.warning("[%s] connection closed: %s", self.name, e)
        except Exception:
            logger.exception("[%s] reader loop crashed", self.name)
        finally:
            self._connected.clear()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError(f"{self.name} disconnected"))
            self._pending.clear()
            if not self._closing:
                asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        logger.info("[%s] reconnecting...", self.name)
        await self.connect()
        try:
            await self.on_reconnected()
        except Exception:
            logger.exception("[%s] on_reconnected failed", self.name)

    async def send_command(self, payload, timeout=15):
        await self._connected.wait()
        msg_id = self.next_id()
        payload = dict(payload)
        payload["id"] = msg_id

        fut = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        await self.ws.send(json.dumps(payload))

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(
                f"[{self.name}] no response for id={msg_id} (type={payload.get('type')})"
            )

    async def _dispatch_event(self, msg):
        for q in self._event_subscribers:
            await q.put(msg)

    def subscribe_events_queue(self):
        """
        Каждый вызов создаёт СВОЮ независимую очередь, получающую копию
        каждого события. Так несколько независимых потребителей (например
        живой форвардер и отладочный принтер) не воруют сообщения друг у
        друга, как было бы с одной общей очередью/next_event().
        """
        q = asyncio.Queue()
        self._event_subscribers.append(q)
        return q

    async def close(self):
        self._closing = True
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            await self.ws.close()
