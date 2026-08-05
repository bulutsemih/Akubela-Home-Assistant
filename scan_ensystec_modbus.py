"""
Сканер регистров Modbus RTU - для реверс-инжиниринга карты регистров
Ensystec Leak Protect, раз публичной документации по Modbus для него
нет (в отличие от Zigbee-модуля, для которого есть подробная
инструкция).

ВАЖНО: нужен ОТДЕЛЬНЫЙ USB-RS485 адаптер, подключённый напрямую к
компьютеру, где это запускается - Modbus RTU не терпит двух мастеров
на одной шине одновременно, а Akubela уже мастер для своей шины.
Подключите Ensystec к этому адаптеру отдельно (не туда же, куда
подключена Akubela), либо временно отключите Akubela от шины на время
сканирования.

Требует: pip install pymodbus pyserial --break-system-packages

Использование:
    python scan_ensystec_modbus.py COM3 --slave 1
    python scan_ensystec_modbus.py /dev/ttyUSB0 --slave 1 --baud 9600

Запустите ДВАЖДЫ - один раз в спокойном состоянии, второй раз намочив
датчик протечки (или подвигав кран) - и сравните вывод. Регистры,
которые изменились между двумя запусками - и есть искомые.
"""

import argparse
import sys

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:
    print("Нужна библиотека pymodbus: pip install pymodbus --break-system-packages")
    sys.exit(1)

# Диапазон сканирования - с запасом, большинство Modbus-устройств
# используют первые несколько десятков регистров каждого типа.
SCAN_COUNT = 64


def _read(read_fn, address, count, slave):
    """
    pymodbus меняла сигнатуру чтения между версиями - в 3.14 (текущей)
    count оказался именованным-только параметром, из-за чего позиционный
    вызов падал с TypeError ещё до того, как успевали различиться
    slave= и unit=. Пробуем несколько вариантов именованных аргументов,
    address всегда передаём позиционно - это стабильно во всех версиях.
    """
    attempts = (
        {"count": count, "slave": slave},
        {"count": count, "unit": slave},
        {"count": count},
    )
    last_err = None
    for kwargs in attempts:
        try:
            return read_fn(address, **kwargs)
        except TypeError as e:
            last_err = e
            continue
    raise RuntimeError(
        f"не удалось вызвать чтение ни одним известным способом "
        f"(последняя ошибка: {last_err}) - проверьте версию pymodbus"
    )


def scan_block(client, slave, label, read_fn, count=SCAN_COUNT):
    print(f"\n--- {label} (0..{count - 1}) ---")
    values = []
    # Читаем блоками по 16 - некоторые устройства не любят большие
    # запросы за один раз и просто не отвечают.
    for start in range(0, count, 16):
        chunk = min(16, count - start)
        try:
            resp = _read(read_fn, start, chunk, slave)
            if resp.isError():
                print(f"  [{start}-{start + chunk - 1}] ошибка: {resp}")
                values.extend([None] * chunk)
                continue
            block = getattr(resp, "bits", None) or getattr(resp, "registers", None)
            values.extend(block)
        except Exception as e:
            print(f"  [{start}-{start + chunk - 1}] исключение: {e!r}")
            values.extend([None] * chunk)

    for i, v in enumerate(values):
        if v is not None and v != 0:
            print(f"  [{i}] = {v!r}  <- ненулевое, обратите внимание")
    print(f"  (всего прочитано {len([v for v in values if v is not None])}/{count}, "
          f"ненулевых: {len([v for v in values if v])})")
    return values


def main():
    parser = argparse.ArgumentParser(description="Сканер регистров Modbus RTU для Ensystec Leak Protect")
    parser.add_argument("port", help="Серийный порт адаптера, например COM6 или /dev/ttyUSB0")
    # Параметры из официального документа Ensystec:
    # Slave ID: 247 (0xF7), скорость: 9600, биты данных: 8,
    # бит чётности: none, стоповых битов: 2, CRC: A001H (стандартный RTU).
    parser.add_argument("--slave", type=int, default=247,
                        help="Modbus slave ID (по умолчанию 247 = 0xF7 как у Ensystec)")
    parser.add_argument("--baud", type=int, default=9600, help="Скорость (по умолчанию 9600)")
    parser.add_argument("--stopbits", type=int, default=2,
                        help="Стоповые биты (по умолчанию 2 - требование Ensystec)")
    parser.add_argument("--count", type=int, default=SCAN_COUNT,
                        help="Сколько регистров каждого типа сканировать")
    args = parser.parse_args()

    client = ModbusSerialClient(
        port=args.port,
        baudrate=args.baud,
        parity="N",
        stopbits=args.stopbits,
        bytesize=8,
        timeout=2,
    )
    if not client.connect():
        print(f"Не удалось открыть порт {args.port}")
        sys.exit(1)

    print(f"Подключен к {args.port}, slave={args.slave}, baud={args.baud}, stopbits={args.stopbits}")
    print("Сканирую регистры Modbus Ensystec Leak Protect...")

    # Согласно документу - поддерживаются:
    # 01H - READ_SINGLE_COIL (только 1 бит за раз!)
    # 03H - READ_HOLDING_REGISTERS
    # 05H - PRESET_SINGLE_COIL (запись)
    # 06H - PRESET_ONE_REGISTERS (запись одного holding)
    # Coils читаются ТОЛЬКО по одному - это отдельная особенность
    # (документ прямо говорит: "доступно чтение состояния только 1 бита")

    print("\n--- Holding Registers 03H (числовые значения, конфиг и данные) ---")
    holding = []
    for i in range(args.count):
        try:
            resp = _read(client.read_holding_registers, i, 1, args.slave)
            if resp.isError():
                val = None
            else:
                val = resp.registers[0]
        except Exception:
            val = None
        holding.append(val)
    for i, v in enumerate(holding):
        if v is not None:
            mark = "  <- НЕНУЛЕВОЕ" if v != 0 else ""
            print(f"  HR[{i}] = {v} (0x{v:04X}){mark}")
    print(f"  (отвечающих: {len([v for v in holding if v is not None])}/{args.count})")

    print("\n--- Coils 01H (биты состояния, читаем по одному - требование Ensystec) ---")
    coils = []
    for i in range(min(32, args.count)):  # обычно не больше 32 coils
        try:
            resp = _read(client.read_coils, i, 1, args.slave)
            if resp.isError():
                val = None
            else:
                val = resp.bits[0]
        except Exception:
            val = None
        coils.append(val)
    for i, v in enumerate(coils):
        if v is not None:
            mark = "  <- УСТАНОВЛЕН (1)" if v else ""
            print(f"  Coil[{i}] = {int(v) if v is not None else '?'}{mark}")
    print(f"  (отвечающих: {len([v for v in coils if v is not None])}/{min(32, args.count)})")

    client.close()
    print("\nГотово. Запустите ещё раз намочив датчик / переключив кран,")
    print("и сравните: какие значения изменились - те и есть нужные регистры.")


if __name__ == "__main__":
    main()
