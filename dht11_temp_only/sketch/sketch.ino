/*
 * DHT11 + LDR → App Lab Bridge.notify → host BLE relay → laptop
 *
 * DHT11:  S->D2  U->5V  G->GND
 * LDR:    5V — LDR — A0 — 100k — GND
 *
 * This is the sketch that streamed real samples (Bridge.notify every 50 ms,
 * DHT read at most once a second). Do not poll get_reading from Python.
 */

#include <Arduino.h>
#include <DHT.h>
#include <Arduino_RouterBridge.h>

#define DHT_PIN 2
#define DHT_TYPE DHT11
#define LDR_PIN A0

#define LOOP_MS 50UL
#define DHT_MIN_MS 1000UL

DHT dht(DHT_PIN, DHT_TYPE);

float last_temp_c = NAN;
float last_humidity = NAN;
int last_light_raw = 0;
unsigned long last_dht_ms = 0;
bool dht_ok = false;
uint32_t sample_n = 0;

static int read_light_raw() {
  long sum = 0;
  for (int i = 0; i < 4; i++) {
    sum += analogRead(LDR_PIN);
  }
  return (int)(sum / 4);
}

String get_reading(String /*unused*/) {
  String out = "{";
  out += "\"ok\":";
  out += dht_ok ? "true" : "false";
  out += ",\"temp_c\":";
  out += isnan(last_temp_c) ? "null" : String(last_temp_c, 1);
  out += ",\"humidity_pct\":";
  out += isnan(last_humidity) ? "null" : String(last_humidity, 0);
  out += ",\"light_raw\":";
  out += String(last_light_raw);
  out += "}";
  return out;
}

void setup() {
  Bridge.begin();
  Monitor.begin();
  Serial.begin(115200);

  pinMode(LDR_PIN, INPUT);
  dht.begin();
  delay(1500);

  Bridge.provide("get_reading", get_reading);

  Monitor.println("MCU ready: DHT11+LDR → on_sample");
  Serial.println("MCU ready: DHT11+LDR → on_sample");
}

void loop() {
  unsigned long now = millis();
  int raw = read_light_raw();
  last_light_raw = raw;

  if (now - last_dht_ms >= DHT_MIN_MS || last_dht_ms == 0) {
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    last_dht_ms = now;
    if (!isnan(h) && !isnan(t)) {
      last_temp_c = t;
      last_humidity = h;
      dht_ok = true;
    } else {
      dht_ok = false;
    }
  }

  sample_n++;
  if ((sample_n % 20) == 0) {
    Monitor.print("#");
    Monitor.print(sample_n);
    Monitor.print(" T=");
    if (dht_ok) {
      Monitor.print(last_temp_c, 1);
      Monitor.print("C RH=");
      Monitor.print(last_humidity, 0);
    } else {
      Monitor.print("---- RH=----");
    }
    Monitor.print(" raw=");
    Monitor.println(raw);
  }

  // bool is an int to Python; on_sample treats 1 as a good reading.
  Bridge.notify(
      "on_sample",
      dht_ok ? 1.0f : 0.0f,
      isnan(last_temp_c) ? 0.0f : last_temp_c,
      isnan(last_humidity) ? 0.0f : last_humidity,
      (float)raw);

  delay(LOOP_MS);
}
