#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "SparkFun_AS7265X.h"
#include "Adafruit_VL53L0X.h"
#include <ArduinoJson.h>
#include <SPI.h>
#include <SD.h>

// --- KONFIGURASI JARINGAN ---
const char* ssid = "GEDUNG JTIK";
const char* password = "polsubbersinar"; 
const char* flask_server = "http://10.100.12.136:5000/api/simpan_sensor";

// --- KONFIGURASI PIN ---
const int pinRed = 14;
const int pinYellow = 12;
const int pinGreen = 13;
const int pinButton = 27;   // Pin untuk Push Button
const int pinCS_SD = 15;    // Pin CS untuk MicroSD Adapter

// --- KONFIGURASI OLED ---
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire1, -1);

WebServer server(80);
AS7265X sensorSpektrum;
Adafruit_VL53L0X sensorJarak = Adafruit_VL53L0X();

// --- VARIABEL KONTROL ---
unsigned long waktuTerakhirCek = 0;
bool statusSukses = false;
unsigned long waktuSukses = 0;
int idLokalOtomatis = 1; // Counter ID untuk pemindaian via tombol fisik

// =======================================================================
// FUNGSI INTI: MENGEKSEKUSI PEMINDAIAN & MENYIMPAN DATA (SD CARD + FLASK)
// =======================================================================
int eksekusiPindaiSpektrum(String id_nanas, String titik_pindai) {
  display.clearDisplay();
  display.setTextSize(2);
  display.setCursor(10, 25);
  display.print("MEMINDAI..");
  display.display();

  sensorSpektrum.takeMeasurements();

  float ch_a = sensorSpektrum.getCalibratedA(); float ch_b = sensorSpektrum.getCalibratedB();
  float ch_c = sensorSpektrum.getCalibratedC(); float ch_d = sensorSpektrum.getCalibratedD();
  float ch_e = sensorSpektrum.getCalibratedE(); float ch_f = sensorSpektrum.getCalibratedF();
  float ch_g = sensorSpektrum.getCalibratedG(); float ch_h = sensorSpektrum.getCalibratedH();
  float ch_i = sensorSpektrum.getCalibratedI(); float ch_j = sensorSpektrum.getCalibratedJ();
  float ch_k = sensorSpektrum.getCalibratedK(); float ch_l = sensorSpektrum.getCalibratedL();
  float ch_r = sensorSpektrum.getCalibratedR(); float ch_s = sensorSpektrum.getCalibratedS();
  float ch_t = sensorSpektrum.getCalibratedT(); float ch_u = sensorSpektrum.getCalibratedU();
  float ch_v = sensorSpektrum.getCalibratedV(); float ch_w = sensorSpektrum.getCalibratedW();

  // 1. SIMPAN KE MICROSD
  File dataFile = SD.open("/data_nanas.csv", FILE_APPEND);
  if (dataFile) {
    dataFile.print(id_nanas); dataFile.print(",");
    dataFile.print(titik_pindai); dataFile.print(",");
    dataFile.print(ch_a); dataFile.print(","); dataFile.print(ch_b); dataFile.print(",");
    dataFile.print(ch_c); dataFile.print(","); dataFile.print(ch_d); dataFile.print(",");
    dataFile.print(ch_e); dataFile.print(","); dataFile.print(ch_f); dataFile.print(",");
    dataFile.print(ch_g); dataFile.print(","); dataFile.print(ch_h); dataFile.print(",");
    dataFile.print(ch_i); dataFile.print(","); dataFile.print(ch_j); dataFile.print(",");
    dataFile.print(ch_k); dataFile.print(","); dataFile.print(ch_l); dataFile.print(",");
    dataFile.print(ch_r); dataFile.print(","); dataFile.print(ch_s); dataFile.print(",");
    dataFile.print(ch_t); dataFile.print(","); dataFile.print(ch_u); dataFile.print(",");
    dataFile.print(ch_v); dataFile.print(","); dataFile.println(ch_w);
    dataFile.close();
    Serial.println("=> TERSIMPAN di MicroSD!");
  } else {
    Serial.println("=> GAGAL membuka MicroSD.");
  }

  // 2. KIRIM KE FLASK (Jika Terhubung)
  int httpResponseCode = 200; // Asumsi sukses (bisa beroperasi *offline* murni dengan SD Card)
  if (WiFi.status() == WL_CONNECTED) {
    JsonDocument payload;
    payload["id_nanas"] = id_nanas;     payload["titik_pindai"] = titik_pindai;
    payload["ch_a"] = ch_a; payload["ch_b"] = ch_b; payload["ch_c"] = ch_c;
    payload["ch_d"] = ch_d; payload["ch_e"] = ch_e; payload["ch_f"] = ch_f;
    payload["ch_g"] = ch_g; payload["ch_h"] = ch_h; payload["ch_i"] = ch_i;
    payload["ch_j"] = ch_j; payload["ch_k"] = ch_k; payload["ch_l"] = ch_l;
    payload["ch_r"] = ch_r; payload["ch_s"] = ch_s; payload["ch_t"] = ch_t;
    payload["ch_u"] = ch_u; payload["ch_v"] = ch_v; payload["ch_w"] = ch_w;

    String jsonPayload;
    serializeJson(payload, jsonPayload);

    HTTPClient http;
    http.begin(flask_server);
    http.addHeader("Content-Type", "application/json");
    httpResponseCode = http.POST(jsonPayload);
    http.end();
    
    Serial.print("=> HTTP Response Flask: ");
    Serial.println(httpResponseCode);
  } else {
    Serial.println("=> WiFi terputus. Hanya menyimpan secara lokal di SD Card.");
  }

  // 3. TAMPILAN SUKSES
  if (httpResponseCode == 200) {
    digitalWrite(pinRed, LOW); digitalWrite(pinYellow, LOW); digitalWrite(pinGreen, HIGH);
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(10, 25);
    display.print("SUKSES!");
    display.display();
    statusSukses = true;
    waktuSukses = millis();
  }
  return httpResponseCode;
}


