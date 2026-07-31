#define LIGHTSENSORPIN A0 //Ambient light sensor reading

void setup() {
  pinMode(LIGHTSENSORPIN, INPUT);
  Serial.begin(19200);
}

void loop() {
  int reading = analogRead(LIGHTSENSORPIN); //Read light level (0-1023)
  Serial.println(reading);
  Serial.flush();
}
