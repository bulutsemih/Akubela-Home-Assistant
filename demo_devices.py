"""
Создаёт в HA через MQTT Discovery 4 демонстрационных устройства и
симулирует их поведение (отвечают на команды, держат состояние) - для
показа полного набора типов, которые умеет бридж:

  cover.demo_shtory           - шторы, с позицией (0-100)
  climate.demo_konditsioner   - кондиционер (off/cool/dry/fan_only/auto)
  climate.demo_teply_pol      - тёплый пол (off/heat)
  sensor.demo_kachestvo_vozdukha - датчик качества воздуха (PM2.5)

Держите скрипт запущенным, пока идёт демонстрация - как и
virtual_mqtt_devices.py, он одновременно и создаёт устройства в HA, и
играет роль настоящих девайсов, отвечая на команды. Датчик воздуха
дополнительно "дышит" - значение плавно меняется само, чтобы было
видно живое обновление в обе стороны.

ВАЖНО: климат в обоих случаях (кондиционер и тёплый пол) регистрируется
в Akubela одинаково, как "Floor Heat" (device_registry.py пока не
различает эти два климат-сценария на стороне Akubela - см.
DEVICE_GUIDE.md, если нужно разделить на разные виджеты).

Запуск:
    python demo_devices.py
"""

import asyncio
import json
import logging
import random
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt

from config import MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("demo_devices")

PREFIX = "demo"

DEVICE_INFO = {
    "identifiers": ["demo_devices"],
    "name": "Демо-устройства (виртуальные)",
    "manufacturer": "AkubelaBridge",
}

COVER_ID = "demo_shtory"
AC_ID = "demo_konditsioner"
FLOOR_ID = "demo_teply_pol"
AIR_ID = "demo_kachestvo_vozdukha"


# ---------------- cover (шторы) ----------------

async def setup_cover(mqtt: aiomqtt.Client):
    base = f"{PREFIX}/cover/{COVER_ID}"
    config = {
        "name": "Демо Шторы",
        "unique_id": COVER_ID,
        "command_topic": f"{base}/set",
        "state_topic": f"{base}/state",
        "position_topic": f"{base}/position",
        "set_position_topic": f"{base}/set_position",
        "device": DEVICE_INFO,
    }
    await mqtt.publish(f"homeassistant/cover/{COVER_ID}/config", json.dumps(config), retain=True)

    cover_state = {"position": 0}
    await mqtt.publish(f"{base}/state", "closed", retain=True)
    await mqtt.publish(f"{base}/position", "0", retain=True)
    logger.info("Cover создан: entity cover.%s", COVER_ID)
    return base, cover_state


async def handle_cover_command(mqtt, base, cover_state, payload):
    if payload == "OPEN":
        cover_state["position"] = 100
    elif payload == "CLOSE":
        cover_state["position"] = 0
    # STOP - оставляем позицию как есть

    state = "open" if cover_state["position"] > 0 else "closed"
    await mqtt.publish(f"{base}/state", state, retain=True)
    await mqtt.publish(f"{base}/position", str(cover_state["position"]), retain=True)
    logger.info("Cover command: %s -> position=%s", payload, cover_state["position"])


async def handle_cover_set_position(mqtt, base, cover_state, payload):
    cover_state["position"] = int(payload)
    state = "open" if cover_state["position"] > 0 else "closed"
    await mqtt.publish(f"{base}/state", state, retain=True)
    await mqtt.publish(f"{base}/position", str(cover_state["position"]), retain=True)
    logger.info("Cover set_position -> %s", cover_state["position"])


# ---------------- climate (кондиционер / тёплый пол) ----------------

def climate_config(unique_id, name, modes, min_temp, max_temp):
    base = f"{PREFIX}/climate/{unique_id}"
    return base, {
        "name": name,
        "unique_id": unique_id,
        "mode_command_topic": f"{base}/mode/set",
        "mode_state_topic": f"{base}/mode/state",
        "temperature_command_topic": f"{base}/temp/set",
        "temperature_state_topic": f"{base}/temp/state",
        "current_temperature_topic": f"{base}/current/state",
        "modes": modes,
        "min_temp": min_temp,
        "max_temp": max_temp,
        "temp_step": 0.5,
        "device": DEVICE_INFO,
    }


async def setup_climate(mqtt: aiomqtt.Client, unique_id, name, modes, min_temp, max_temp, start_current):
    base, config = climate_config(unique_id, name, modes, min_temp, max_temp)
    await mqtt.publish(f"homeassistant/climate/{unique_id}/config", json.dumps(config), retain=True)

    state = {"mode": "off", "target_temp": (min_temp + max_temp) / 2, "current_temp": start_current}
    await mqtt.publish(f"{base}/mode/state", state["mode"], retain=True)
    await mqtt.publish(f"{base}/temp/state", str(state["target_temp"]), retain=True)
    await mqtt.publish(f"{base}/current/state", str(state["current_temp"]), retain=True)
    logger.info("Climate создан: entity climate.%s (%s)", unique_id, name)
    return base, state