void setup() {
  Serial.begin(115200);
  
  Wire.begin(21, 22);
  Wire1.begin(5, 4);

  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED gagal diinisialisasi");
  } else {
    display.clearDisplay();
    display.setRotation(2);
    display.setTextSize(1);
    display.setTextColor(WHITE);
    display.setCursor(10, 20);
    display.println("Sistem Memulai...");
    display.display();
  }

  pinMode(pinRed, OUTPUT);
  pinMode(pinYellow, OUTPUT);
  pinMode(pinGreen, OUTPUT);
  digitalWrite(pinRed, LOW); digitalWrite(pinYellow, LOW); digitalWrite(pinGreen, LOW);
  
  pinMode(pinButton, INPUT_PULLUP); // Tombol menggunakan pull-up internal

  // INISIALISASI MICROSD
  Serial.print("Inisialisasi SD Card... ");
  if (!SD.begin(pinCS_SD)) {
    Serial.println("GAGAL!");
  } else {
    Serial.println("SIAP!");
    File dataFile = SD.open("/data_nanas.csv", FILE_READ);
    if (!dataFile) { // Jika file belum ada, buat file dan tulis header kolom
      dataFile = SD.open("/data_nanas.csv", FILE_WRITE);
      dataFile.println("id_nanas,titik_pindai,ch_a,ch_b,ch_c,ch_d,ch_e,ch_f,ch_g,ch_h,ch_i,ch_j,ch_k,ch_l,ch_r,ch_s,ch_t,ch_u,ch_v,ch_w");
      dataFile.close();
    } else {
      dataFile.close();
    }
  }

  if (!sensorJarak.begin()) {
    Serial.println("Gagal mendeteksi VL53L0X!");
    while(1);
  }
  if (!sensorSpektrum.begin()) {
    Serial.println("Gagal mendeteksi AS7265X!");
    while(1);
  }
  sensorSpektrum.disableIndicator();

  WiFi.begin(ssid, password);
  Serial.print("Menghubungkan WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  
  Serial.println("\n=========================================");
  Serial.println("Wi-Fi Terhubung!");
  Serial.print("Endpoint: http://"); Serial.print(WiFi.localIP()); Serial.println("/trigger");
  Serial.println("=========================================");

  display.clearDisplay();
  display.setCursor(0, 10);
  display.println("WiFi Terhubung!");
  display.println(WiFi.localIP().toString());
  display.display();
  delay(2000);

  server.on("/trigger", HTTP_OPTIONS, []() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.sendHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
    server.send(204);
  });
  server.on("/trigger", HTTP_POST, handleTrigger);
  server.begin();
}

