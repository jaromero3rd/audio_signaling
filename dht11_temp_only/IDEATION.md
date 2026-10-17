# Crop observation ideation (UNO Q)

## What we measure now

| Sensor | Pin | Signal | Crop meaning |
|--------|-----|--------|--------------|
| **DHT11** | D2 | Temp °C + RH % | Heat stress, dry air, mold risk when RH is huge |
| **Photoresistor** | A0 | Sunlight proxy 0–1 | Photosynthesis energy vs harsh drying sun |

## How we decide “are crops healthy?”

Crops “dry out” when **water leaving the plant** (hot, dry, windy, intense sun)
beats **water available** (soil moisture — not measured yet — and air humidity).

On-device logic (`crop_health.py`) combines:

1. **Dry-out risk** — low RH + high temp (+ intense light) → higher risk  
2. **Light adequacy** — too dark (weak growth) vs good band vs harsh sun  
3. **Status** — `healthy` → `watch` → `stress` → `critical`  
4. **Actions** — human-readable next steps for the survey

This is the same *shape* of reasoning an UNO Q **AI agent / local LLM brick**
would do; today it is a transparent score so you can trust and demo it. Later
you can replace `analyze()` with App Lab **Agent Mode** or an Edge Impulse model
trained on labeled field days.

## Arduino UNO Q AI angle (what to add next)

| Capability | Use for crops |
|------------|----------------|
| **Rules + score (now)** | Fast, offline, explainable dry-out alerts |
| **App Lab Agent / local LLM** | Turn readings into spoken/text advice for the farmer |
| **Edge Impulse / vision brick** | Leaf wilt, yellowing, pest spots from a camera |
| **Object detection** | Find crop rows / bare soil / irrigation equipment |
| **Time-series on device** | Detect multi-hour drying trends, not single snapshots |

Farmer **voice commands** stay on the laptop (Grok Voice). The UNO Q’s job is
**sense + local interpret + relay**.

## Drone onboard camera → Arduino UNO Q

The flying camera (Tello) already streams to the **laptop** in this repo
(`drone_interface/tello_client.py`, YOLO/AprilTag/Grok vision). The UNO Q should
**not** ingest raw video over Wi‑Fi (too heavy). Instead:

```
Tello camera
    → laptop (detect wilt / wheat / yellowing)
    → POST small JSON to UNO Q :8766/vision
    → UNO Q fuses vision + DHT11 + LDR → crop status
```

What the UNO Q receives (example):

```json
{"label":"wilt","confidence":0.81,"wilt":true,"yellowing":false}
```

Helper on the laptop:

```bash
python -m ground_station.vision_to_unoq --host <UNO_Q_IP> --label wilt --confidence 0.81 --wilt
```

Optional later: a **MIPI camera on the UNO Q itself** for a ground/tripod view —
separate from the drone cam.

## Other sensors for sustainable crop observation

Priority for *this* hack / AgriDrone demo:

| Priority | Sensor | Why it matters |
|----------|--------|----------------|
| **P0** | **Soil moisture** (capacitive) | True dry-out signal; air RH alone can lie |
| **P0** | **DHT11 / better DHT22 or SHT45** | Air climate (you have DHT11) |
| **P0** | **Photoresistor or lux / UV** | Sunlight & UV stress |
| **P1** | **Soil temperature** | Root-zone stress, germination |
| **P1** | **Leaf wetness** | Disease / fungal risk after dew or rain |
| **P1** | **Camera (MIPI on UNO Q)** | Visual health, weeds, stand count |
| **P2** | **Rain gauge / tipping bucket** | Water budget |
| **P2** | **Wind speed** | Evaporation & spray drift |
| **P2** | **CO₂** | Greenhouse / canopy activity |
| **P2** | **pH / EC / NPK soil probes** | Nutrient sustainability, fertilizer precision |
| **P3** | **Multispectral / NDVI proxy** | Biomass & nitrogen stress at scale |

### Sustainability framing

- **Water** — soil moisture + dry-out score → irrigate only when needed  
- **Chemicals** — leaf wetness + humidity → spray only when disease risk is real  
- **Energy** — schedule drone flights in good light, avoid useless midday heat runs  
- **Soil health** — pH/EC over the season, not just one photo  

## Suggested next hardware step

1. Keep DHT11 + LDR (this app).  
2. Add **capacitive soil moisture** on another analog pin (e.g. A1).  
3. Fold soil % into `dry_out_risk` (soil dry + air dry + hard sun = critical).  
4. Optional: UNO Q camera brick for wilt confirmation when stress_score is high.
