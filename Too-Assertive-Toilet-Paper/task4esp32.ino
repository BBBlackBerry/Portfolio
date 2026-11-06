#include <WiFi.h>
#include <ESP_Mail_Client.h>
#include <Wire.h>
#include <APDS9930.h>
#include "credentials.h"

#define ARDUINO_I2C_ADDRESS 9

APDS9930 apds = APDS9930();
SMTPSession smtp;

int tp_remaining = 100;
int last_tp_level = 100;
bool isSensorReady = false;
bool email_sent_flag = false;

void updateAndDisplayStatus();
void sendNotificationEmail();
void smtpCallback(SMTP_Status status);

void setup() {
    Serial.begin(115200);
    
    Serial.print("Connecting to Wi-Fi...");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        Serial.print(".");
        delay(200);
    }
    Serial.println("\nWiFi connected.");

    Wire.begin();

    isSensorReady = apds.init() && apds.enableProximitySensor(false);
    if (!isSensorReady) {
        Serial.println(F("APDS-9930 Sensor Init FAILED!"));
    } else {
        Serial.println(F("APDS-9930 Proximity Sensor Initialized."));
    }

    smtp.callback(smtpCallback);
    
    updateAndDisplayStatus();
}

void loop() {
    Wire.requestFrom(ARDUINO_I2C_ADDRESS, 1);
    byte event = 0;
    if (Wire.available()) {
        event = Wire.read();
    }

    bool needsUpdate = false;
    if (event > 0) {
        switch (event) {
            case 1:
                tp_remaining -= 5;
                needsUpdate = true;
                break;
            case 2:
                tp_remaining++;
                needsUpdate = true;
                break;
            case 3:
                tp_remaining = 100;
                email_sent_flag = false;
                Serial.println(F(">> TP Reset! Email flag cleared. <<"));
                needsUpdate = true;
                break;
        }
    }
    
    tp_remaining = constrain(tp_remaining, 0, 100);

    if (needsUpdate && tp_remaining != last_tp_level) {
        updateAndDisplayStatus();
        last_tp_level = tp_remaining;
    }

    delay(50);
}

void updateAndDisplayStatus() {
    String emoji = "", dialogue = "";

    if (tp_remaining > 80) {
        emoji = "(^o^)";
        dialogue = "";
    } else if (tp_remaining > 50) {
        emoji = "(^_^;)";
        dialogue = "";
    } else if (tp_remaining > 20) {
        emoji = "(o_o;)";
        dialogue = "";
    } else if (tp_remaining > 0) {
        emoji = "(T_T)";
        dialogue = "";
    } else {
        emoji = "(; ;)";
        dialogue = "";
    }

    if (tp_remaining == 0 && !email_sent_flag) {
        Serial.println("TP is zero. Sending email notification...");
        sendNotificationEmail();
    }

    String remainingStr = String(tp_remaining) + "%";
    while(remainingStr.length() < 8) remainingStr = " " + remainingStr;
    String displayData = emoji + "|" + remainingStr;

    Wire.beginTransmission(ARDUINO_I2C_ADDRESS);
    Wire.write(displayData.c_str());
    Wire.endTransmission();

    Serial.print("TP Remaining: " + String(tp_remaining) + "% -> ");
    Serial.println(dialogue);
}

void sendNotificationEmail() {
    Session_Config config;
    config.server.host_name = SMTP_HOST;
    config.server.port = SMTP_PORT;
    config.login.email = SENDER_EMAIL;
    config.login.password = GMAIL_APP_PASSWORD;
    config.login.user_domain = "";

    SMTP_Message message;
    message.sender.name = SENDER_NAME;
    message.sender.email = SENDER_EMAIL;
    message.subject = "【緊急通知】TPがゼロになりました！";
    message.addRecipient(RECIPIENT_EMAIL, RECIPIENT_EMAIL);
    message.text.content = "デバイスのTP残量が0%になりました。\n至急、補充してください。";
    message.text.charSet = "UTF-8";

    if (!smtp.connect(&config)) {
        Serial.println("Error, SMTP session connection failed.");
        return;
    }
    if (!MailClient.sendMail(&smtp, &message)) {
        Serial.println("Error, Message sending failed.");
    }
}

void smtpCallback(SMTP_Status status) {
    Serial.println(status.info());
    if (status.success()) {
        Serial.println("----------------");
        Serial.println("Email sent successfully!");
        Serial.println("----------------");
        email_sent_flag = true;
    }
}