async def drift_current_temperature(mqtt, base, state, label):
    while True:
        await asyncio.sleep(5)
        delta = state["target_temp"] - state["current_temp"]
        if abs(delta) > 0.05:
            state["current_temp"] = round(state["current_temp"] + delta * 0.2, 1)
            await mqtt.publish(f"{base}/current/state", str(state["current_temp"]), retain=True)
            logger.info("%s current_temp -> %s", label, state["current_temp"])


# ---------------- sensor (качество воздуха) ----------------

async def setup_air_quality(mqtt: aiomqtt.Client):
    base = f"{PREFIX}/sensor/{AIR_ID}"
    config = {
        "name": "Демо Качество воздуха (PM2.5)",
        "unique_id": AIR_ID,
        "state_topic": f"{base}/state",
        "unit_of_measurement": "µg/m³",
        "device_class": "pm25",
        "state_class": "measurement",
        "device": DEVICE_INFO,
    }
    await mqtt.publish(f"homeassistant/sensor/{AIR_ID}/config", json.dumps(config), retain=True)
    await mqtt.publish(f"{base}/state", "12", retain=True)
    logger.info("Sensor создан: entity sensor.%s", AIR_ID)
    return base


async def breathe_air_quality(mqtt, base):
    value = 12.0
    while True:
        await asyncio.sleep(4)
        value = max(2.0, min(80.0, value + random.uniform(-4, 4)))
        await mqtt.publish(f"{base}/state", str(round(value, 1)), retain=True)


# ---------------- main ----------------

async def main():
    print(f"Подключаюсь к MQTT {MQTT_HOST}:{MQTT_PORT}...")
    async with aiomqtt.Client(
        hostname=MQTT_HOST, port=MQTT_PORT,
        username=MQTT_USERNAME or None, password=MQTT_PASSWORD or None,
    ) as mqtt:
        cover_base, cover_state = await setup_cover(mqtt)
        ac_base, ac_state = await setup_climate(
            mqtt, AC_ID, "Демо Кондиционер",
            ["off", "cool", "dry", "fan_only", "auto"], 16, 30, 24.0,
        )
        floor_base, floor_state = await setup_climate(
            mqtt, FLOOR_ID, "Демо Тёплый пол",
            ["off", "heat"], 15, 35, 21.0,
        )
        air_base = await setup_air_quality(mqtt)

        await mqtt.subscribe(f"{cover_base}/set")
        await mqtt.subscribe(f"{cover_base}/set_position")
        await mqtt.subscribe(f"{ac_base}/mode/set")
        await mqtt.subscribe(f"{ac_base}/temp/set")
        await mqtt.subscribe(f"{floor_base}/mode/set")
        await mqtt.subscribe(f"{floor_base}/temp/set")

        print()
        print("Демо-устройства созданы и симулируются:")
        print(f"  cover.{COVER_ID}")
        print(f"  climate.{AC_ID}")
        print(f"  climate.{FLOOR_ID}")
        print(f"  sensor.{AIR_ID}  (значение меняется само каждые ~4 сек)")
        print()
        print("Проверьте, что они появились в HA, потом запустите test_connect.py,")
        print("чтобы бридж подхватил их и создал виджеты на панели.")
        print("Скрипт держите запущенным - он и есть эти устройства. (Ctrl+C для выхода)")
        print()

        asyncio.create_task(drift_current_temperature(mqtt, ac_base, ac_state, "AC"))
        asyncio.create_task(drift_current_temperature(mqtt, floor_base, floor_state, "FloorHeat"))
        asyncio.create_task(breathe_air_quality(mqtt, air_base))

        async for message in mqtt.messages:
            topic = str(message.topic)
            payload = message.payload.decode()

            if topic == f"{cover_base}/set":
                await handle_cover_command(mqtt, cover_base, cover_state, payload)
            elif topic == f"{cover_base}/set_position":
                await handle_cover_set_position(mqtt, cover_base, cover_state, payload)
            elif topic == f"{ac_base}/mode/set":
                ac_state["mode"] = payload
                await mqtt.publish(f"{ac_base}/mode/state", payload, retain=True)
                logger.info("AC mode -> %s", payload)
            elif topic == f"{ac_base}/temp/set":
                ac_state["target_temp"] = float(payload)
                await mqtt.publish(f"{ac_base}/temp/state", payload, retain=True)
                logger.info("AC target temp -> %s", payload)
            elif topic == f"{floor_base}/mode/set":
                floor_state["mode"] = payload
                await mqtt.publish(f"{floor_base}/mode/state", payload, retain=True)
                logger.info("FloorHeat mode -> %s", payload)
            elif topic == f"{floor_base}/temp/set":
                floor_state["target_temp"] = float(payload)
                await mqtt.publish(f"{floor_base}/temp/state", payload, retain=True)
                logger.info("FloorHeat target temp -> %s", payload)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
