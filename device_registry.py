"""
Маппинг HA entity -> спецификация устройства Akubela (device_type,
feature, device_config), плюс персистентное хранилище соответствия
entity_id <-> device_id_ext <-> device_id (внутренний id панели).

Таблица device_type в Appendix 2 официального документа отличает
product_type ("Sensor") от device_type ("Gas Sensor", "Temperature
Sensor" и т.п.) - именно второе нужно передавать в device/add. Старый
config.py путал их (передавал "Sensor" вместо "Gas Sensor").

Значения "feature" для Light откалиброваны ЧАСТИЧНО по дампу трафика:
  0 - без device_config -> взято как "просто on/off"
  1 - без device_config -> взято как "диммируемый (brightness)"
  2 - device_config с color_temp_min/max -> "диммируемый + цветовая t"
Варианты 3-6 в дампе тоже существуют, но их device_config не отличает
их достаточно однозначно (RGB и т.п.) - НЕ используются здесь,
пока нет реального теста с color_mode=rgb на живой панели.
"""

import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)


def is_own_mqtt_bridge_entity(entity_id: str) -> bool:
    """
    True если сущность сама пришла ИЗ Akubela (через native_sensor_bridge.py,
    native_switch_bridge.py, native_lock_bridge.py или ручную MQTT-интеграцию
    наподобие sensor.akubela_ps51_temperature). Такие сущности нельзя пушить
    обратно в Akubela - получится дублирующий виджет и вечный источник
    путаницы (см. историю с temperature/humidity, и позже - с 8-канальным
    реле, которое начало дублировать само себя тем же способом после
    добавления native_switch_bridge.py/native_lock_bridge.py, потому что
    эта функция тогда проверяла только sensor/binary_sensor).

    Два уровня эвристики:
    1. sensor/binary_sensor с именем "akubela..." - исторически проверенный
       широкий паттерн, ограниченный только этими двумя доменами.
    2. ЛЮБОЙ домен (включая switch/lock) с именем, начинающимся на полный
       слаг имени устройства "Akubela HyPanel (родные устройства)"
       ("akubela_hypanel_rodnye_ustroistva...") - HA сам генерирует такой
       entity_id, когда группирует сущности под одним "device" в MQTT
       discovery (см. identifiers=["akubela_panel_native"] во всех
       native_*_bridge.py). Это узкий, специфичный паттерн - не задевает
       обычные пользовательские сущности вроде input_boolean.akubela_test.
    """
    if "." not in entity_id:
        return False
    domain, object_id = entity_id.split(".", 1)

    if domain in ("sensor", "binary_sensor") and object_id.startswith("akubela"):
        return True

    if object_id.startswith("akubela_hypanel_rodnye_ustroistva"):
        return True

    return False


def make_device_id_ext(entity_id: str) -> str:
    """Детерминированный короткий id для device_id_ext (у панели не
    подтверждено ограничение длины, но в дампе использовались короткие
    5-значные строки, так что подстраховываемся хэшем)."""
    return "ha" + hashlib.md5(entity_id.encode()).hexdigest()[:10]


# ---------------- HA sensor device_class -> Akubela device_type ----------------

SENSOR_DEVICE_TYPE_BY_CLASS = {
    "temperature": "Temperature Sensor",
    "humidity": "Humidity Sensor",
    "illuminance": "Illuminance Sensor",
    "power": "Power Meter",
    "gas": "Gas Sensor",
    # "Air Quality Monitor" - подтверждено официальным OpenAPI-документом
    # Akubela (Appendix 2: "Air quality monitors such as TVOC and PM2.5").
    # Несколько HA device_class могут соответствовать одному и тому же
    # виджету на панели.
    "pm25": "Air Quality Monitor",
    "carbon_dioxide": "Air Quality Monitor",
    "volatile_organic_compounds": "Air Quality Monitor",
    "aqi": "Air Quality Monitor",
}

AKUBELA_DOMAIN_BY_DEVICE_TYPE = {
    "Light": "light",
    "Switch": "switch",
    "Cover": "cover",
    "Lock": "lock",
    "Floor Heat": "climate",
    "AC Remote": "climate",
    "Ventilation": "climate",
}


def resolve_state_service(domain: str, state: str):
    """
    Возвращает (service, service_data) для перевода состояния в конкретный
    call_service. В отличие от switch/light/input_boolean (turn_on/turn_off),
    у cover и lock свои имена сервисов - подтверждено рабочим кодом
    github.com/jaaneo/akubela-homeassistant (cover.py: open_cover/close_cover/
    stop_cover/set_cover_position). None - если для state/domain нет
    однозначного сервиса (например переходные opening/closing у cover -
    не форсим повторно).
    """
    if domain == "climate":
        return "set_hvac_mode", {"hvac_mode": state}
    if domain == "cover":
        if state == "open":
            return "open_cover", {}
        if state == "closed":
            return "close_cover", {}
        return None
    if domain == "lock":
        if state == "locked":
            return "lock", {}
        if state == "unlocked":
            return "unlock", {}
        return None
    if state == "on":
        return "turn_on", {}
    if state == "off":
        return "turn_off", {}
    return None


