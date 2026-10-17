# AgriDrone Field Station (Arduino UNO Q)

## What belongs where

| Concern | Where it runs |
|---------|----------------|
| Farmer voice commands → Grok Voice → drone control | **Laptop** (`ground_station/voice_grok.py`) |
| Field sensors (DHT11, light) + SD offline log | **UNO Q MCU** |
| Mic capture + transfer to ground computer | **UNO Q Linux** (`mic_capture` / `mic_transfer`) |
| Return-to-Wi-Fi when link is lost | **UNO Q Linux** autonomy agent |

The UNO Q does **not** run Grok Voice. It only records microphone PCM and posts it to the ground station (`POST /mic`).

## Hardware (UNO Q)

| Part | Wiring |
|------|--------|
| DHT11 | **VCC→5V**, **GND→GND**, **DATA→D2** (see [WIRING_DHT11.md](WIRING_DHT11.md)) |
| Photoresistor | Divider → **A0** |
| Microphone | USB / board input visible to Linux `sounddevice` |
| microSD | CS **D10**, MOSI **D11**, MISO **D12**, SCK **D13** |

DHT11 fields relayed: `temp_c`, `temp_f`, `humidity_pct`, `heat_index_c`.

## Data paths

```
DHT11 + LDR  --Bridge-->  Linux  --POST /telemetry-->  ground laptop
Mic (sounddevice) -------- Linux  --POST /mic------->  ground laptop (raw f32le file)
```

If Wi-Fi drops: env rows go to SD; mic bytes append to local `data/mic_offline.f32le` until the link returns.

## Setup (phone hotspot LAN)

The Mac reaches the internet over **USB Personal Hotspot**. This board joins the **same hotspot over Wi-Fi**. Both land on `172.20.10.0/28`, so the board can POST to the Mac without sharing the Tello radio.

1. Phone: enable Personal Hotspot, USB to the Mac, Wi-Fi for this board. Stay in range of the phone.
2. Laptop: `python -m drone_interface.field_links --apply` then:

```bash
python -m ground_station.sensor_recv --host 0.0.0.0 --port 8765
```

3. Copy `python/config.example.json` → `python/config.json`. Set `wifi.ssid` (and password, or `PHONE_HOTSPOT_PASSWORD`). Leave `ground_station_url` as `"auto"` to scan `172.20.10.x:8765`.
4. App Lab → open this folder → Run on the UNO Q.

Farmer voice control (separate process on the same laptop):

```bash
python demo.py   # Grok Voice + Tello mission
```
