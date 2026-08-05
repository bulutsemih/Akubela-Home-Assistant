import asyncio
import logging
import sys

if sys.platform == "win32":
    # aiomqtt (через paho-mqtt) использует add_reader/add_writer, которых
    # нет в дефолтном ProactorEventLoop на Windows - переключаемся на
    # SelectorEventLoop до создания цикла событий. Иначе NativeSensorBridge
    # падает с NotImplementedError на add_reader/add_writer.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

from config import BRIDGE_HTTP_HOST, BRIDGE_HTTP_PORT, STATE_FILE, SCENE_STATE_FILE, BRIDGE_PUBLIC_URL, NATIVE_CAMERA_ENABLED
from ha_client import HAClient
from akubela_client import AkubelaClient
from device_registry import StateStore
from sync_service import SyncService
from control_server import create_app
from control_bridge import ControlBridge
from state_forwarder import StateForwarder
from native_sensor_bridge import NativeSensorBridge
from native_lock_bridge import NativeLockBridge
from native_switch_bridge import NativeSwitchBridge
from native_camera_bridge import NativeCameraBridge
from scene_sync_service import SceneSyncService
from scene_control_bridge import SceneControlBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("main")


async def run_control_server(app):
    config = uvicorn.Config(app, host=BRIDGE_HTTP_HOST, port=BRIDGE_HTTP_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def _resilient(name, coro):
    """
    ВАЖНО: asyncio.gather() по умолчанию отменяет ВСЕ остальные задачи,
    как только ОДНА из них падает с исключением - это раньше приводило
    к тому, что, например, control_server (резервный, не критичный HTTP-
    приёмник) падал из-за занятого порта и обрушивал весь бридж целиком,
    включая sync_service ПОСЕРЕДИНЕ full_sync - уже созданные на Akubela
    устройства не успевали сохраниться в bridge_state.json, и при
    следующем запуске бридж создавал их заново, плодя дубли (см.
    ARCHITECTURE.md, "падение одной части не должно убивать всё").

    Каждая фоновая задача теперь оборачивается в это: если она упадёт -
    громко логируем и НЕ роняем остальные (в частности sync_service
    продолжает жить и получит шанс нормально доработать full_sync и
    сохранить store).
    """
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("%s упал и не будет перезапущен в этом процессе - "
                          "остальные части бриджа продолжают работать", name)


async def main():
    store = StateStore(STATE_FILE)
    scene_store = StateStore(SCENE_STATE_FILE)
    ha = HAClient()
    ak = AkubelaClient()

    logger.info("Connecting to Home Assistant (%s)...", BRIDGE_HTTP_HOST)
    await ha.connect()
    await ha.subscribe_state_changed()

    logger.info("Connecting to Akubela panel...")
    await ak.connect()
    await ak.subscribe_device_events()
    await ak.subscribe_scene_events()
    await ak.subscribe_state_changed()

    try:
        await ak.set_api_server(BRIDGE_PUBLIC_URL)
        await ak.set_api_control_enable(True)
        logger.info("Registered %s as Akubela api_server_info", BRIDGE_PUBLIC_URL)
    except Exception:
        logger.exception(
            "failed to configure api_server_info on Akubela "
            "(не критично: Phase 2 теперь идёт через WS state_changed, не через HTTP)"
        )

    sync = SyncService(ha, ak, store)
    control_bridge = ControlBridge(ha, ak, store)
    state_forwarder = StateForwarder(ha, ak, store)
    native_sensors = NativeSensorBridge(ak, store)
    native_locks = NativeLockBridge(ak, store)
    native_switches = NativeSwitchBridge(ak, store)
    scene_sync = SceneSyncService(ha, ak, scene_store)
    scene_control = SceneControlBridge(ha, ak, scene_store)
    app = create_app(ha, store)

    logger.info("Bridge started.")

    tasks = [
        _resilient("sync_service", sync.run_forever()),
        _resilient("control_bridge", control_bridge.run_forever()),
        _resilient("state_forwarder", state_forwarder.run_forever()),
        _resilient("native_sensor_bridge", native_sensors.run_forever()),
        _resilient("native_lock_bridge", native_locks.run_forever()),
        _resilient("native_switch_bridge", native_switches.run_forever()),
        _resilient("scene_sync_service", scene_sync.run_forever()),
        _resilient("scene_control_bridge", scene_control.run_forever()),
        _resilient("control_server", run_control_server(app)),
    ]

    if NATIVE_CAMERA_ENABLED:
        native_cameras = NativeCameraBridge(ak, store)
        tasks.append(_resilient("native_camera_bridge", native_cameras.run_forever()))
    else:
        logger.info("NativeCameraBridge отключен (NATIVE_CAMERA_ENABLED=false) - "
                    "/api/camera_proxy подтверждённо падает на стороне панели")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