def akubela_domain_for(device_type):
    """Домен, которым Akubela называет сущность на своей стороне WS -
    может отличаться от HA-домена источника (например input_boolean -> switch)."""
    return AKUBELA_DOMAIN_BY_DEVICE_TYPE.get(device_type)


LIGHT_FEATURE_ONOFF = 0
LIGHT_FEATURE_DIMMABLE = 1
# feature=4 подтверждён вживую пробником probe_light_features.py - именно
# у него виджет на панели показывает И яркость, И цветовую температуру
# одновременно (feature=2 даёт только температуру без яркости, вопреки
# первоначальной догадке по одному лишь наличию color_temp в device_config).
LIGHT_FEATURE_COLOR_TEMP = 4


def build_device_spec(domain: str, entity: dict):
    """
    Возвращает dict(device_type=..., feature=..., device_config=...)
    для передачи в device_add(), либо None если сущность не пушим.
    """
    attrs = entity.get("attributes", {})

    if domain == "light":
        color_modes = attrs.get("supported_color_modes") or []

        if "color_temp" in color_modes:
            return {
                "device_type": "Light",
                "feature": LIGHT_FEATURE_COLOR_TEMP,
                "device_config": {
                    "color_temp_unit": 0,
                    "color_temp_min": attrs.get("min_color_temp_kelvin") or 2700,
                    "color_temp_max": attrs.get("max_color_temp_kelvin") or 6500,
                },
            }

        if "brightness" in color_modes:
            return {"device_type": "Light", "feature": LIGHT_FEATURE_DIMMABLE, "device_config": {}}

        # rgb/rgbw/rgbww/hs/xy сюда пока не смаплены - не подтверждён
        # feature-код на реальной панели, лучше зарегистрировать как
        # простой on/off, чем угадать неправильную схему.
        if color_modes and color_modes != ["onoff"]:
            logger.warning(
                "light %s: color_modes=%s не откалиброваны, регистрирую как on/off",
                entity.get("entity_id"), color_modes,
            )

        return {"device_type": "Light", "feature": LIGHT_FEATURE_ONOFF, "device_config": {}}

    if domain in ("switch", "input_boolean"):
        return {"device_type": "Switch", "feature": 0, "device_config": {}}

    if domain == "sensor":
        device_class = attrs.get("device_class")
        akubela_type = SENSOR_DEVICE_TYPE_BY_CLASS.get(device_class)
        if not akubela_type:
            logger.debug(
                "sensor %s: device_class=%r не смаплен, пропускаю",
                entity.get("entity_id"), device_class,
            )
            return None
        # feature для Sensor не встречается в дампе трафика вообще -
        # используем 0 по умолчанию, требует проверки на реальной панели.
        return {"device_type": akubela_type, "feature": 0, "device_config": {}}

    if domain == "climate":
        hvac_modes = attrs.get("hvac_modes") or []
        modes_dict = {f"{i + 1:02d}": m for i, m in enumerate(hvac_modes)}
        return {
            "device_type": "Floor Heat",
            "feature": 1 if hvac_modes else 0,
            "device_config": {
                "temp_min": attrs.get("min_temp", 15),
                "temp_max": attrs.get("max_temp", 30),
                "fan_ctrl_mode": 1,
                "hvac_modes": modes_dict,
                "fan_modes": {},
                "swing_modes": {},
            },
        }

    if domain == "cover":
        # Откалибровано визуально через probe_cover_features.py + веб-морду
        # Akubela (меню "Выберите функцию" в редакторе устройства):
        #   0, 2 - три кнопки открыть/пауза/закрыть, БЕЗ слежения за ходом
        #          (отсюда нестабильность с открытием - панель не знает,
        #          когда "открытие завершено")
        #   1, 4, 7 - слайдер/позиция (1 и 7 одинаковые, 4 "по ширине")
        #   3, 5, 9 - открытие/закрытие "по ширине", кнопочный режим
        #   6, 8 - угол наклона ламелей (жалюзи), не то, что нужно шторам
        # Берём 1 - слайдер с реальным слежением за позицией.
        return {"device_type": "Cover", "feature": 1, "device_config": {}}

    if domain == "lock":
        # feature для Lock не встречается в дампе трафика вообще -
        # 0 по умолчанию, требует проверки на реальной панели.
        return {"device_type": "Lock", "feature": 0, "device_config": {}}

    return None


class StateStore:
    """Персистентная таблица entity_id -> {device_id_ext, device_id, device_type, feature}."""

    def __init__(self, path):
        self.path = path
        self.entities = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.entities = json.load(f)
            except Exception:
                logger.exception("failed to load state file %s", self.path)
                self.entities = {}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.entities, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, entity_id):
        return self.entities.get(entity_id)

    def set(self, entity_id, **kwargs):
        rec = self.entities.setdefault(entity_id, {})
        rec.update(kwargs)

    def remove(self, entity_id):
        if entity_id in self.entities:
            del self.entities[entity_id]

    def device_id_ext_to_entity(self, device_id_ext):
        for entity_id, rec in self.entities.items():
            if rec.get("device_id_ext") == device_id_ext:
                return entity_id
        return None
