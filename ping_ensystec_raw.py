"""
Отправляет один сырой Modbus-запрос напрямую через pyserial, минуя
pymodbus полностью. Помогает понять: устройство физически отвечает
или нет, независимо от того, правильно ли pymodbus интерпретирует ответ.

Если этот скрипт тоже не видит ответа - проблема точно физическая
(неверная полярность A/B, конкурирующий мастер на шине, отсутствие
питания контроллера и т.д.).

Если этот скрипт ВИДИТ байты, а scan_ensystec_modbus.py - нет, значит
проблема в pymodbus (неверная интерпретация ответа).

Запуск:
    python ping_ensystec_raw.py COM6
"""

import sys
import struct
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM6"
SLAVE = 0xF7   # 247 - из документа Ensystec
BAUD = 9600
STOPBITS = 2


def crc16(data: bytes) -> bytes:
    """Modbus RTU CRC16 (полином A001H)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)


def make_request(slave, func, reg_addr, count):
    pdu = struct.pack(">BBHH", slave, func, reg_addr, count)
    return pdu + crc16(pdu)


def main():
    print(f"Открываю {PORT} (9600 baud, 8N2)...")
    try:
        ser = serial.Serial(
            port=PORT,
            baudrate=BAUD,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=STOPBITS,
            timeout=2,
        )
    except Exception as e:
        print(f"Не удалось открыть порт: {e}")
        sys.exit(1)

    tests = [
        ("Read Holding Register #0 (func 03h, должен вернуть 2 байта данных = 7 байт итого)",
         make_request(SLAVE, 0x03, 0, 1)),
        ("Read Coil #0 (func 01h, должен вернуть 1 байт данных = 6 байт итого)",
         make_request(SLAVE, 0x01, 0, 1)),
    ]

    for label, request in tests:
        ser.reset_input_buffer()
        print(f"\n--- {label} ---")
        print(f"  Отправляю ({len(request)} байт): {request.hex(' ').upper()}")
        ser.write(request)
        ser.flush()  # убедились, что всё отправлено

        # Полудуплексные USB-RS485 адаптеры "слышат" собственный запрос
        # пока его отправляют (TX эхо) - нужно дать адаптеру переключиться
        # из режима TX в RX и сбросить буфер, иначе первые байты ответа
        # окажутся нашим же запросом, а не ответом устройства.
        import time
        time.sleep(0.1)  # ~10мс на переключение TX->RX + небольшой запас
        ser.reset_input_buffer()  # выбрасываем эхо нашего запроса

        # Читаем всё, что придёт за 3 секунды - с паузами между чтениями,
        # на случай если устройство отвечает с задержкой или по кускам.
        import time
        collected = b""
        deadline = time.time() + 3.0
        while time.time() < deadline:
            chunk = ser.read(64)
            if chunk:
                collected += chunk
            elif collected:
                break  # уже что-то получили и поток иссяк - достаточно

        if collected:
            print(f"  Получил {len(collected)} байт: {collected.hex(' ').upper()}")
            print(f"  По байтам: {[f'0x{b:02X}' for b in collected]}")
            if len(collected) >= 3 and collected[1] & 0x80:
                print(f"  -> Ошибка Modbus: function code {collected[1]:02X}h, "
                      f"exception {collected[2]}")
            elif len(collected) >= 5:
                print(f"  -> Полный ответ. Device ID={collected[0]:#04x}, "
                      f"Func={collected[1]:#04x}, данные: {collected[2:-2].hex(' ').upper()}")
            else:
                print(f"  -> Неполный ответ ({len(collected)} байт вместо ожидаемых 5+). "
                      f"Возможно устройство шлёт кастомный формат или ещё не успело "
                      f"ответить полностью - попробуйте --timeout 5")
        else:
            print(f"  -> Нет ответа (таймаут 3с)")

    ser.close()
    print("\nГотово.")
    print("Если оба теста 'нет ответа' - проблема физическая:")
    print("  1. Попробуйте поменять A и B местами")
    print("  2. Убедитесь, что Akubela не подключена к тем же клеммам одновременно")
    print("  3. Проверьте питание контроллера Ensystec")


if __name__ == "__main__":
    main()
