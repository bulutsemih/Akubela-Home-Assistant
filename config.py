import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------- Home Assistant (ваш "боевой" HA) ----------

# ВАЖНО: у вас Docker не пробросил порт 8123 наружу на 192.168.33.90 -
# HA реально был доступен только на localhost:8123 (найдено экспериментально,
# curl http://localhost:8123 -> 200, Test-NetConnection 192.168.33.90:8123 -> fail).
# Если у вас другая машина/схема сети - проверьте оба варианта.
HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
HA_ACCESS_TOKEN = os.environ.get(
    "HA_ACCESS_TOKEN",
    "REPLACE_ME_HA_LONG_LIVED_TOKEN",
)

# ---------- Akubela PS51 / HyPanel ----------
#
# ВАЖНО: у панели свой собственный токен, отдельный от HA_ACCESS_TOKEN.
# Он лежит в localStorage браузера, залогиненного в веб-морду панели:
#   http://<IP_ПАНЕЛИ>  -> DevTools -> Console ->
#   localStorage.getItem("AKUBELA_USER-TOKEN")
# Формат похож на HA-токен ("new_project-..."), потому что панель
# внутри сама поднимает HA-совместимый WS-сервер (ha_version 4.0.1) -
# это подтверждено сниффингом трафика и независимым проектом
# github.com/jaaneo/akubela-homeassistant.

AKUBELA_HOST = os.environ.get("AKUBELA_HOST", "192.168.33.90")  # можно "host:port"
AKUBELA_USE_SSL = os.environ.get("AKUBELA_USE_SSL", "false").lower() == "true"
# localStorage.getItem("AKUBELA_USER-TOKEN") возвращает JSON-объект вида
#   {"value": "new_project-xxxx...", "time": 172..., "expire": null}
# сюда нужно положить именно поле .value, а не весь JSON целиком.
AKUBELA_ACCESS_TOKEN = os.environ.get(
    "AKUBELA_ACCESS_TOKEN",
    "REPLACE_ME_AKUBELA_PANEL_TOKEN",
)

# ---------- Наш HTTP-сервер, куда Akubela шлёт callback'и ----------

BRIDGE_HTTP_HOST = os.environ.get("BRIDGE_HTTP_HOST", "0.0.0.0")
BRIDGE_HTTP_PORT = int(os.environ.get("BRIDGE_HTTP_PORT", "8080"))

# Адрес:порт, который мы регистрируем в Akubela (api_server_info.url и
# url в каждом device/add / scene/add). Панель шлёт сюда команды
# управления и (предположительно) запросы состояния наших виртуальных
# устройств - формат этих запросов НЕ подтверждён сниффингом, см. README.
BRIDGE_PUBLIC_URL = os.environ.get("BRIDGE_PUBLIC_URL", "192.168.33.137:8080")

# ---------- Что пушим из HA в Akubela ----------
#
# ВАЖНО: sensor намеренно НЕ в дефолте - подтверждено (probe_air_quality.py),
# что панель не создаёт устройства категории Sensor через device/add вообще,
# ни при каком device_type (см. ARCHITECTURE.md раздел 14). Родные датчики
# идут в обратную сторону (Akubela -> HA) через native_sensor_bridge.py.

PUSH_DOMAINS = [
    d.strip()
    for d in os.environ.get("PUSH_DOMAINS", "light,switch,climate,input_boolean,cover,lock").split(",")
    if d.strip()
]

EXCLUDE_ENTITIES = {
    e.strip() for e in os.environ.get("EXCLUDE_ENTITIES", "").split(",") if e.strip()
}

STATE_FILE = os.environ.get("STATE_FILE", "bridge_state.json")
SCENE_STATE_FILE = os.environ.get("SCENE_STATE_FILE", "bridge_scene_state.json")

RESYNC_INTERVAL_SEC = int(os.environ.get("RESYNC_INTERVAL_SEC", "300"))

# ---------- MQTT (для нативных датчиков Akubela -> HA через MQTT Discovery) ----------
#
# Проверьте реальные host/port/логин вашего брокера в docker-compose.yml
# (контейнер akubela-mqtt) - дефолты ниже могут не совпадать с вашей сетью.

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_DISCOVERY_PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")

# Какие домены нативных сущностей Akubela тянем в HA как read-only сенсоры
NATIVE_SENSOR_DOMAINS = {
    d.strip()
    for d in os.environ.get("NATIVE_SENSOR_DOMAINS", "sensor,binary_sensor").split(",")
    if d.strip()
}

# Отключено по умолчанию: /api/camera_proxy на локальной панели падает с
# 500 Internal Server Error - это баг на СТОРОНЕ ПАНЕЛИ (трейсбек её
# собственного aiohttp-сервера, не нашего кода), подтверждено вживую.
# Включайте только если проверите, что на вашей прошивке эндпоинт
# реально отвечает (см. native_camera_bridge.py).
NATIVE_CAMERA_ENABLED = os.environ.get("NATIVE_CAMERA_ENABLED", "false").lower() == "true"

# ---------- Rate-limit / антипетлевая защита ----------
#
# color_temp для света поначалу вызывал бесконечный эхо-цикл между HA и
# Akubela (конвертация kelvin<->mired давала расхождение при каждом
# проходе). Решено не отключением, а через debounce
# (rate_limited_sender.py) + loop_guard.py - оставлено включённым по
# умолчанию, т.к. проблема решена структурно, не костылём отключения.
FORWARD_LIGHT_COLOR_TEMP = os.environ.get("FORWARD_LIGHT_COLOR_TEMP", "true").lower() == "true"

# Минимальный интервал между двумя пересылками одного и того же
# "непрерывного" значения (яркость/цвет света, позиция cover, целевая
# температура climate) для одной сущности - защита от резонансных
# петель между HA и Akubela. Используется rate_limited_sender.py -
# который НЕ теряет финальное значение при попадании в это окно (в
# отличие от первой версии), а откладывает и досылает его сразу после
# истечения окна.
LIGHT_FORWARD_MIN_INTERVAL_SEC = float(os.environ.get("LIGHT_FORWARD_MIN_INTERVAL_SEC", "1.5"))
