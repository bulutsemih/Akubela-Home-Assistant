# Akubela PS51/HyPanel ↔ Home Assistant Bridge

Двусторонний мост между Home Assistant и панелью умного дома Akubela
PS51 (HyPanel). Устройства, сцены и родные устройства панели (реле,
замки/домофон, датчики) синхронизируются в обе стороны в реальном
времени, полностью локально, без облака.

**Документация:**
- `README.md`
- [`EXPLAIN_SIMPLY.md`](./EXPLAIN_SIMPLY.md) — что такое WebSocket, MQTT, API, как устройство едет из HA на панель и обратно. Начните отсюда, если не писали такое раньше.
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — подробное техническое описание для инженеров: протокол, потоки данных, защитные механизмы, что подтверждено вживую, а что нет.
- [`DEVICE_GUIDE.md`](./DEVICE_GUIDE.md) — практическая памятка: как добавить тип устройства, как калибровать виджет, troubleshooting.

## Что делает бридж

| Направление | Что происходит | Статус |
|---|---|---|
| HA → Akubela (устройства) | `light`/`switch`/`climate`/`cover`/`lock`/`input_boolean` из HA появляются как виджеты на панели | ✅ работает |
| HA → Akubela (live) | изменение состояния/яркости/температуры/позиции в HA сразу видно на панели | ✅ работает |
| Akubela → HA (live) | нажатие на виджет панели сразу видно в HA | ✅ работает |
| Akubela → HA (родные датчики) | температура/влажность/освещённость и т.д. видны в HA через MQTT Discovery | ✅ работает |
| Akubela → HA (родные реле/выключатели) | например Modbus RTU реле — видны и управляются из HA | ✅ работает |
| Akubela → HA (родной замок/домофон) | открытие двери из HA, статус замка в HA | ✅ работает (см. ARCHITECTURE.md — команда unlock подтверждена на реальном устройстве) |
| Akubela → HA (камера домофона) | видео/снимок с камеры в HA | ❌ `/api/camera_proxy` падает с 500 на стороне самой панели — подтверждённый баг прошивки, не нашего кода |
| HA → Akubela (сцены) | HA-сцены становятся кнопками на панели | ✅ работает |
| Akubela → HA (сцены) | нажатие сцены на панели запускает сцену в HA | 🔶 экспериментально |
| HA → Akubela (`sensor.*`) | датчики HA как отдельные виджеты на панели | ❌ панель не создаёт устройства категории `Sensor` через API вообще (подтверждено) |

## Быстрый запуск

```bash
git clone <этот репозиторий>
cd akubela_bridge
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS
pip install -r requirements.txt

cp .env.example .env   # заполните токены, см. ниже
python test_ha_auth.py       # проверка связи с HA
python test_akubela_auth.py  # проверка связи с панелью
python test_connect.py       # один проход синхронизации устройств
python test_roundtrip.py     # 90 секунд живого двустороннего теста

python main.py                # боевой запуск (держит процесс)
```

Для быстрой демонстрации всего разом (виртуальные тестовые устройства +
сам бридж одной командой, логи с префиксами в одной консоли):
```bash
python run_demo.py            # без камеры домофона
python run_demo.py --camera   # + пробовать камеру (см. таблицу выше)
```

### Токены

- **HA_ACCESS_TOKEN** — HA → Профиль → Безопасность → Долгосрочные
  токены доступа → Создать. Формат JWT (`eyJhbGci...`).
- **AKUBELA_ACCESS_TOKEN** — `http://<IP_панели>` в браузере → залогиниться →
  DevTools → Console:
  ```js
  JSON.parse(localStorage.getItem("AKUBELA_USER-TOKEN")).value
  ```
  Токен привязан к сессии панели и может протухать при её перезагрузке —
  если авторизация вдруг перестала проходить, возьмите новый.

.

## Структура проекта

```
Транспорт:
  ws_base.py               общий надёжный WS-клиент (reader-таск, pub/sub событий, автореконнект)
  ha_client.py              клиент Home Assistant
  akubela_client.py         клиент панели Akubela

Устройства (HA -> Akubela, наши виртуальные):
  device_registry.py        HA entity -> device_type/feature/device_config, персистентный store
  sync_service.py            HA -> Akubela: регистрация устройств (разовая/периодическая)
  state_forwarder.py         HA -> Akubela: живая пересылка изменений
  control_bridge.py          Akubela -> HA: живая пересылка изменений
  loop_guard.py              защита от эхо-петель (дискретные состояния: on/off, режимы)
  rate_limited_sender.py     debounce для непрерывных величин (яркость/позиция/температура) -
                             не теряет финальное значение при быстрых изменениях

Сцены:
  scene_registry.py          HA scene -> ak_api/scene/add маппинг
  scene_sync_service.py      HA -> Akubela: регистрация сцен
  scene_control_bridge.py    Akubela -> HA: активация сцены (экспериментально)

Родные устройства панели (Akubela -> HA, не наши, физические):
  native_sensor_bridge.py    датчики (температура/влажность/освещённость и т.п.) -> HA
  native_switch_bridge.py    реле/переключатели (например Modbus RTU) -> HA, двусторонне
  native_lock_bridge.py      замок/домофон -> HA, двусторонне (открытие обратно в Akubela)
  native_camera_bridge.py    снимок с камеры домофона -> HA (выключено по умолчанию, см. таблицу выше)

Резерв/отладка:
  control_server.py          HTTP-приёмник callback'ов (не основной канал)

Точка входа:
  main.py                    поднимает всё вместе одним процессом
  run_demo.py                main.py + виртуальные демо-устройства одной командой

Тесты:
  test_ha_auth.py, test_akubela_auth.py   изолированная проверка каждой стороны
  test_connect.py                          один проход синхронизации устройств
  test_roundtrip.py                        живой двусторонний тест с логом
  test_mqtt_native.py                      изолированный тест родных датчиков
  test_brightness_debug.py                 перебор имён параметра яркости (диагностика)
  test_id_ext.py                           проверка ak_api/id_ext/next
  test_doorphone_unlock.py                 калибровка команды разблокировки домофона

Демо-устройства (виртуальные, через MQTT):
  virtual_mqtt_devices.py                  light + climate для базовых тестов
  demo_devices.py                          cover + AC + тёплый пол + датчик воздуха

Калибровка и обслуживание:
  probe_light_features.py                  подбор feature для Light (0-15)
  probe_cover_features.py                  подбор feature для Cover (0-9)
  probe_air_quality.py                     проверка device_type для датчиков (Sensor не работает)
  probe_native_entities.py                 дамп всех родных сущностей панели
  cleanup_orphans.py                       найти/удалить "осиротевшие" устройства на Akubela
  cleanup_mqtt_ghosts.py                   найти/удалить осиротевшие retained MQTT-конфиги в HA
  force_resync.py                          сбросить устройство к дефолту кода
```

Подробности — в [`ARCHITECTURE.md`](./ARCHITECTURE.md) и
[`DEVICE_GUIDE.md`](./DEVICE_GUIDE.md).

## Лицензия

MIT — см. [`LICENSE`](./LICENSE).
