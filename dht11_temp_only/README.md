# UNO Q sensors → Bluetooth → other computer

The Arduino only **reads** DHT11 + photoresistor and **sends JSON over Bluetooth**.
Processing happens on the ground-station laptop.

```
[UNO Q]  --Bluetooth-->  [ground-station laptop]
   DHT11 + LDR                bt_sensor_recv.py
```

## 1) On the UNO Q (App Lab)
1. Import / re-import `dht11_temp_only` (or the zip)
2. Press **Run**
3. Monitor should show lines like:
   - `[bt] Powered: yes`
   - `[bt] Discoverable: yes`
   - `[bt-ble] advertising as AgriDrone-UNOQ`

If Powered is **no**, Bluetooth on the board is off — see Fix below.

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

### If the computer cannot see the UNO Q
```bash
python -m ground_station.bt_sensor_recv --scan
```
That lists every nearby BLE device. Look for `AgriDrone-UNOQ` or `Arduino`.

**Fix checklist (most common):**
1. App Lab app is **Running** (not just uploaded)
2. On the UNO Q Monitor: advertising line appears
3. Ground-station laptop: **Bluetooth ON**
4. Stay within a few meters; close other apps using Bluetooth
5. On Windows: Settings → Bluetooth → make sure it can discover new devices
6. Still invisible → use classic serial (Option B)

### Option B — Classic Bluetooth Serial (Windows)
1. Windows Settings → Bluetooth → **Add device**
2. Pair **AgriDrone-UNOQ** / **Arduino UNO Q**
3. Device Manager → Ports (COM & LPT) → note e.g. `COM5`
4. Run:
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
