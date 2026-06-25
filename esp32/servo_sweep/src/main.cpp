// Servo sweep demo for the Seeed Studio XIAO ESP32-S3.
//
// Wiring:
//   Servo signal -> D10  (silk-screen label; this is GPIO9 on the XIAO ESP32-S3)
//   Servo VCC    -> 5V
//   Servo GND    -> GND
//
// Behaviour: sweep the servo across its full travel one way (0 deg -> 180 deg),
// pause, then back the other way (180 deg -> 0 deg), and repeat. A standard
// positional hobby servo (e.g. SG90) cannot do a true 360 deg revolution, so a
// full end-to-end sweep is the closest "one revolution to each side" it can do.
//
// If your servo doesn't reach the mechanical ends or buzzes at the extremes,
// tweak MIN_PULSE_US / MAX_PULSE_US below to match your servo's spec.

#include <Arduino.h>
#include <ESP32Servo.h>

static const int SERVO_PIN = D10;  // GPIO9 on the XIAO ESP32-S3

// Pulse widths (microseconds) for the servo's travel limits.
static const int MIN_PULSE_US = 500;   // ~0 deg
static const int MAX_PULSE_US = 2500;  // ~180 deg

static const int STEP_DELAY_MS = 15;   // pause between 1-degree steps (speed)
static const int END_PAUSE_MS = 700;   // pause at each end of travel

Servo servo;

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("XIAO ESP32-S3 servo sweep starting on D10 (GPIO9)...");

  // ESP32Servo: allocate a hardware timer and set the standard 50 Hz frame.
  servo.setPeriodHertz(50);
  servo.attach(SERVO_PIN, MIN_PULSE_US, MAX_PULSE_US);
}

void loop() {
  // Sweep one way: 0 -> 180 degrees.
  Serial.println("Sweeping 0 -> 180");
  for (int angle = 0; angle <= 180; angle++) {
    servo.write(angle);
    delay(STEP_DELAY_MS);
  }
  delay(END_PAUSE_MS);

  // Sweep back the other way: 180 -> 0 degrees.
  Serial.println("Sweeping 180 -> 0");
  for (int angle = 180; angle >= 0; angle--) {
    servo.write(angle);
    delay(STEP_DELAY_MS);
  }
  delay(END_PAUSE_MS);
}
