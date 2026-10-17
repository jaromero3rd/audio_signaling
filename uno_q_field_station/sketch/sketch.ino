/*
 * AgriDrone field station — MCU side (Arduino UNO Q STM32).
 *
 * Sensors on this sketch:
 *   - DHT11 on D2         → temperature + humidity
 *   - Photoresistor on A0 → ambient light (voltage divider)
 *   - MicroSD (SPI)       → CS=D10, MOSI=D11, MISO=D12, SCK=D13
 *
 * Microphone is NOT on the MCU and is NOT used for ElevenAgents here.
 * Linux (mic_capture / mic_transfer) only captures PCM and POSTs it to the
 * ground laptop. Farmer voice commands run on the laptop via voice_eleven.py.
 */

#include <Arduino.h>
#include <DHT.h>
#include <SPI.h>
#include <SD.h>
#include <Arduino_RouterBridge.h>

// UNO Q classic header: digital pin labeled D2 (MCU PB3).
#ifndef DHT_PIN
#define DHT_PIN 2
#endif

#define DHT_TYPE DHT11

#ifndef LDR_PIN
#define LDR_PIN A0
#endif

#ifndef SD_CS_PIN
#define SD_CS_PIN 10
#endif

// DHT11 needs >= 1 s between reads; 2.5 s is safe.
#ifndef SAMPLE_MS
#define SAMPLE_MS 2500UL
#endif

static const char *SD_LOG_PATH = "telemetry.csv";

DHT dht(DHT_PIN, DHT_TYPE);

float last_temp_c = NAN;
float last_temp_f = NAN;
float last_humidity = NAN;
float last_heat_index_c = NAN;
float last_light = NAN;  // 0..1
bool sd_ready = false;
bool offline_mode = false;
unsigned long last_sample_ms = 0;
uint32_t sample_seq = 0;

static bool ensure_sd_header() {
  if (!sd_ready) {
    return false;
  }
  if (SD.exists(SD_LOG_PATH)) {
    return true;
  }
  File f = SD.open(SD_LOG_PATH, FILE_WRITE);
  if (!f) {
    return false;
  }
  f.println(
      "seq,uptime_ms,temp_c,temp_f,humidity_pct,heat_index_c,light,source");
  f.close();
  return true;
}

static bool append_sd_line(const String &line) {
  if (!sd_ready && !SD.begin(SD_CS_PIN)) {
    sd_ready = false;
    return false;
  }
  sd_ready = true;
  if (!ensure_sd_header()) {
    return false;
  }
  File f = SD.open(SD_LOG_PATH, FILE_WRITE);
  if (!f) {
    return false;
  }
  f.println(line);
  f.close();
  return true;
}

static float clampf(float v, float lo, float hi) {
  if (v < lo) {
    return lo;
  }
  if (v > hi) {
    return hi;
  }
  return v;
}

static float read_light_norm() {
  int raw = analogRead(LDR_PIN);
  return clampf((float)raw / 1023.0f, 0.0f, 1.0f);
}

String get_reading(String /*unused*/) {
  // Full DHT11 payload: humidity + temp C/F + heat index, plus light.
  String out = "{";
  out += "\"sensor\":\"DHT11\"";
  out += ",\"pin\":\"D2\"";
  out += ",\"temp_c\":";
  out += isnan(last_temp_c) ? "null" : String(last_temp_c, 1);
  out += ",\"temp_f\":";
  out += isnan(last_temp_f) ? "null" : String(last_temp_f, 1);
  out += ",\"humidity_pct\":";
  out += isnan(last_humidity) ? "null" : String(last_humidity, 0);
  out += ",\"heat_index_c\":";
  out += isnan(last_heat_index_c) ? "null" : String(last_heat_index_c, 1);
  out += ",\"light\":";
  out += isnan(last_light) ? "null" : String(last_light, 3);
  out += ",\"seq\":";
  out += String(sample_seq);
  out += ",\"offline\":";
  out += offline_mode ? "true" : "false";
  out += ",\"sd_ready\":";
  out += sd_ready ? "true" : "false";
  out += "}";
  return out;
}

