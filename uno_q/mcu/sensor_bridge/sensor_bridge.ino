/*
 * AgriDrone — Uno Q MCU sketch (STM32 side)
 *
 * Roles:
 *   - Read temp / humidity (BME280 I2C preferred; DHT22 optional fallback)
 *   - Expose samples to Linux via Arduino Bridge RPC
 *   - Append JSON lines to SPI microSD when Linux asks (offline failover)
 *   - Drive a status LED: boot / online / offline / error
 *
 * VERIFY ON HARDWARE (hackathon placeholders):
 *   - I2C pins for BME280 (Uno Q defaults usually Wire)
 *   - BME280 address 0x76 vs 0x77
 *   - SPI CS pin for microSD module (SD_CS below)
 *   - LED pin (LED_STATUS); built-in LED may differ on Uno Q
 *   - Bridge RPC API names must match linux/uno_q_linux/bridge_rpc.py
 *
 * Libraries (install in IDE / App Lab as needed):
 *   - Arduino_RouterBridge (Uno Q) or board Bridge package
 *   - Adafruit BME280 + Adafruit Unified Sensor
 *   - SD (SPI)
 *   - Optional: DHT sensor library if using DHT22
 */

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_BME280.h>

// ---- Pin / bus assumptions (CONFIRM) ----
static const int SD_CS = 10;          // CONFIRM: microSD CS
static const int LED_STATUS = LED_BUILTIN;  // CONFIRM
static const uint8_t BME_ADDR = 0x76; // CONFIRM: 0x76 or 0x77

// Optional DHT fallback (set USE_DHT 1 and wire DATA_PIN)
#define USE_DHT 0
#if USE_DHT
#include <DHT.h>
static const int DHT_PIN = 2;         // CONFIRM
static const int DHT_TYPE = DHT22;
DHT dht(DHT_PIN, DHT_TYPE);
#endif

Adafruit_BME280 bme;
bool g_bme_ok = false;
bool g_sd_ok = false;

enum LedMode : uint8_t { LED_BOOT = 0, LED_ONLINE = 1, LED_OFFLINE = 2, LED_ERROR = 3 };
volatile uint8_t g_led_mode = LED_BOOT;

struct Sample {
  float temperature_c;
  float humidity_pct;
  float pressure_hpa;  // 0 if N/A
  uint32_t millis_ts;
  bool ok;
};

Sample readSensorsLocal() {
  Sample s{};
  s.millis_ts = millis();
  s.ok = false;
  s.pressure_hpa = 0;

#if USE_DHT
  s.temperature_c = dht.readTemperature();
  s.humidity_pct = dht.readHumidity();
  s.ok = !isnan(s.temperature_c) && !isnan(s.humidity_pct);
#else
  if (!g_bme_ok) {
    return s;
  }
  s.temperature_c = bme.readTemperature();
  s.humidity_pct = bme.readHumidity();
  s.pressure_hpa = bme.readPressure() / 100.0f;
  s.ok = !isnan(s.temperature_c) && !isnan(s.humidity_pct);
#endif
  return s;
}

// Build a compact JSON object (no ArduinoJson dependency for hackathon).
String sampleToJson(const Sample& s) {
  String j = "{";
  j += "\"temperature_c\":";
  j += String(s.temperature_c, 2);
  j += ",\"humidity_pct\":";
  j += String(s.humidity_pct, 2);
  j += ",\"pressure_hpa\":";
  j += String(s.pressure_hpa, 2);
  j += ",\"millis\":";
  j += String(s.millis_ts);
  j += ",\"ok\":";
  j += s.ok ? "true" : "false";
  j += "}";
  return j;
}

bool sdAppendLine(const String& line) {
  if (!g_sd_ok) {
    return false;
  }
  File f = SD.open("/telemetry.jsonl", FILE_WRITE);
  if (!f) {
    return false;
  }
  f.println(line);
  f.close();
  return true;
}

void updateLed() {
  static uint32_t last = 0;
  static bool phase = false;
  uint32_t now = millis();
  uint32_t period = 1000;
  switch (g_led_mode) {
    case LED_ONLINE:  period = 800; break;
    case LED_OFFLINE: period = 200; break;
    case LED_ERROR:   period = 100; break;
    default:          period = 500; break;
  }
  if (now - last >= period) {
    last = now;
    phase = !phase;
    digitalWrite(LED_STATUS, phase ? HIGH : LOW);
  }
}

/*
 * Bridge RPC hooks
 * ----------------
 * Uno Q boards expose MCU functions to Linux via Bridge. Exact registration
 * syntax varies by Arduino_RouterBridge / App Lab version — keep method names
 * stable and adapt the macros below to your installed Bridge package.
 *
 * Expected Linux calls:
 *   readSensors() -> JSON string
 *   sdWriteLine(line: string) -> bool
 *   setLedMode(mode: int) -> void
 *
 * If Bridge is unavailable during bring-up, Linux mock mode still works.
 */

#if defined(ARDUINO_UNOWIFIR4) || defined(ARDUINO_UNOR4_WIFI) || defined(ARDUINO_UNO_Q) || 1
// Soft stubs so the sketch compiles on non-Uno-Q CI hosts; on real Uno Q,
// replace with Bridge.begin() + Monitor / RPC registration from App Lab docs.
String bridgeReadSensors() {
  return sampleToJson(readSensorsLocal());
}

bool bridgeSdWriteLine(const String& line) {
  return sdAppendLine(line);
}

void bridgeSetLedMode(int mode) {
  if (mode < 0 || mode > 3) {
    g_led_mode = LED_ERROR;
  } else {
    g_led_mode = (uint8_t)mode;
  }
}
#endif

void setup() {
  pinMode(LED_STATUS, OUTPUT);
  Serial.begin(115200);
  delay(200);
  Serial.println(F("[sensor_bridge] boot"));

  Wire.begin();
#if USE_DHT
  dht.begin();
  g_bme_ok = true;  // DHT path
#else
  g_bme_ok = bme.begin(BME_ADDR);
  if (!g_bme_ok) {
    Serial.println(F("[sensor_bridge] BME280 not found — check wiring/addr"));
    g_led_mode = LED_ERROR;
  }
#endif

  if (SD.begin(SD_CS)) {
    g_sd_ok = true;
    Serial.println(F("[sensor_bridge] SD ok"));
  } else {
    Serial.println(F("[sensor_bridge] SD begin failed — offline log disabled until fixed"));
    g_led_mode = LED_ERROR;
  }

  // TODO(Maxim): register bridgeReadSensors / bridgeSdWriteLine / bridgeSetLedMode
  // with the Uno Q Bridge RPC layer once the board package is confirmed.
  Serial.println(F("[sensor_bridge] ready (register Bridge RPC on hardware)"));
}

void loop() {
  updateLed();

  // Local heartbeat for serial debug; Linux should pull via Bridge in production.
  static uint32_t lastPrint = 0;
  if (millis() - lastPrint > 2000) {
    lastPrint = millis();
    Sample s = readSensorsLocal();
    Serial.println(sampleToJson(s));
  }
}
