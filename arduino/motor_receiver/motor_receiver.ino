/*
 * motor_receiver.ino  —  BTS7960 dual H-bridge version
 *
 * Receives CMD,<left_us>,<right_us>\n frames from the Pi (ttyAMA0) over
 * UART — IDENTICAL wire protocol to the previous Servo-based sketch, so
 * motor_writer.py on the Pi needs NO changes.
 *
 * Hardware: TWO separate BTS7960 modules — one per motor. A single
 * BTS7960 drives only one DC motor; it cannot drive both left and right
 * from one board.
 *
 * Protocol (unchanged):
 *   CMD,1500,1500\n   stop
 *   CMD,2000,2000\n   full forward
 *   CMD,1000,1000\n   full reverse
 *   CMD,2000,1500\n   left full forward, right stopped (turn right)
 *   CMD,1000,2000\n   left full reverse, right full forward (hard pivot)
 *
 * left_us / right_us are 1000–2000, exactly as before:
 *   1500           = stop (both RPWM and LPWM driven LOW — coast, not brake)
 *   1500..2000     = forward, scaled duty cycle 0..255 on RPWM
 *   1000..1500     = reverse, scaled duty cycle 255..0 on LPWM
 *
 * Wiring — Pi link (unchanged)
 * ─────────────────────────────
 *   Arduino GND  ──── Pi GND
 *   Arduino RX   ◄─── Pi TX   (GPIO 14 / pin 8 on Pi GPIO header)
 *   Arduino TX   ───► Pi RX   (GPIO 15 / pin 10 — optional, debug only)
 *
 * Wiring — Left BTS7960
 * ──────────────────────
 *   RPWM  ──► Arduino D9   (forward PWM)
 *   LPWM  ──► Arduino D10  (reverse PWM)
 *   R_EN  ──► Arduino D6   (tie HIGH at boot — forward side enable)
 *   L_EN  ──► Arduino D7   (tie HIGH at boot — reverse side enable)
 *   VCC   ──► Arduino 5V
 *   GND   ──► Arduino GND
 *   B+/B- ──► motor power supply (NOT the Arduino — separate battery/PSU)
 *   M+/M- ──► left motor terminals
 *
 * Wiring — Right BTS7960
 * ───────────────────────
 *   RPWM  ──► Arduino D11  (forward PWM)
 *   LPWM  ──► Arduino D12  (reverse PWM — note: D12 is NOT a PWM-capable
 *                            pin on Uno/Nano; see PIN NOTE below)
 *   R_EN  ──► Arduino D4
 *   L_EN  ──► Arduino D5
 *   VCC   ──► Arduino 5V
 *   GND   ──► Arduino GND
 *   B+/B- ──► motor power supply (separate battery/PSU)
 *   M+/M- ──► right motor terminals
 *
 * ⚠ PIN NOTE — verify before wiring:
 *   Arduino Uno/Nano PWM-capable pins are: 3, 5, 6, 9, 10, 11.
 *   D12 above is a placeholder and is NOT PWM-capable on Uno/Nano — it
 *   will not work for LPWM_RIGHT as written. Re-map RIGHT to use two of
 *   {3, 5, 6} instead (LEFT already uses 9, 10). Suggested fix:
 *     RPWM_RIGHT -> D11, LPWM_RIGHT -> D3
 *   This sketch uses D11/D3 below (NOT D11/D12) — the wiring comment
 *   above intentionally flags the mistake so you check your own board's
 *   pinout (Mega has far more PWM pins, Uno/Nano are limited) before
 *   committing wires.
 *
 * Safety
 * ───────
 *   RPWM and LPWM are NEVER driven simultaneously nonzero — the BTS7960
 *   datasheet and multiple integration guides warn this can damage the
 *   module. set_motor() below always zeroes the unused side first.
 *
 * Watchdog
 * ─────────
 *   If no valid CMD frame arrives for WATCHDOG_MS, both motors are
 *   commanded to stop. Protects against Pi crash or UART disconnect.
 */

// ── Pin assignments ─────────────────────────────────────────────────────────
// Left BTS7960
const uint8_t L_RPWM = 9;    // forward PWM, left motor
const uint8_t L_LPWM = 10;   // reverse PWM, left motor
const uint8_t L_REN  = 6;    // forward enable, left motor
const uint8_t L_LEN  = 7;    // reverse enable, left motor

// Right BTS7960  (see PIN NOTE above — D3 used instead of D12)
const uint8_t R_RPWM = 11;   // forward PWM, right motor
const uint8_t R_LPWM = 3;    // reverse PWM, right motor
const uint8_t R_REN  = 4;    // forward enable, right motor
const uint8_t R_LEN  = 5;    // reverse enable, right motor

const uint8_t PIN_LED = LED_BUILTIN;

