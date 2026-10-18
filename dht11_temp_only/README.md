# UNO Q sensors → laptop

## The one important fact

**App Lab cannot do Bluetooth.** Arduino’s App container has no BlueZ/D-Bus.
That is why you saw `BLE-DOWN` / Errno 2. Sensors still work — use TCP, or run
Bluetooth on the Debian **host**.

```
MCU --Bridge--> App Lab container --:9124--> Debian host_tcp_relay --:9123--> Mac
```

App Lab cannot POST to the Mac on iPhone hotspot, and cannot expose :9123 on
the board Wi-Fi IP. The host relay is required.

---

## Path A — Host TCP relay (use this)

1. Board USB to the Mac (ADB). Phone hotspot can stay as the board's Wi-Fi.

```bash
adb devices
adb push dht11_temp_only/python/host_tcp_relay.py /tmp/host_tcp_relay.py
adb shell python3 /tmp/host_tcp_relay.py
```

Leave that shell open. You want `App Lab on 0.0.0.0:9124` and `Mac pull on 0.0.0.0:9123`.

2. App Lab → **Run** the DHT11 app. Monitor should show `HOST-172.17.0.1:9124` (not only POST failed).

3. Mac:

```bash
python3 -m ground_station.sensor_recv
```

It scans `172.20.10.x:9123`. If needed, use the board hotspot IP from the App Lab/host:

```bash
python3 -m ground_station.sensor_recv --tcp 172.20.10.5:9123
```

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
