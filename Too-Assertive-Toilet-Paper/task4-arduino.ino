#include <SoftwareSerial.h>
#include <DFRobotDFPlayerMini.h>
#include <Wire.h>
#include <RotaryEncoder.h>
#include <LiquidCrystal.h>

#define ENCODER_CLK_PIN 2
#define ENCODER_DT_PIN  3
#define TACT_SWITCH_PIN 4

#define DFPLAYER_RX_PIN 10
#define DFPLAYER_TX_PIN 11

#define I2C_ADDRESS 9

const int LCD_RS = 8, LCD_EN = 9, LCD_D4 = 10, LCD_D5 = 11, LCD_D6 = 12, LCD_D7 = 13;

SoftwareSerial dfpSerial(DFPLAYER_RX_PIN, DFPLAYER_TX_PIN);
DFRobotDFPlayerMini myDFPlayer;
RotaryEncoder encoder(ENCODER_CLK_PIN, ENCODER_DT_PIN);
LiquidCrystal lcd(LCD_RS, LCD_EN, LCD_D4, LCD_D5, LCD_D6, LCD_D7);

long oldPosition = 0;
volatile byte eventToSend = 0;

void setup() {
    lcd.begin(8, 2);
    lcd.print("Connecting..");

    dfpSerial.begin(9600);

    if (myDFPlayer.begin(dfpSerial)) {
        myDFPlayer.volume(25);
    }

    pinMode(ENCODER_CLK_PIN, INPUT_PULLUP);
    pinMode(ENCODER_DT_PIN, INPUT_PULLUP);
    pinMode(TACT_SWITCH_PIN, INPUT_PULLUP);

    oldPosition = encoder.getPosition();

    Wire.begin(I2C_ADDRESS);
    Wire.onReceive(receiveEvent);
    Wire.onRequest(requestEvent);
}

void loop() {
    if (eventToSend != 0) {
        return;
    }

    if (digitalRead(TACT_SWITCH_PIN) == LOW) {
        delay(50);
        eventToSend = 3;
        while(digitalRead(TACT_SWITCH_PIN) == LOW);
        return;
    }

    encoder.tick();
    long newPosition = encoder.getPosition();

    if (newPosition != oldPosition) {
        if (newPosition > oldPosition) {
            eventToSend = 1;
        } else {
            eventToSend = 2;
        }
        oldPosition = newPosition;
    }
}

void receiveEvent(int numBytes) {
    static int last_percentage = -1;
    String line1 = "", line2 = "";
    bool onFirstLine = true;

    while (Wire.available()) {
        char c = Wire.read();
        if (c == '|') {
            onFirstLine = false;
        } else {
            if (onFirstLine) {
                line1 += c;
            } else {
                line2 += c;
            }
        }
    }

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print(line1);
    lcd.setCursor(0, 1);
    lcd.print(line2);

    String percentageStr = line2;
    percentageStr.trim();
    percentageStr.replace("%", "");
    int current_percentage = percentageStr.toInt();

    if (current_percentage == 100 && last_percentage < 100) {
        myDFPlayer.play(3);
    } else if (current_percentage <= 50 && last_percentage > 50) {
        myDFPlayer.play(2);
    } else if (current_percentage <= 10 && last_percentage > 10) {
        myDFPlayer.play(1);
    }
    
    last_percentage = current_percentage;
}

void requestEvent() {
    Wire.write(eventToSend);
    eventToSend = 0;
}
