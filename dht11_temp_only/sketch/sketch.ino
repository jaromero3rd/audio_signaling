/*
 * Sensor read-only — Arduino UNO Q MCU
 * DHT11: S->D2  U->5V  G->GND
 * LDR:   5V - LDR - A0 - 10k - GND
 *
 * No analysis here. Linux forwards values over Bluetooth.
 */

#include <Arduino.h>
#include <DHT.h>
#include <Arduino_RouterBridge.h>

#define DHT_PIN 2
#define DHT_TYPE DHT11
#define LDR_PIN A0

DHT dht(DHT_PIN, DHT_TYPE);

float last_temp_c = NAN;
float last_humidity = NAN;
int last_light_raw = 0;

static int read_light_raw() {
  long sum = 0;
  for (int i = 0; i < 8; i++) {
    sum += analogRead(LDR_PIN);
    delay(2);
  }
  return (int)(sum / 8);
}

String get_reading(String /*unused*/) {
  String out = "{";
  out += "\"temp_c\":";
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
  pinMode(LDR_PIN, INPUT);
  dht.begin();
  delay(2000);
  Bridge.provide("get_reading", get_reading);
  Monitor.println("Sensors ready -> Bluetooth on Linux");
}

void loop() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  int raw = read_light_raw();
  last_light_raw = raw;

  if (isnan(h) || isnan(t)) {
    Monitor.println("FAIL DHT11");
    Bridge.notify("on_sample", false, 0.0, 0.0, (float)raw);
  } else {
    last_temp_c = t;
    last_humidity = h;
    Monitor.print("T=");
    Monitor.print(t, 1);
    Monitor.print("C RH=");
    Monitor.print(h, 0);
    Monitor.print("% raw=");
    Monitor.println(raw);
    Bridge.notify("on_sample", true, t, h, (float)raw);
  }
  delay(2000);
}
