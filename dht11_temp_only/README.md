# DHT11 + LDR → Bluetooth → ground-station laptop

UNO Q only reads sensors and sends them. No crop analysis on the board.

## DHT11
| Pin | UNO Q |
|-----|--------|
| S | D2 |
| U | 5V |
| G | GND |

## Photoresistor wiring

```
5V ---- LDR ----+---- A0
                |
             [100kΩ]
                |
               GND
```

Brighter light → higher `light_raw`. Software maps to **`light` 1–100**:
- **1** = dark  
- **50** = room when the app starts  
- **100** = super bright  

If a flashlight makes `light` go *down*, set `INVERT = True` in `python/light_cal.py`.

Stream rate: light ~**20 Hz**; DHT11 refreshes about once per second.

## Run
1. App Lab: import/run this app on the UNO Q (`AgriDrone-UNOQ`)
2. Ground-station laptop:
```bash
pip install bleak
python -m ground_station.bt_sensor_recv
```
