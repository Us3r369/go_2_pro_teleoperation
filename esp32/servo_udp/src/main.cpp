// WiFi UDP servo receiver for the Seeed Studio XIAO ESP32-S3.
//
// Wiring (same as the servo_sweep demo):
//   Servo signal -> D10  (silk-screen label; this is GPIO9 on the XIAO ESP32-S3)
//   Servo VCC    -> 5V
//   Servo GND    -> GND
//
// Behaviour:
//   * Joins WiFi (credentials in secrets.h) and listens for UDP packets carrying
//     a target angle as ASCII degrees, e.g. "97.50".
//   * Drives a single (1-DOF / yaw) pan servo toward that angle, SLEW-LIMITED so
//     a jumpy stream or a recovery after packet loss never snaps the servo.
//   * WATCHDOG: if no packet arrives for INPUT_TIMEOUT_MS, it holds the last
//     commanded position rather than leaving the actuator in an uncommanded
//     state (mirror of the robot-side velocity deadman).
//   * Periodically BROADCASTS a discovery beacon ("GO2SERVO <listen_port>") so
//     the laptop bridge learns this board's IP with zero configuration.
//
// This is a continuous setpoint stream over UDP: newest packet wins and dropped
// packets are harmless, so there is no connection state to manage.

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESP32Servo.h>

#include "secrets.h"  // defines WIFI_SSID and WIFI_PASS (gitignored)

// --- Servo travel ----------------------------------------------------------
static const int SERVO_PIN = D10;       // GPIO9 on the XIAO ESP32-S3
static const int MIN_PULSE_US = 500;    // ~0 deg
static const int MAX_PULSE_US = 2500;   // ~180 deg
static const float SERVO_MIN_DEG = 0.0f;
static const float SERVO_MAX_DEG = 180.0f;
static const float SERVO_CENTER_DEG = 90.0f;

// --- Networking ------------------------------------------------------------
static const uint16_t LISTEN_PORT = 8888;  // laptop -> ESP32 angle stream
static const uint16_t HELLO_PORT = 8889;   // ESP32 -> laptop discovery beacon
static const unsigned long HELLO_INTERVAL_MS = 1000;

// --- Control loop ----------------------------------------------------------
static const unsigned long INPUT_TIMEOUT_MS = 500;   // watchdog: stale input
static const float MAX_SLEW_DEG_PER_S = 240.0f;      // limit servo speed
static const unsigned long LOOP_DELAY_MS = 5;        // ~200 Hz control loop

Servo servo;
WiFiUDP udp;

static float targetAngle = SERVO_CENTER_DEG;
static float currentAngle = SERVO_CENTER_DEG;
static unsigned long lastInputMs = 0;
static unsigned long lastHelloMs = 0;
static unsigned long lastLoopMs = 0;
static bool inputLive = false;

static char rxBuf[64];

static float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

// Human-readable name for a WiFi.status() code -- the key diagnostic when a
// connection never completes.
static const char *wifiStatusName(int s) {
  switch (s) {
    case WL_NO_SHIELD:
      return "NO_SHIELD";
    case WL_IDLE_STATUS:
      return "IDLE";
    case WL_NO_SSID_AVAIL:
      return "NO_SSID_AVAIL (network not seen -- wrong name, out of range, or 5GHz-only)";
    case WL_SCAN_COMPLETED:
      return "SCAN_COMPLETED";
    case WL_CONNECTED:
      return "CONNECTED";
    case WL_CONNECT_FAILED:
      return "CONNECT_FAILED (usually a wrong password)";
    case WL_CONNECTION_LOST:
      return "CONNECTION_LOST";
    case WL_DISCONNECTED:
      return "DISCONNECTED";
    default:
      return "UNKNOWN";
  }
}

// List the 2.4 GHz networks the ESP32 can actually see. If the target SSID is
// absent here, it is on 5 GHz or out of range -- not a credential problem.
static void scanNetworks() {
  Serial.println("Scanning for visible 2.4 GHz networks...");
  int n = WiFi.scanNetworks();
  if (n <= 0) {
    Serial.println("  (none found)");
    return;
  }
  for (int i = 0; i < n; i++) {
    Serial.printf("  %2d: %-32s ch%2d  %4ddBm  %s\n", i, WiFi.SSID(i).c_str(),
                  WiFi.channel(i), WiFi.RSSI(i),
                  WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "open" : "secured");
  }
  WiFi.scanDelete();
}

static void connectWifi() {
  Serial.println();
  Serial.println("=== XIAO ESP32-S3 UDP servo receiver (WiFi diagnostics) ===");
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(100);
  scanNetworks();

  Serial.printf("Target SSID from secrets.h: '%s'\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long lastReport = 0;
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastReport > 2500) {
      lastReport = millis();
      int s = WiFi.status();
      Serial.printf("  still connecting... status=%d (%s)\n", s, wifiStatusName(s));
    }
    delay(250);
  }
  Serial.printf("Connected. IP=%s  listening on UDP:%u\n",
                WiFi.localIP().toString().c_str(), LISTEN_PORT);
}

// Broadcast a discovery beacon so the laptop bridge learns our IP + listen port.
static void sendHello() {
  udp.beginPacket(WiFi.broadcastIP(), HELLO_PORT);
  char msg[32];
  int n = snprintf(msg, sizeof(msg), "GO2SERVO %u", LISTEN_PORT);
  udp.write(reinterpret_cast<const uint8_t *>(msg), n);
  udp.endPacket();
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("XIAO ESP32-S3 UDP servo receiver starting...");

  connectWifi();
  udp.begin(LISTEN_PORT);

  // ESP32Servo: allocate a hardware timer and set the standard 50 Hz frame.
  servo.setPeriodHertz(50);
  servo.attach(SERVO_PIN, MIN_PULSE_US, MAX_PULSE_US);
  servo.write(static_cast<int>(currentAngle));

  lastLoopMs = millis();
}

void loop() {
  const unsigned long now = millis();

  // 1) Drain all pending packets; keep only the newest angle (newest wins).
  bool got = false;
  float newAngle = targetAngle;
  int packetSize;
  while ((packetSize = udp.parsePacket()) > 0) {
    int n = udp.read(rxBuf, sizeof(rxBuf) - 1);
    if (n > 0) {
      rxBuf[n] = '\0';
      newAngle = atof(rxBuf);
      got = true;
    }
  }
  if (got) {
    targetAngle = clampf(newAngle, SERVO_MIN_DEG, SERVO_MAX_DEG);
    lastInputMs = now;
    if (!inputLive) {
      inputLive = true;
      Serial.println("input: live");
    }
  }

  // 2) Watchdog: if input went stale, hold the last commanded position.
  if (inputLive && (now - lastInputMs > INPUT_TIMEOUT_MS)) {
    inputLive = false;
    Serial.println("input: STALE -> holding position");
  }

  // 3) Slew-limit toward the target so jumps/recovery don't snap the servo.
  const float dt = (now - lastLoopMs) / 1000.0f;
  lastLoopMs = now;
  const float maxStep = MAX_SLEW_DEG_PER_S * dt;
  float err = targetAngle - currentAngle;
  if (err > maxStep) err = maxStep;
  if (err < -maxStep) err = -maxStep;
  currentAngle += err;
  servo.write(static_cast<int>(lroundf(currentAngle)));

  // 4) Periodic discovery beacon (only while connected).
  if (now - lastHelloMs > HELLO_INTERVAL_MS) {
    lastHelloMs = now;
    if (WiFi.status() == WL_CONNECTED) {
      sendHello();
    }
  }

  delay(LOOP_DELAY_MS);
}
