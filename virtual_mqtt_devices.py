"""
Создаёт в HA через MQTT Discovery виртуальные устройства, которых у вас
пока нет физически - диммируемый свет с цветовой температурой и
термостат - и симулирует их поведение (отвечают на команды, держат
состояние), чтобы проверить более сложные ветки маппинга в
device_registry.py (Light/feature=2, Floor Heat с hvac_modes), а не
только простые on/off свитчи через input_boolean.

Держите скрипт запущенным, пока тестируете - он одновременно и
"создаёт" устройства в HA, и играет роль настоящего девайса, отвечая
на команды. Как только его остановите - устройства в HA останутся
(retained), но перестанут отвечать на команды.

Запуск:
    python virtual_mqtt_devices.py
"""

import asyncio
import json
import logging
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import aiomqtt

from config import MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("virtual_devices")

PREFIX = "bridgetest"

DEVICE_INFO = {
    "identifiers": ["bridge_test_devices"],
    "name": "Bridge Test Devices (виртуальные)",
    "manufacturer": "AkubelaBridge",
}

LIGHT_ID = "bridge_test_light"
CLIMATE_ID = "bridge_test_climate"


async def setup_light(mqtt: aiomqtt.Client):
    base = f"{PREFIX}/light/{LIGHT_ID}"
    config = {
        "name": "Bridge Test Light",
        "unique_id": LIGHT_ID,
        "schema": "json",
        "command_topic": f"{base}/set",
        "state_topic": f"{base}/state",
        "brightness": True,
        "supported_color_modes": ["color_temp"],
        "min_kelvin": 2700,
        "max_kelvin": 6500,
        "device": DEVICE_INFO,
    }
    await mqtt.publish(f"homeassistant/light/{LIGHT_ID}/config", json.dumps(config), retain=True)

    state = {"state": "OFF", "brightness": 255, "color_temp_kelvin": 4000, "color_mode": "color_temp"}
    await mqtt.publish(f"{base}/state", json.dumps(state), retain=True)
    logger.info("Light создан: entity light.%s", LIGHT_ID)
    return base, state


async def setup_climate(mqtt: aiomqtt.Client):
    base = f"{PREFIX}/climate/{CLIMATE_ID}"
    config = {
        "name": "Bridge Test Climate",
        "unique_id": CLIMATE_ID,
        "mode_command_topic": f"{base}/mode/set",
        "mode_state_topic": f"{base}/mode/state",
        "temperature_command_topic": f"{base}/temp/set",
        "temperature_state_topic": f"{base}/temp/state",
        "current_temperature_topic": f"{base}/current/state",
        "modes": ["off", "heat", "cool", "auto"],
        "min_temp": 15,
        "max_temp": 30,
        "temp_step": 0.5,
        "device": DEVICE_INFO,
    }
    await mqtt.publish(f"homeassistant/climate/{CLIMATE_ID}/config", json.dumps(config), retain=True)

    climate_state = {"mode": "off", "target_temp": 22.0, "current_temp": 21.5}

    await mqtt.publish(f"{base}/mode/state", climate_state["mode"], retain=True)
    await mqtt.publish(f"{base}/temp/state", str(climate_state["target_temp"]), retain=True)
    await mqtt.publish(f"{base}/current/state", str(climate_state["current_temp"]), retain=True)
    logger.info("Climate создан: entity climate.%s", CLIMATE_ID)
    return base, climate_state


async def drift_current_temperature(mqtt: aiomqtt.Client, base: str, climate_state: dict):
    """Каждые 5 секунд слегка двигает current_temp к target_temp - имитация
    реального термостата, чтобы было что синхронизировать в current_temperature."""
    while True:
        await asyncio.sleep(5)
        delta = climate_state["target_temp"] - climate_state["current_temp"]
        if abs(delta) > 0.05:
            climate_state["current_temp"] = round(climate_state["current_temp"] + delta * 0.2, 1)
            await mqtt.publish(f"{base}/current/state", str(climate_state["current_temp"]), retain=True)
            logger.info("Climate current_temp -> %s", climate_state["current_temp"])


async def main():
    print(f"Подключаюсь к MQTT {MQTT_HOST}:{MQTT_PORT}...")
    async with aiomqtt.Client(
        hostname=MQTT_HOST, port=MQTT_PORT,
        username=MQTT_USERNAME or None, password=MQTT_PASSWORD or None,
    ) as mqtt:
        light_base, light_state = await setup_light(mqtt)
        climate_base, climate_state = await setup_climate(mqtt)

        await mqtt.subscribe(f"{light_base}/set")
        await mqtt.subscribe(f"{climate_base}/mode/set")
        await mqtt.subscribe(f"{climate_base}/temp/set")

        print()
        print("Виртуальные устройства созданы и симулируются:")
        print(f"  light.{LIGHT_ID}")
        print(f"  climate.{CLIMATE_ID}  (current_temperature дрейфует к target каждые 5 сек)")
        print()
        print("Проверьте, что они появились в HA (может занять пару секунд),")
        print("потом запустите test_connect.py / main.py, чтобы бридж их подхватил.")
        print("Скрипт держите запущенным - он отвечает на команды как настоящий девайс.")
        print("(Ctrl+C для выхода)")
        print()

        asyncio.create_task(drift_current_temperature(mqtt, climate_base, climate_state))

        async for message in mqtt.messages:
            topic = str(message.topic)
            payload = message.payload.decode()

            if topic == f"{light_base}/set":
                cmd = json.loads(payload)
                light_state.update(cmd)
                await mqtt.publish(f"{light_base}/state", json.dumps(light_state), retain=True)
                logger.info("Light command: %s -> %s", cmd, light_state)

            elif topic == f"{climate_base}/mode/set":
                climate_state["mode"] = payload
                await mqtt.publish(f"{climate_base}/mode/state", payload, retain=True)
                logger.info("Climate mode -> %s", payload)

            elif topic == f"{climate_base}/temp/set":
                climate_state["target_temp"] = float(payload)
                await mqtt.publish(f"{climate_base}/temp/state", payload, retain=True)
                logger.info("Climate target temp -> %s", payload)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
