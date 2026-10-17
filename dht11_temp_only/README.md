# UNO Q sensors → laptop

## The one important fact

**App Lab cannot do Bluetooth.** Arduino’s App container has no BlueZ/D-Bus.
That is why you saw `BLE-DOWN` / Errno 2. Sensors still work — use TCP, or run
Bluetooth on the Debian **host**.

```
MCU sensors --Bridge--> App Lab Python --TCP:9123--> laptop     (works now)
                              \
                               --TCP:9124--> host_bt_relay.py --BLE--> laptop
```

---

## Path A — Get data in 2 minutes (TCP / Wi‑Fi)

1. Import this app into App Lab → **Run**
2. Monitor shows `TCP:9123` and a board IP
3. On the laptop (same Wi‑Fi):
```bash
python -m ground_station.bt_sensor_recv --tcp <BOARD-IP>:9123
```

When the laptop connects, Monitor changes from `WAITING-FOR-LAPTOP` to `SENT`.

---

## Path B — Real Bluetooth (host relay)

### 1) PC: open a shell on the board
Follow Arduino: https://docs.arduino.cc/tutorials/uno-q/adb/

```bash
adb devices
adb shell
```

### 2) Board shell: start Bluetooth relay
Copy `python/host_bt_relay.py` onto the board (or clone the repo), then:

```bash
pip3 install --user bless
python3 host_bt_relay.py
```

You want:
```
[host] BLE advertising as AgriDrone-UNOQ
[host] waiting for App Lab on TCP :9124
```

Leave this terminal open.

### 3) App Lab: Run the sensor app
Monitor should change to:
```
BLE-via-host(172.17.0.1:9124)
```

### 4) Laptop:
```bash
pip install bleak
python -m ground_station.bt_sensor_recv
```

---

## Wiring
| Sensor | Pins |
|--------|------|
| DHT11 | S→D2, U→5V, G→GND |
| LDR | 5V—LDR—A0—100k—GND |
