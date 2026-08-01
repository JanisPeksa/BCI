// Device-timestamped photosensor stream for SSVEP monitor validation.
//
// Output format:
//   sample_index,device_time_us,light_amp
//
// device_time_us is captured on the Arduino immediately before analogRead(),
// so frequency analysis does not depend on when the host process drains USB.

constexpr uint8_t LIGHT_SENSOR_PIN = A0;
constexpr unsigned long SERIAL_BAUD = 115200;
constexpr uint32_t SAMPLE_INTERVAL_US = 2500;  // Fixed 400 Hz cadence.

uint32_t sample_index = 0;
uint32_t next_sample_us = 0;

void setup() {
  pinMode(LIGHT_SENSOR_PIN, INPUT);
  Serial.begin(SERIAL_BAUD);
  Serial.println(F("sample_index,device_time_us,light_amp"));
  Serial.flush();
  next_sample_us = micros();
}

void loop() {
  const uint32_t now = micros();
  if (static_cast<int32_t>(now - next_sample_us) < 0) {
    return;
  }

  const uint32_t captured_at_us = micros();
  const int reading = analogRead(LIGHT_SENSOR_PIN);

  Serial.print(sample_index);
  Serial.print(',');
  Serial.print(captured_at_us);
  Serial.print(',');
  Serial.println(reading);
  Serial.flush();

  ++sample_index;
  next_sample_us += SAMPLE_INTERVAL_US;

  // If output ever takes longer than one complete interval, resume from the
  // current device time instead of emitting a catch-up burst.
  const uint32_t after_write_us = micros();
  if (static_cast<int32_t>(after_write_us - next_sample_us) >= 0) {
    next_sample_us = after_write_us + SAMPLE_INTERVAL_US;
  }
}
