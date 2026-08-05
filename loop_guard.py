"""
Защита от эхо-петель между HA и Akubela.

Раньше rate-limit был только у яркости/цвета света. Обычный on/off
(режим устройства) пересылался без всякого троттлинга - если что-то
(сторонняя автоматизация на панели/в HA, или сама эхо-петля) меняет
состояние очень часто, дедуп по "совпадает ли с последним отправленным
значением" не спасает, т.к. значение каждый раз ДЕЙСТВИТЕЛЬНО другое
(on -> off -> on -> off...).

LoopGuard считает переключения в скользящем окне; если их слишком много -
считает это петлёй (человек физически не жмёт кнопку 10 раз в секунду),
временно перестаёт пересылать эту сущность и громко предупреждает.
"""

import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class LoopGuard:

    def __init__(self, max_events=6, window_sec=3.0, suspend_sec=60.0, name="?"):
        self.max_events = max_events
        self.window_sec = window_sec
        self.suspend_sec = suspend_sec
        self.name = name
        self._history = defaultdict(deque)
        self._suspended_until = {}
        self._warned = set()

    def allowed(self, key) -> bool:
        now = time.monotonic()

        suspended_until = self._suspended_until.get(key)
        if suspended_until and now < suspended_until:
            return False
        if suspended_until and now >= suspended_until:
            self._suspended_until.pop(key, None)
            self._warned.discard(key)
            logger.warning("[%s] %s: подозрение на петлю снято, пересылка возобновлена", self.name, key)

        hist = self._history[key]
        hist.append(now)
        while hist and now - hist[0] > self.window_sec:
            hist.popleft()

        if len(hist) > self.max_events:
            self._suspended_until[key] = now + self.suspend_sec
            if key not in self._warned:
                self._warned.add(key)
                logger.error(
                    "[%s] %s: похоже на бесконечную петлю (%s переключений за %sс) - "
                    "ПРИОСТАНАВЛИВАЮ пересылку на %sс. Проверьте автоматизации/сцены, "
                    "которые могут инвертировать это состояние в ответ на изменение.",
                    self.name, key, len(hist), self.window_sec, self.suspend_sec,
                )
            return False

        return True
