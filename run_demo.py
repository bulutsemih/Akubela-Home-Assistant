"""
Единая точка запуска для демонстрации: поднимает три процесса разом -
виртуальные MQTT-устройства (свет, климат), демо-устройства (шторы,
кондиционер, тёплый пол, датчик воздуха) и сам бридж (main.py) -
и выводит их логи в одну консоль с префиксами, чтобы не держать
открытыми три отдельных окна терминала.

MQTT-устройства запускаются первыми и с небольшой задержкой перед
main.py - чтобы к моменту первого full_sync они уже появились в HA
(иначе бридж на первом проходе их просто не увидит).

Замок домофона (native_lock_bridge) поднимается в main.py всегда,
отдельно включать не нужно - в логе будет виден как [Bridge].

Камера домофона (native_camera_bridge) ВЫКЛЮЧЕНА по умолчанию -
подтверждено, что /api/camera_proxy падает с 500 на стороне самой
панели (см. ARCHITECTURE.md/DEVICE_GUIDE.md). Включайте флагом
--camera только если проверяете это заново после обновления прошивки
или нашли рабочий путь получения снимка.

Запуск:
    python run_demo.py              # без камеры (по умолчанию)
    python run_demo.py --camera     # + пробовать камеру домофона

Остановка: Ctrl+C - останавливает все три процесса разом.
"""

import os
import subprocess
import sys
import threading
import time

CAMERA_ENABLED = "--camera" in sys.argv

SCRIPTS = [
    ("MQTT-devices", "virtual_mqtt_devices.py", 0),
    ("Demo-devices", "demo_devices.py", 0),
    ("Bridge", "main.py", 4),  # даём MQTT-устройствам 4 секунды на публикацию в HA
]

_processes = []


def stream_output(name, proc):
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            print(f"[{name}] {line.rstrip()}")
    except Exception:
        pass


def start(name, script, delay):
    if delay:
        time.sleep(delay)
    print(f"[run_demo] запускаю {name} ({script})...")

    env = os.environ.copy()
    if script == "main.py":
        env["NATIVE_CAMERA_ENABLED"] = "true" if CAMERA_ENABLED else "false"

    proc = subprocess.Popen(
        [sys.executable, "-u", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    _processes.append((name, proc))
    t = threading.Thread(target=stream_output, args=(name, proc), daemon=True)
    t.start()


def main():
    print("=" * 70)
    print("run_demo.py - запускаю всё для демонстрации разом")
    print("=" * 70)
    print(f"Замок домофона (lock): всегда включён в Bridge")
    print(f"Камера домофона (camera): {'включена (--camera)' if CAMERA_ENABLED else 'выключена (подтверждено, что не работает - см. --camera для повторной проверки)'}")
    print()

    threads = []
    for name, script, delay in SCRIPTS:
        t = threading.Thread(target=start, args=(name, script, delay), daemon=True)
        t.start()
        threads.append(t)

    # ждём, пока все процессы стартуют (с учётом задержек выше)
    time.sleep(max(d for _, _, d in SCRIPTS) + 2)

    print()
    print("=" * 70)
    print("Всё запущено. Логи всех трёх процессов идут ниже с префиксами")
    print("[MQTT-devices] / [Demo-devices] / [Bridge]. Ctrl+C - остановить всё.")
    print("=" * 70)
    print()

    try:
        while True:
            time.sleep(1)
            for name, proc in list(_processes):
                code = proc.poll()
                if code is not None:
                    print(f"[run_demo] ВНИМАНИЕ: {name} завершился (код {code}) - "
                          f"смотрите лог этого процесса выше на предмет ошибки")
                    _processes.remove((name, proc))
    except KeyboardInterrupt:
        print("\n[run_demo] останавливаю все процессы...")
        for name, proc in _processes:
            proc.terminate()
        time.sleep(2)
        for name, proc in _processes:
            if proc.poll() is None:
                proc.kill()
        print("[run_demo] готово.")


if __name__ == "__main__":
    main()
