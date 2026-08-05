"""
Rate-limit, который не теряет финальное значение.

Раньше (см. LIGHT_FORWARD_MIN_INTERVAL_SEC в state_forwarder.py/
control_bridge.py) при попадании в окно ожидания обновление просто
ОТБРАСЫВАЛОСЬ - если человек успел покрутить яркость/шторку/термостат
быстрее, чем раз в 1.5 секунды, последнее значение могло вообще не
дойти до другой стороны, и приходилось вводить его повторно, чтобы
"попасть" в свободное окно.

RateLimitedSender вместо этого запоминает последнее желаемое значение и,
если сейчас нельзя отправить, планирует ОДИН отложенный досыл ровно на
момент истечения окна - с АКТУАЛЬНЫМ на тот момент значением, а не с
тем, что было в момент планирования. Это гарантирует, что финальное
состояние всегда долетает, просто с небольшой задержкой при частых
изменениях, а не теряется.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class RateLimitedSender:

    def __init__(self, min_interval_sec, name="?"):
        self.min_interval_sec = min_interval_sec
        self.name = name
        self._last_sent_at = {}
        self._pending_value = {}
        self._scheduled = set()

    def _time_since_last(self, key):
        return time.monotonic() - self._last_sent_at.get(key, 0)

    def _ready(self, key):
        return self._time_since_last(key) >= self.min_interval_sec

    def _mark_sent(self, key):
        self._last_sent_at[key] = time.monotonic()

    async def submit(self, key, value, send_fn):
        """
        key: идентификатор сущности (обычно entity_id).
        value: текущее желаемое значение (число, строка, кортеж - что
               угодно, что понимает send_fn).
        send_fn: async callable(value) -> None, реально шлёт команду.

        Если можно отправить прямо сейчас - отправляет сразу. Иначе
        запоминает value как "ожидающее" и, если досыл ещё не
        запланирован для этого key - планирует его ровно на момент
        истечения окна. Повторные submit() для того же key, пока досыл
        ещё не случился, просто обновляют "ожидающее" значение - при
        срабатывании досыла уйдёт самое свежее из них, а не то, что
        было в момент первого submit().
        """
        self._pending_value[key] = value

        if self._ready(key):
            self._mark_sent(key)
            v = self._pending_value.pop(key)
            await send_fn(v)
            return

        if key in self._scheduled:
            return  # досыл уже запланирован, он сам подхватит свежее значение

        self._scheduled.add(key)
        delay = self.min_interval_sec - self._time_since_last(key)

        async def _flush():
            try:
                await asyncio.sleep(delay)
                self._mark_sent(key)
                v = self._pending_value.pop(key, None)
                if v is not None:
                    logger.info("[%s] отложенный досыл (rate limit истёк): %s -> %s", self.name, key, v)
                    await send_fn(v)
            except Exception:
                logger.exception("[%s] ошибка отложенного досыла для %s", self.name, key)
            finally:
                self._scheduled.discard(key)

        asyncio.create_task(_flush())
