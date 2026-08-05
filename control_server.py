"""
HTTP-сервер, который принимает callback'и от панели Akubela (адрес,
который мы регистрируем через ak_api/config/set config_type=
api_server_info, и который передаём в url при device/add).

ВНИМАНИЕ: формат этих запросов НЕ подтверждён сниффингом трафика - в
собранном дампе никто не нажимал кнопку на реальном виртуальном
устройстве, поэтому нет примера реального callback'а. Ниже - рабочая
ГИПОТЕЗА (основана на старом api_server.py проекта, где было
{"device_id":..., "state": "on"/"off"}), плюс catch-all роут, который
логирует АБСОЛЮТНО ЛЮБОЙ входящий запрос как есть.

Как откалибровать: подключите бридж к реальной панели, включите
Control API (config/set api_control_enable=true), нажмите на
виртуальное устройство в интерфейсе Akubela и посмотрите лог bridge -
там будет "UNHANDLED callback ..." с полным телом запроса. После этого
допишите обработку в device_control() по реальному формату.
"""

import json
import logging

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def create_app(ha_client, store):
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"status": "Akubela bridge control server running"}

    @app.post("/device/control")
    async def device_control(request: Request):
        body = await request.body()
        logger.info("callback /device/control: %s", body.decode("utf-8", "replace"))

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid json"}

        device_id = data.get("device_id") or data.get("device_id_ext")
        entity_id = store.device_id_ext_to_entity(device_id) if device_id else None

        if not entity_id:
            logger.warning("callback for unknown device: %s", data)
            return {"success": False, "error": "unknown device"}

        domain = entity_id.split(".", 1)[0]
        state = data.get("state") or data.get("action")

        try:
            if state in ("on", "turn_on"):
                await ha_client.call_service(domain, "turn_on", entity_id)
            elif state in ("off", "turn_off"):
                await ha_client.call_service(domain, "turn_off", entity_id)
            else:
                logger.warning("unhandled state=%r for %s, payload=%s", state, entity_id, data)
                return {"success": False, "error": "unhandled state"}
        except Exception:
            logger.exception("failed to call HA service for %s", entity_id)
            return {"success": False, "error": "ha call failed"}

        return {"success": True}

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT"])
    async def catch_all(full_path: str, request: Request):
        body = await request.body()
        logger.info(
            "UNHANDLED callback %s /%s : %s",
            request.method, full_path, body.decode("utf-8", "replace"),
        )
        return {"success": True}

    return app