void loop() {
  server.handleClient();

  if (statusSukses) {
    if (millis() - waktuSukses > 2000) {
      statusSukses = false; 
    }
  } 
  else {
    // --- CEK JIKA TOMBOL FISIK DITEKAN ---
    if (digitalRead(pinButton) == LOW) {
      delay(50); // Debounce untuk mencegah klik ganda
      if (digitalRead(pinButton) == LOW) {
        Serial.println("\n=> TOMBOL FISIK DITEKAN!");
        
        VL53L0X_RangingMeasurementData_t dataJarak;
        sensorJarak.rangingTest(&dataJarak, false);
        int jarak_mm = (dataJarak.RangeStatus != 4) ? dataJarak.RangeMilliMeter : 9999;
        
        if (jarak_mm >= 39 && jarak_mm <= 61) {
          String idGenerate = "Fisik_" + String(idLokalOtomatis);
          eksekusiPindaiSpektrum(idGenerate, "T_Lokal");
          idLokalOtomatis++; // Naikkan nomor untuk pemindaian berikutnya
        } else {
          Serial.println("=> DITOLAK: Jarak tidak pas!");
          display.clearDisplay();
          display.setTextSize(2);
          display.setCursor(10, 25);
          display.print("GAGAL!");
          display.display();
          delay(1000); // Tahan tampilan gagal sesaat
        }
        while(digitalRead(pinButton) == LOW); // Tunggu sampai jari Anda dilepas
      }
    }

    // --- RUTINITAS PEMBACAAN JARAK (OLED & LED) ---
    if (millis() - waktuTerakhirCek > 200) {
      waktuTerakhirCek = millis();
      
      VL53L0X_RangingMeasurementData_t dataJarak;
      sensorJarak.rangingTest(&dataJarak, false);
      
      int jarak_mm = 9999;
      if (dataJarak.RangeStatus != 4) {
        jarak_mm = dataJarak.RangeMilliMeter;
      }

      display.clearDisplay();
      display.setTextSize(2);
      display.setCursor(10, 10);
      display.print("Jarak:");
      
      display.setCursor(10, 35);
      if (jarak_mm == 9999) {
        display.print("Error");
      } else {
        display.print(jarak_mm);
        display.print(" mm");
      }
      display.display();

      if (jarak_mm >= 39 && jarak_mm <= 61) {
        digitalWrite(pinRed, LOW); digitalWrite(pinYellow, HIGH); digitalWrite(pinGreen, LOW);
      } else {
        digitalWrite(pinRed, HIGH); digitalWrite(pinYellow, LOW); digitalWrite(pinGreen, LOW);
      }
    }
  }
}

// --- FUNGSI TRIGGER DARI NEXT.JS (WEB) ---
void handleTrigger() {
  Serial.println("\n=================================");
  Serial.println("=> PERMINTAAN MASUK DARI NEXT.JS!");
  
  server.sendHeader("Access-Control-Allow-Origin", "*");
  
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"error\":\"Data kosong\"}");
    return;
  }

  VL53L0X_RangingMeasurementData_t dataJarak;
  sensorJarak.rangingTest(&dataJarak, false);
  int jarak_mm = (dataJarak.RangeStatus != 4) ? dataJarak.RangeMilliMeter : 9999;

  Serial.print("=> Jarak fisik saat web diklik: "); Serial.print(jarak_mm); Serial.println(" mm");

  if (jarak_mm < 39 || jarak_mm > 61) {
    server.send(400, "application/json", "{\"status\":\"error\", \"pesan\":\"Jarak harus pas!\"}");
    return;
  }

  String body = server.arg("plain");
  JsonDocument inputDoc;
  deserializeJson(inputDoc, body);
  String id_nanas = inputDoc["id_nanas"];
  String titik_pindai = inputDoc["titik_pindai"];

  // Memanggil fungsi inti
  int httpCode = eksekusiPindaiSpektrum(id_nanas, titik_pindai);

  if (httpCode == 200) {
    server.send(200, "application/json", "{\"status\":\"sukses\"}");
  } else {
    server.send(500, "application/json", "{\"status\":\"error\", \"pesan\":\"Gagal mencapai Flask\"}");
  }
}