// ── Protocol constants (unchanged from previous sketch) ────────────────────
const int PWM_MIN  = 1000;     // full reverse
const int PWM_STOP = 1500;     // neutral / stop
const int PWM_MAX  = 2000;     // full forward
const unsigned long WATCHDOG_MS = 300;   // stop motors if no command for this long

// ── Serial configuration ────────────────────────────────────────────────────
const long BAUD = 115200;

// ── State ───────────────────────────────────────────────────────────────────
unsigned long last_cmd_ms = 0;
int last_left  = PWM_STOP;
int last_right = PWM_STOP;

// ── Setup ────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(BAUD);

    pinMode(L_RPWM, OUTPUT);
    pinMode(L_LPWM, OUTPUT);
    pinMode(L_REN,  OUTPUT);
    pinMode(L_LEN,  OUTPUT);

    pinMode(R_RPWM, OUTPUT);
    pinMode(R_LPWM, OUTPUT);
    pinMode(R_REN,  OUTPUT);
    pinMode(R_LEN,  OUTPUT);

    // Enable both half-bridges on both modules.
    digitalWrite(L_REN, HIGH);
    digitalWrite(L_LEN, HIGH);
    digitalWrite(R_REN, HIGH);
    digitalWrite(R_LEN, HIGH);

    // Start fully stopped on both sides.
    set_motor(L_RPWM, L_LPWM, PWM_STOP);
    set_motor(R_RPWM, R_LPWM, PWM_STOP);

    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);

    Serial.println("motor_receiver (BTS7960 x2) ready — waiting for CMD frames");
}

// ── Main loop ───────────────────────────────────────────────────────────────
void loop() {
    if (millis() - last_cmd_ms > WATCHDOG_MS) {
        if (last_left != PWM_STOP || last_right != PWM_STOP) {
            last_left  = PWM_STOP;
            last_right = PWM_STOP;
            set_motor(L_RPWM, L_LPWM, last_left);
            set_motor(R_RPWM, R_LPWM, last_right);
            Serial.println("WATCHDOG — motors stopped");
        }
    }

    if (parse_cmd_frame()) {
        set_motor(L_RPWM, L_LPWM, last_left);
        set_motor(R_RPWM, R_LPWM, last_right);
        last_cmd_ms = millis();
    }
}

// ── Motor output: converts a 1000–2000 µs-style value to BTS7960 duty ───────
/*
 * us_value: 1000 (full reverse) .. 1500 (stop) .. 2000 (full forward)
 *
 * Forward (us_value > 1500): drive PWM pin with scaled duty, hold
 *   reverse pin at 0 first.
 * Reverse (us_value < 1500): drive reverse PWM pin with scaled duty,
 *   hold forward pin at 0 first.
 * Stop (us_value == 1500): both pins 0 (coast — NOT a hard brake).
 *
 * RPWM and LPWM are never both nonzero at the same time.
 */
void set_motor(uint8_t pwm_fwd_pin, uint8_t pwm_rev_pin, int us_value) {
    us_value = constrain(us_value, PWM_MIN, PWM_MAX);

    if (us_value > PWM_STOP) {
        // Forward: zero the reverse side FIRST, then drive forward side.
        int duty = map(us_value, PWM_STOP, PWM_MAX, 0, 255);
        analogWrite(pwm_rev_pin, 0);
        analogWrite(pwm_fwd_pin, duty);
    } else if (us_value < PWM_STOP) {
        // Reverse: zero the forward side FIRST, then drive reverse side.
        int duty = map(us_value, PWM_STOP, PWM_MIN, 0, 255);
        analogWrite(pwm_fwd_pin, 0);
        analogWrite(pwm_rev_pin, duty);
    } else {
        // Stop: both zero.
        analogWrite(pwm_fwd_pin, 0);
        analogWrite(pwm_rev_pin, 0);
    }
}

// ── Frame parser (unchanged from previous sketch) ───────────────────────────
bool parse_cmd_frame() {
    if (Serial.available() < 1) return false;

    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) return false;

    if (!line.startsWith("CMD,")) return false;

    int comma1 = line.indexOf(',');
    int comma2 = line.indexOf(',', comma1 + 1);
    if (comma1 < 0 || comma2 < 0) return false;

    String left_str  = line.substring(comma1 + 1, comma2);
    String right_str = line.substring(comma2 + 1);

    int left  = left_str.toInt();
    int right = right_str.toInt();

    if (left  < PWM_MIN || left  > PWM_MAX) return false;
    if (right < PWM_MIN || right > PWM_MAX) return false;

    last_left  = left;
    last_right = right;

    digitalWrite(PIN_LED, HIGH);
    delayMicroseconds(5000);
    digitalWrite(PIN_LED, LOW);

    Serial.print("OK ");
    Serial.print(last_left);
    Serial.print(' ');
    Serial.println(last_right);

    return true;
}