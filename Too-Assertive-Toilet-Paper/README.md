# Too-Assertive Toilet Paper（自己主張が強いトイレットペーパー）

> 自らの「命」の終わりが近づくにつれて感情をあらわにし、必死に存在を訴えかける「自己主張が強いトイレットペーパー」の物語です。
>
> *The story of a toilet paper roll with a strong personality: as the end of its "life" gets closer, it shows more and more emotion and pleads harder to be noticed.*

A small physical-computing experiment I built during a class in 2025. A toilet paper holder tracks how much paper is left. As the roll runs low, the holder gets more emotional: its face on a tiny LCD changes, it plays voice clips, and when the roll is empty it sends an email asking someone to replace it.

The point was to try giving an everyday object a personality, instead of just showing a number.

---

## How it behaves

| Paper left | Face on LCD | What else happens |
|---|---|---|
| 81–100 % | `(^o^)` | Plays a sound when a fresh roll is loaded (100 %) |
| 51–80 % | `(^_^;)` | |
| 21–50 % | `(o_o;)` | Plays a sound when it drops to 50 % |
| 1–20 % | `(T_T)` | Plays a sound when it drops to 10 % |
| 0 % | `(; ;)` | Sends an email: 「【緊急通知】TPがゼロになりました！」("Urgent: the toilet paper is gone!") |

- **Pulling paper.** A rotary encoder turns with the roll. Each step in one direction takes 5 % off the remaining amount; turning the other way adds 1 % (for small corrections).
- **Replacing the roll.** Pressing the push button resets the level to 100 % and allows a new email to be sent next time.
- **One email per roll.** It only sends again after a reset.

## Hardware

Two microcontrollers split the work and talk over I²C:

```
 ┌──────────────────────┐        I²C (ESP32 = master,       ┌────────────────────────┐
 │        ESP32         │ ◀──────  Arduino = slave @ 0x09) ─▶│      Arduino Uno       │
 │  • Wi-Fi + email     │                                    │  • Rotary encoder      │
 │  • Keeps the % level │   Arduino → ESP32: input event     │  • Push button (reset) │
 │  • Chooses the face  │   ESP32 → Arduino: "face|  NN%"    │  • 8×2 character LCD   │
 │  • APDS-9930 sensor  │                                    │  • DFPlayer Mini + spk │
 └──────────────────────┘                                    └────────────────────────┘
```

| Part | Role |
|---|---|
| ESP32 | Main logic, Wi-Fi, sends the email through Gmail SMTP |
| Arduino (Uno-class) | Reads the inputs, drives the LCD and sound |
| Rotary encoder | Measures how much paper is pulled |
| Tactile push button | "New roll" reset |
| 8×2 character LCD (HD44780-type) | Shows the face and the percentage |
| DFRobot DFPlayer Mini + speaker | Plays voice clips `0001`–`0003` from a microSD card |
| APDS-9930 proximity sensor | Set up on the ESP32 but not used in the final logic |

## Code

| File | Runs on | What it does |
|---|---|---|
| [`task4esp32.ino`](task4esp32.ino) | ESP32 | Connects to Wi-Fi. Every 50 ms it asks the Arduino for an input event (1 = pulled, 2 = turned back, 3 = reset), updates the percentage, chooses the face, sends `face\|percentage` back to be displayed, and emails at 0 %. |
| [`task4-arduino.ino`](task4-arduino.ino) | Arduino | I²C slave at address 9. Turns encoder and button input into events. Shows whatever the ESP32 sends, and plays the matching sound when the level crosses 100 %, 50 % or 10 %. |
| [`credentials.example.h`](credentials.example.h) | ESP32 | Template for the Wi-Fi and Gmail settings |

### Libraries

- **ESP32:** `WiFi`, `Wire`, [`ESP_Mail_Client`](https://github.com/mobizt/ESP-Mail-Client), `APDS9930`
- **Arduino:** `Wire`, `SoftwareSerial`, `LiquidCrystal`, [`DFRobotDFPlayerMini`](https://github.com/DFRobot/DFRobotDFPlayerMini), `RotaryEncoder`

### Setup

1. Copy `credentials.example.h` to `credentials.h` and fill in your Wi-Fi name and password, plus a Gmail address and a [Gmail app password](https://support.google.com/accounts/answer/185833). `credentials.h` is git-ignored. Never commit it.
2. Put three audio files on the DFPlayer's microSD card: `0001.mp3` (10 % left), `0002.mp3` (50 % left) and `0003.mp3` (new roll).
3. Upload `task4-arduino.ino` to the Arduino and `task4esp32.ino` to the ESP32. Connect SDA, SCL and GND between the two boards.

## Notes and limitations

This was a quick class experiment, not a finished product:

- The amount left is estimated from encoder steps, not measured, so it drifts. The proximity sensor was meant to help with this but never got used.
- In the code, the DFPlayer's software-serial pins (10, 11) are the same as two LCD data pins. These need to be moved to free pins on a real build.
- The ESP32 waits for Wi-Fi before it does anything else. If there's no network, the device never starts.
- The `dialogue` strings in the ESP32 code are empty placeholders for spoken lines that only exist as audio files.
