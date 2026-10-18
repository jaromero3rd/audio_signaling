# DHT11 + LDR → Bluetooth (wireless) → laptop

App Lab **cannot** talk to BlueZ. The board host runs a small BLE bridge:

```
MCU sensors → App Lab → TCP :9124 → host_bt_relay.py → BLE AgriDrone-UNOQ → laptop
```

After a **one-time USB setup**, the board can run on **battery** and the laptop
receives over Bluetooth with **no wire**.

---

## One-time board setup (USB to this PC once)

From PowerShell in the repo:

```powershell
$adb = "$env:LOCALAPPDATA\AppLab\a15\data\packages\arduino\tools\adb\32.0.0\adb.exe"
& $adb devices
& $adb push dht11_temp_only\python\host_bt_relay.py /home/arduino/host_bt_relay.py
& $adb push dht11_temp_only\python\start_bt_relay.sh /home/arduino/start_bt_relay.sh
& $adb push dht11_temp_only\python\agridrone-bt-relay.service /home/arduino/agridrone-bt-relay.service
& $adb push dht11_temp_only\python\install_bt_relay.sh /home/arduino/install_bt_relay.sh
& $adb shell "sed -i 's/\r$//' /home/arduino/*.sh; chmod +x /home/arduino/install_bt_relay.sh /home/arduino/start_bt_relay.sh; bash /home/arduino/install_bt_relay.sh"
```

You want the relay advertising: `BLE advertising as AgriDrone-UNOQ`

After install, a **board power-cycle** restarts the relay and it **re-advertises**
automatically. The laptop must keep `bt_sensor_recv` running (it reconnects).
Do **not** Windows-pair AgriDrone-UNOQ — BLE GATT reconnect is enough.

## Every session (wireless)

1. Power the UNO Q (battery or USB power — USB to the **laptop is not required**).
2. App Lab → import `dht11_temp_only_import.zip` → **Run**  
   Monitor: `BLE-BRIDGE(...:9124)` then `SENT`
3. On the laptop (Bluetooth ON — **do not** pair AgriDrone-UNOQ in Windows Settings):

```powershell
cd C:\Users\maxim\audio_signaling
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m ground_station.bt_sensor_recv
```

Leave that laptop command running across board power-cycles — it will reconnect
when AgriDrone-UNOQ advertises again.

Dismiss Windows “Add a device” popups. **Do not pair** AgriDrone-UNOQ in Settings
(Windows often treats it as headphones and then GATT fails). Remove it if listed.

Success looks like: `T=22.3C  RH=40%  light=…`

If BLE connect fails on Windows (`Unreachable` / no GATT), remove the device in
Bluetooth settings, toggle Bluetooth off/on, and run `.\recv_unoq_ble.ps1` again.
USB debug (below) still works while you sort that out.

---

## Optional USB debug (when BLE is flaky)

```powershell
$adb = "$env:LOCALAPPDATA\AppLab\a15\data\packages\arduino\tools\adb\32.0.0\adb.exe"
& $adb forward tcp:9125 tcp:9125
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m ground_station.bt_sensor_recv --tcp 127.0.0.1:9125
```

## Wi‑Fi fallback

```powershell
python -m ground_station.bt_sensor_recv --tcp <BOARD-IP>:9123
```
