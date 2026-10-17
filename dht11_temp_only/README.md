# DHT11 + LDR → Bluetooth → ground-station computer

The UNO Q only **reads** sensors and **sends** raw values.  
No crop-health processing on the board.

**Important:** the receiver is the **ground-station laptop** (a different machine
from whatever you use to flash/dev). That PC must have Bluetooth on and run
`bt_sensor_recv` while near the UNO Q.

```
[UNO Q + DHT11 + LDR] --BLE--> [ground-station laptop]
                                      ├─ sensor log / crop analysis (later)
                                      ├─ Tello / AprilTag / Grok Voice
                                      └─ NOT required to be the App Lab PC
```

## Wiring
| Sensor | Pins |
|--------|------|
| DHT11 | S→D2, U→5V, G→GND |
| LDR | 5V—LDR—A0—10k—GND |

## 1) Dev / App Lab PC (can be this machine)
1. Import `dht11_temp_only_import.zip` in App Lab  
2. Run on the UNO Q (advertises **AgriDrone-UNOQ**)

## 2) Ground-station laptop (the other computer)
```bash
cd audio_signaling
pip install bleak
python -m ground_station.bt_sensor_recv
```

You should see lines like:
```
T=24.0C  RH=45%  light_raw=612  ok=True
```

Requirements on that laptop: Bluetooth enabled, close enough to the UNO Q,
same repo (or at least `ground_station/bt_sensor_recv.py`).