void set_offline(bool offline) {
  offline_mode = offline;
  Monitor.print("offline_mode=");
  Monitor.println(offline ? "1" : "0");
}

bool sd_append(String line) {
  return append_sd_line(line);
}

String sd_pending(String /*unused*/) {
  if (!sd_ready && !SD.begin(SD_CS_PIN)) {
    return "";
  }
  sd_ready = true;
  if (!SD.exists(SD_LOG_PATH)) {
    return "";
  }
  File f = SD.open(SD_LOG_PATH, FILE_READ);
  if (!f) {
    return "";
  }
  const size_t kMax = 8000;
  String body;
  body.reserve(kMax);
  while (f.available() && body.length() < kMax) {
    body += (char)f.read();
  }
  f.close();
  return body;
}

bool sd_clear(String /*unused*/) {
  if (!sd_ready && !SD.begin(SD_CS_PIN)) {
    return false;
  }
  sd_ready = true;
  if (SD.exists(SD_LOG_PATH)) {
    SD.remove(SD_LOG_PATH);
  }
  return ensure_sd_header();
}

String get_sd_status(String /*unused*/) {
  String out = "{";
  out += "\"sd_ready\":";
  out += sd_ready ? "true" : "false";
  out += ",\"offline\":";
  out += offline_mode ? "true" : "false";
  out += "}";
  return out;
}

void setup() {
  Bridge.begin();
  Monitor.begin();

  pinMode(LDR_PIN, INPUT);

  dht.begin();
  delay(2000);  // DHT11 warm-up

  sd_ready = SD.begin(SD_CS_PIN);
  if (sd_ready) {
    ensure_sd_header();
    Monitor.println("SD ready");
  } else {
    Monitor.println("SD init failed - online-only until card mounts");
  }

  Bridge.provide("get_reading", get_reading);
  Bridge.provide("set_offline", set_offline);
  Bridge.provide("sd_append", sd_append);
  Bridge.provide("sd_pending", sd_pending);
  Bridge.provide("sd_clear", sd_clear);
  Bridge.provide("get_sd_status", get_sd_status);

  Monitor.println("UNO Q MCU ready (DHT11 + LDR); mic via Linux sounddevice");
}

void loop() {
  unsigned long now = millis();
  if (now - last_sample_ms < SAMPLE_MS) {
    delay(20);
    return;
  }
  last_sample_ms = now;

  // DHT11 reports relative humidity (%) and temperature (C). We also request F
  // and compute heat index so the ground station sees the full useful set.
  float h = dht.readHumidity();
  float t_c = dht.readTemperature();       // Celsius
  float t_f = dht.readTemperature(true);   // Fahrenheit
  if (isnan(h) || isnan(t_c) || isnan(t_f)) {
    Monitor.println("DHT11 read failed (check VCC/GND/DATA on D2 + pull-up)");
    return;
  }
  float hi_c = dht.computeHeatIndex(t_c, h, false);

  float light = read_light_norm();
  last_humidity = h;
  last_temp_c = t_c;
  last_temp_f = t_f;
  last_heat_index_c = hi_c;
  last_light = light;
  sample_seq++;

  Monitor.print("DHT11  T=");
  Monitor.print(t_c, 1);
  Monitor.print("C / ");
  Monitor.print(t_f, 1);
  Monitor.print("F  RH=");
  Monitor.print(h, 0);
  Monitor.print("%  HI=");
  Monitor.print(hi_c, 1);
  Monitor.print("C  light=");
  Monitor.println(light, 3);

  bool notified = (bool)Bridge.notify(
      "on_sensor_sample",
      t_c,
      t_f,
      h,
      hi_c,
      light,
      (int)sample_seq,
      (int)now);

  if (offline_mode || !notified) {
    String line = String(sample_seq) + "," + String(now) + "," +
                  String(t_c, 1) + "," + String(t_f, 1) + "," +
                  String(h, 0) + "," + String(hi_c, 1) + "," +
                  String(light, 3) + ",sd";
    if (append_sd_line(line)) {
      Monitor.println("buffered to SD");
    } else {
      Monitor.println("SD append failed");
    }
  }
}
