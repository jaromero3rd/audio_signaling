# UNO Q sensors → Bluetooth → other computer

The Arduino only **reads** DHT11 + photoresistor and **sends JSON over Bluetooth**.
Processing happens on the ground-station laptop.

```
[UNO Q]  --Bluetooth-->  [ground-station laptop]
   DHT11 + LDR                bt_sensor_recv.py
```

## 1) On the UNO Q (App Lab)
1. Import `dht11_temp_only_import.zip`
2. Press **Run**
3. Monitor should say it is advertising as **AgriDrone-UNOQ**

## 2) On the other computer (ground station)
Copy/clone this repo, then:

### Option A — Bluetooth LE (easiest)
```bash
pip install bleak
python -m ground_station.bt_sensor_recv
```

You should see:
```
T=24.0C  RH=45%  light=50/100 (room)  raw=612  ok=True
```

### Option B — Classic Bluetooth Serial
1. Pair the UNO Q / AgriDrone-UNOQ in Windows Bluetooth settings  
2. Note the COM port (e.g. COM5)  
3. Run:
```bash
pip install pyserial
python -m ground_station.bt_sensor_recv --com COM5
```

## Wiring
| Sensor | Pins |
|--------|------|
| DHT11 | S→D2, U→5V, G→GND |
| LDR | 5V—LDR—A0—100k—GND |

`light` is 1–100 (50 ≈ room at startup). `INVERT` is in `python/light_cal.py`.
