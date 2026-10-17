# DHT11 ↔ Arduino UNO Q wiring

The sketch reads **everything the DHT11 provides**:

| Value | Field | Notes |
|-------|--------|--------|
| Temperature °C | `temp_c` | `dht.readTemperature()` |
| Temperature °F | `temp_f` | `dht.readTemperature(true)` |
| Relative humidity % | `humidity_pct` | `dht.readHumidity()` |
| Heat index °C | `heat_index_c` | derived from T + RH |

Those values are printed on the MCU Monitor, sent over Bridge to Linux, posted to
`/telemetry`, and (if offline) written to the SD card.

## Pins used by this project

| Function | UNO Q pin (silk screen) | Sketch constant |
|----------|-------------------------|-----------------|
| DHT11 DATA | **D2** | `DHT_PIN` (= 2) |
| Photoresistor | **A0** | `LDR_PIN` |
| microSD CS | **D10** | `SD_CS_PIN` |
| Power for DHT11 | **5V** | — |
| Ground | **GND** | — |

UNO Q keeps the classic UNO header layout. Digital pins on the right/left
headers include **D0–D13**; power pins include **3.3V**, **5V**, **GND**, **VIN**.

Full pinout PDF: https://docs.arduino.cc/resources/pinouts/ABX00162-full-pinout.pdf

## How to connect the DHT11

### 3-pin DHT11 **module** (most common — has a board + built-in resistor)

| DHT11 module wire | Connect to UNO Q |
|-------------------|------------------|
| **VCC** / + | **5V** |
| **GND** / − | **GND** |
| **DATA** / OUT / S / DOUT | **D2** |

No extra resistor needed on most modules.

### 4-pin bare DHT11 **sensor**

| DHT11 pin | Connect to UNO Q |
|-----------|------------------|
| **VCC** | **5V** |
| **DATA** | **D2** |
| **NC** | leave unconnected |
| **GND** | **GND** |

Add a **4.7kΩ–10kΩ** pull-up resistor between **DATA** and **VCC**.

```
UNO Q 5V  ----+---- DHT11 VCC
              |
             [10kΩ]
              |
UNO Q D2  ----+---- DHT11 DATA
UNO Q GND --------- DHT11 GND
```

## Tips

- Wait ~2 s after power-on (the sketch already delays).
- Do not read faster than once per second (sketch uses 2.5 s).
- If Monitor prints `DHT11 read failed`, re-check **5V / GND / D2** and the pull-up.
- Confirm readings in App Lab Monitor, or on the laptop at `GET http://…:8765/latest`.
