/*
  NECTA AS7265X - Akuisisi M0-M5 v4-M5
  =====================================
  Fokus versi ini:
  - Gain dikunci 64x untuk M0-M5
  - Integration cycles dikunci 49 (~140 ms)
  - RAW + calibrated 18 kanal disimpan terpisah
  - RAW 485 dibaca 3x dan dipilih tanpa menghapus nilai asli
  - /trigger5 menghitung CV otomatis dari 5 technical repeats
  - M4-M5 memakai QC real-time (ACCEPT / WATCH_REPEAT / FLAG)
  - M5 menyimpan reference_role untuk baseline, drift check, recalibration, final audit
  - Drift M5 dihitung oleh Flask/Supabase agar baseline tetap tersedia walau ESP32 restart

  Endpoint ESP32:
    POST /trigger
    POST /trigger5
    POST /diagnostic485
    GET  /config

  M5 reference_role:
    BASELINE_DARK
    BASELINE_WHITE
    DRIFT_CHECK_WHITE
    RECAL_DARK
    RECAL_WHITE
    FINAL_WHITE
    FINAL_DARK
    NONE (untuk SAMPLE)
*/

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
#include <Preferences.h>
#include <ESPmDNS.h>

// ======================================================
// JARINGAN DINAMIS (Dapat diubah via SD Card / Web UI)
// ======================================================
Preferences prefs;
String wifi_ssid = "Oke";
String wifi_password = "dimaskece";
String flask_server = "http://10.200.135.209:5000/api/simpan_sensor";
float lastBrixPred = -1.0f; // Nilai prediksi kemanisan Brix terakhir dari model PLSR Flask

// ======================================================
// PIN
// ======================================================
const int pinRed = 14;
const int pinYellow = 12;
const int pinGreen = 13;
const int pinButton = 27;
const int pinCS_SD = 15;

// ======================================================
// OLED
// ======================================================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire1, -1);

// ======================================================
// SENSOR / SERVER
// ======================================================
WebServer server(80);
AS7265X sensorSpektrum;
Adafruit_VL53L0X sensorJarak = Adafruit_VL53L0X();

// ======================================================
// SETTING PENELITIAN - DIKUNCI
// ======================================================
const uint8_t GAIN_X = 64;
const uint8_t GAIN_ENUM = AS7265X_GAIN_64X;
const uint8_t INTEGRATION_CYCLES = 49;
const float INTEGRATION_MS_EST = (INTEGRATION_CYCLES + 1) * 2.8f;
const float LED_CURRENT_MA = 12.5f;
const char* FIRMWARE_VERSION = "NECTA-M0M5-v4.0-M5";

// Threshold pilot penelitian, bukan standar resmi AS7265X
const float QC_ACCEPT_MAX = 5.0f;
const float QC_WATCH_MAX = 10.0f;

// ======================================================
// RUNTIME
// ======================================================
unsigned long waktuTerakhirCek = 0;
bool statusSukses = false;
unsigned long waktuSukses = 0;
bool sdReady = false;

// ======================================================
// MAPPING 18 KANAL
// ======================================================
const char* wavelengthNames[18] = {
  "410", "435", "460", "485", "510", "535",
  "560", "585", "610", "645", "680", "705",
  "730", "760", "810", "860", "900", "940"
};

struct Spectrum18 {
  uint16_t raw[18];
  float cal[18];
  uint16_t raw485_check2;
  uint16_t raw485_check3;
  uint16_t raw485_selected;
  String raw485_source;
  String raw485_status;
};

struct ScanCapture {
  Spectrum18 spectrum;
  int distanceMM;
  unsigned long uptimeMs;
};

struct QCResult {
  float cv[18];
  float medianCv;
  float maxCv;
  int validChannels;
  int channelsOver10;
  bool applied;
  String status;
};

// ======================================================
// VALIDASI / HELPER
// ======================================================
bool validModel(const String &model) {
  return model == "M0" || model == "M1" || model == "M2" ||
         model == "M3" || model == "M4" || model == "M5";
}

bool validMeasurementType(const String &type) {
  return type == "SAMPLE" || type == "WHITE" || type == "DARK";
}

bool validReferenceRole(const String &role) {
  return role == "NONE" ||
         role == "BASELINE_DARK" || role == "BASELINE_WHITE" ||
         role == "DRIFT_CHECK_WHITE" ||
         role == "RECAL_DARK" || role == "RECAL_WHITE" ||
         role == "FINAL_WHITE" || role == "FINAL_DARK";
}

bool validM5RoleForType(const String &measurementType, const String &role) {
  if (measurementType == "SAMPLE") return role == "NONE";
  if (measurementType == "DARK") {
    return role == "BASELINE_DARK" || role == "RECAL_DARK" || role == "FINAL_DARK";
  }
  if (measurementType == "WHITE") {
    return role == "BASELINE_WHITE" || role == "DRIFT_CHECK_WHITE" ||
           role == "RECAL_WHITE" || role == "FINAL_WHITE";
  }
  return false;
}

String normalizePosition(String position) {
  position.trim();
  if (position.length() == 0) return "REFERENCE";
  if (position.equalsIgnoreCase("Atas")) return "Atas";
  if (position.equalsIgnoreCase("Tengah")) return "Tengah";
  if (position.equalsIgnoreCase("Bawah")) return "Bawah";
  if (position.equalsIgnoreCase("Reference")) return "REFERENCE";
  return position;
}

String illuminationMode(const String &model, const String &measurementType) {
  if (measurementType == "DARK") return "DARK";
  if (model == "M3" || model == "M4" || model == "M5") return "ONBOARD";
  return "AMBIENT";
}

bool shouldBulbsBeOn(const String &model, const String &measurementType) {
  if (measurementType == "DARK") return false;
  return model == "M3" || model == "M4" || model == "M5";
}

void allBulbsOff() {
  sensorSpektrum.disableBulb(AS7265x_LED_WHITE);
  sensorSpektrum.disableBulb(AS7265x_LED_IR);
  sensorSpektrum.disableBulb(AS7265x_LED_UV);
}

int readDistanceMM() {
  VL53L0X_RangingMeasurementData_t measure;
  sensorJarak.rangingTest(&measure, false);
  if (measure.RangeStatus == 4) return -1;
  int d = (int)measure.RangeMilliMeter;
  if (d <= 0) return -1;
  return d;
}

String autoReferenceId(
  const String &sessionId,
  const String &model,
  const String &measurementType,
  const String &referenceRole,
  const String &fruitId
) {
  String prefix = measurementType == "DARK" ? "D" : "W";
  String suffix = fruitId.length() ? fruitId : String(millis());
  return prefix + "-" + sessionId + "-" + model + "-" + referenceRole + "-" + suffix;
}

String makeQCGroupId(
  const String &sessionId,
  const String &model,
  const String &fruitId,
  const String &position
) {
  String f = fruitId.length() ? fruitId : "REF";
  String p = position.length() ? position : "REFERENCE";
  return "Q-" + sessionId + "-" + model + "-" + f + "-" + p + "-" + String(millis());
}

// ======================================================
// BACA SPEKTRUM
// ======================================================
void readSpectrum(Spectrum18 &s) {
  s.raw[0]  = sensorSpektrum.getA();
  s.raw[1]  = sensorSpektrum.getB();
  s.raw[2]  = sensorSpektrum.getC();
  s.raw[3]  = sensorSpektrum.getD();
  s.raw[4]  = sensorSpektrum.getE();
  s.raw[5]  = sensorSpektrum.getF();
  s.raw[6]  = sensorSpektrum.getG();
  s.raw[7]  = sensorSpektrum.getH();
  s.raw[8]  = sensorSpektrum.getR();
  s.raw[9]  = sensorSpektrum.getI();
  s.raw[10] = sensorSpektrum.getS();
  s.raw[11] = sensorSpektrum.getJ();
  s.raw[12] = sensorSpektrum.getT();
  s.raw[13] = sensorSpektrum.getU();
  s.raw[14] = sensorSpektrum.getV();
  s.raw[15] = sensorSpektrum.getW();
  s.raw[16] = sensorSpektrum.getK();
  s.raw[17] = sensorSpektrum.getL();

  s.cal[0]  = sensorSpektrum.getCalibratedA();
  s.cal[1]  = sensorSpektrum.getCalibratedB();
  s.cal[2]  = sensorSpektrum.getCalibratedC();
  s.cal[3]  = sensorSpektrum.getCalibratedD();
  s.cal[4]  = sensorSpektrum.getCalibratedE();
  s.cal[5]  = sensorSpektrum.getCalibratedF();
  s.cal[6]  = sensorSpektrum.getCalibratedG();
  s.cal[7]  = sensorSpektrum.getCalibratedH();
  s.cal[8]  = sensorSpektrum.getCalibratedR();
  s.cal[9]  = sensorSpektrum.getCalibratedI();
  s.cal[10] = sensorSpektrum.getCalibratedS();
  s.cal[11] = sensorSpektrum.getCalibratedJ();
  s.cal[12] = sensorSpektrum.getCalibratedT();
  s.cal[13] = sensorSpektrum.getCalibratedU();
  s.cal[14] = sensorSpektrum.getCalibratedV();
  s.cal[15] = sensorSpektrum.getCalibratedW();
  s.cal[16] = sensorSpektrum.getCalibratedK();
  s.cal[17] = sensorSpektrum.getCalibratedL();

  // Diagnostik kanal D / 485 nm. Nilai pertama tetap dipertahankan.
  delay(5);
  s.raw485_check2 = sensorSpektrum.getD();
  delay(5);
  s.raw485_check3 = sensorSpektrum.getD();

  if (s.raw[3] > 0) {
    s.raw485_selected = s.raw[3];
    s.raw485_source = "FIRST";
    s.raw485_status = "OK";
  } else if (s.raw485_check2 > 0) {
    s.raw485_selected = s.raw485_check2;
    s.raw485_source = "CHECK2";
    s.raw485_status = "INTERMITTENT_READ";
  } else if (s.raw485_check3 > 0) {
    s.raw485_selected = s.raw485_check3;
    s.raw485_source = "CHECK3";
    s.raw485_status = "INTERMITTENT_READ";
  } else if (s.cal[3] > 0.0f) {
    s.raw485_selected = 0;
    s.raw485_source = "NONE";
    s.raw485_status = "RAW_ZERO_CAL_NONZERO";
  } else {
    s.raw485_selected = 0;
    s.raw485_source = "NONE";
    s.raw485_status = "ZERO";
  }
}

// ======================================================
// QC CV 5 TECHNICAL REPEATS
// ======================================================
float sampleStdDev(float values[], int n, float mean) {
  if (n < 2) return 0.0f;
  float sumSq = 0.0f;
  for (int i = 0; i < n; i++) {
    float d = values[i] - mean;
    sumSq += d * d;
  }
  return sqrt(sumSq / (n - 1));
}

float medianOfArray(float values[], int n) {
  if (n <= 0) return -1.0f;
  for (int i = 1; i < n; i++) {
    float key = values[i];
    int j = i - 1;
    while (j >= 0 && values[j] > key) {
      values[j + 1] = values[j];
      j--;
    }
    values[j + 1] = key;
  }
  if (n % 2 == 1) return values[n / 2];
  return (values[n / 2 - 1] + values[n / 2]) / 2.0f;
}

QCResult calculateQC(
  ScanCapture captures[5],
  const String &model,
  const String &measurementType
) {
  QCResult q;
  q.medianCv = -1.0f;
  q.maxCv = -1.0f;
  q.validChannels = 0;
  q.channelsOver10 = 0;
  q.applied = false;
  q.status = "REFERENCE";

  float validCvs[18];

  for (int ch = 0; ch < 18; ch++) {
    float values[5];
    float sum = 0.0f;
    for (int r = 0; r < 5; r++) {
      values[r] = captures[r].spectrum.cal[ch];
      sum += values[r];
    }

    float mean = sum / 5.0f;
    if (fabs(mean) < 0.001f) {
      q.cv[ch] = -1.0f;
      continue;
    }

    float sd = sampleStdDev(values, 5, mean);
    float cv = (sd / fabs(mean)) * 100.0f;
    q.cv[ch] = cv;
    validCvs[q.validChannels++] = cv;
    if (cv > QC_WATCH_MAX) q.channelsOver10++;
    if (q.maxCv < 0.0f || cv > q.maxCv) q.maxCv = cv;
  }

  if (q.validChannels > 0) {
    q.medianCv = medianOfArray(validCvs, q.validChannels);
  }

  if (measurementType != "SAMPLE") {
    q.status = q.validChannels == 0 ? "LOW_SIGNAL" : "REFERENCE";
    return q;
  }

  if (!(model == "M4" || model == "M5")) {
    if (q.validChannels == 0) {
      q.status = (model == "M0" || model == "M1") ? "AMBIENT_NO_LED" : "LOW_SIGNAL";
    } else {
      q.status = "POSTHOC_ONLY";
    }
    return q;
  }

  q.applied = true;
  if (q.validChannels == 0 || q.medianCv < 0.0f) q.status = "LOW_SIGNAL";
  else if (q.medianCv <= QC_ACCEPT_MAX) q.status = "ACCEPT";
  else if (q.medianCv <= QC_WATCH_MAX) q.status = "WATCH_REPEAT";
  else q.status = "FLAG";

  return q;
}

// ======================================================
// OLED
// ======================================================
void showMessage(const String &line1, const String &line2 = "") {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 8);
  display.println(line1);
  display.setTextSize(2);
  display.setCursor(0, 30);
  display.println(line2);
  display.display();
}

void showSuccess(const String &line1, const String &line2) {
  digitalWrite(pinRed, LOW);
  digitalWrite(pinYellow, LOW);
  digitalWrite(pinGreen, HIGH);
  showMessage(line1, line2);
  statusSukses = true;
  waktuSukses = millis();
}

// ======================================================
// MICROSD
// ======================================================
void initSD() {
  sdReady = SD.begin(pinCS_SD);
  if (!sdReady) {
    Serial.println("SD tidak tersedia; Supabase tetap dapat digunakan jika WiFi aktif.");
    return;
  }

  if (!SD.exists("/data_nanas_m0_m5_v4.csv")) {
    File f = SD.open("/data_nanas_m0_m5_v4.csv", FILE_WRITE);
    if (f) {
      f.print("firmware_version,session_id,id_nanas,model,measurement_type,position,repeat_no,");
      f.print("reference_id,reference_role,dark_reference_id,white_reference_id,");
      f.print("gain_x,integration_cycles,integration_ms_est,measurement_mode,");
      f.print("illumination_mode,led_white,led_ir,led_uv,led_current_ma,distance_mm,uptime_ms");
      for (int i = 0; i < 18; i++) { f.print(",raw_"); f.print(wavelengthNames[i]); }
      for (int i = 0; i < 18; i++) { f.print(",cal_"); f.print(wavelengthNames[i]); }
      f.print(",raw_485_check2,raw_485_check3,raw_485_selected,raw_485_source,raw_485_status");
      f.print(",qc_group_id,qc_median_cv,qc_max_cv,qc_valid_channels,qc_channels_over_10,qc_applied,qc_status");
      f.println();
      f.close();
    }
  }

  if (!SD.exists("/diag485_v4.csv")) {
    File f = SD.open("/diag485_v4.csv", FILE_WRITE);
    if (f) {
      f.println("uptime_ms,index,distance_mm,raw_485,raw_485_check2,raw_485_check3,raw_485_selected,raw_485_source,cal_485,status");
      f.close();
    }
  }
}

void loadDynamicConfig() {
  // 1. Muat dari Preferences NVS Flash (jika pernah diset via Web UI)
  prefs.begin("necta", true);
  String savedFlask = prefs.getString("flask_url", "");
  String savedSsid = prefs.getString("wifi_ssid", "");
  String savedPass = prefs.getString("wifi_pass", "");
  prefs.end();

  if (savedFlask.length() > 0 && savedFlask.indexOf("10.111.0.37") < 0) flask_server = savedFlask;
  if (savedSsid.length() > 0) wifi_ssid = savedSsid;
  if (savedPass.length() > 0) wifi_password = savedPass;

  // 2. Muat dari SD Card /config.txt jika file tersebut ada di SD Card
  if (sdReady && SD.exists("/config.txt")) {
    File f = SD.open("/config.txt", FILE_READ);
    if (f) {
      while (f.available()) {
        String line = f.readStringUntil('\n');
        line.trim();
        if (line.startsWith("ssid=")) wifi_ssid = line.substring(5);
        else if (line.startsWith("password=")) wifi_password = line.substring(9);
        else if (line.startsWith("flask_server=") && line.indexOf("10.111.0.37") < 0) flask_server = line.substring(13);
      }
      f.close();
    }
  }

  // Juga buatkan file template /config.txt di SD Card jika belum ada
  if (sdReady && !SD.exists("/config.txt")) {
    File f = SD.open("/config.txt", FILE_WRITE);
    if (f) {
      f.print("ssid="); f.println(wifi_ssid);
      f.print("password="); f.println(wifi_password);
      f.print("flask_server="); f.println(flask_server);
      f.close();
    }
  }
}

bool saveToSD(
  const String &sessionId,
  const String &fruitId,
  const String &model,
  const String &measurementType,
  const String &position,
  int repeatNo,
  const String &referenceId,
  const String &referenceRole,
  const String &darkReferenceId,
  const String &whiteReferenceId,
  bool bulbsOn,
  const ScanCapture &capture,
  const String &qcGroupId,
  const QCResult &qc
) {
  if (!sdReady) return true;
  File f = SD.open("/data_nanas_m0_m5_v4.csv", FILE_APPEND);
  if (!f) return true;

  f.print(FIRMWARE_VERSION); f.print(",");
  f.print(sessionId); f.print(",");
  f.print(fruitId); f.print(",");
  f.print(model); f.print(",");
  f.print(measurementType); f.print(",");
  f.print(position); f.print(",");
  f.print(repeatNo); f.print(",");
  f.print(referenceId); f.print(",");
  f.print(referenceRole); f.print(",");
  f.print(darkReferenceId); f.print(",");
  f.print(whiteReferenceId); f.print(",");
  f.print(GAIN_X); f.print(",");
  f.print(INTEGRATION_CYCLES); f.print(",");
  f.print(INTEGRATION_MS_EST, 1); f.print(",");
  f.print("6CHAN_ONE_SHOT"); f.print(",");
  f.print(illuminationMode(model, measurementType)); f.print(",");
  f.print(bulbsOn ? 1 : 0); f.print(",");
  f.print(bulbsOn ? 1 : 0); f.print(",");
  f.print(bulbsOn ? 1 : 0); f.print(",");
  f.print(LED_CURRENT_MA, 1); f.print(",");
  f.print(capture.distanceMM); f.print(",");
  f.print(capture.uptimeMs);

  for (int i = 0; i < 18; i++) { f.print(","); f.print(capture.spectrum.raw[i]); }
  for (int i = 0; i < 18; i++) { f.print(","); f.print(capture.spectrum.cal[i], 6); }

  f.print(","); f.print(capture.spectrum.raw485_check2);
  f.print(","); f.print(capture.spectrum.raw485_check3);
  f.print(","); f.print(capture.spectrum.raw485_selected);
  f.print(","); f.print(capture.spectrum.raw485_source);
  f.print(","); f.print(capture.spectrum.raw485_status);
  f.print(","); f.print(qcGroupId);
  f.print(","); if (qc.medianCv >= 0.0f) f.print(qc.medianCv, 4);
  f.print(","); if (qc.maxCv >= 0.0f) f.print(qc.maxCv, 4);
  f.print(","); f.print(qc.validChannels);
  f.print(","); f.print(qc.channelsOver10);
  f.print(","); f.print(qc.applied ? 1 : 0);
  f.print(","); f.print(qc.status);
  f.println();
  f.close();
  return true;
}

// ======================================================
// FLASK
// ======================================================
String getBatchFlaskUrl() {
  String url = flask_server;
  url.trim();
  if (url.endsWith("/api/simpan_sensor")) {
    url = url.substring(0, url.length() - 18) + "/api/simpan_sensor_batch";
  } else if (!url.endsWith("/api/simpan_sensor_batch")) {
    if (url.endsWith("/")) url = url.substring(0, url.length() - 1);
    url += "/api/simpan_sensor_batch";
  }
  return url;
}

int sendToFlask(
  const String &sessionId,
  const String &fruitId,
  const String &model,
  const String &measurementType,
  const String &position,
  int repeatNo,
  const String &referenceId,
  const String &referenceRole,
  const String &darkReferenceId,
  const String &whiteReferenceId,
  bool bulbsOn,
  const ScanCapture &capture,
  const String &qcGroupId,
  const QCResult &qc
) {
  if (WiFi.status() != WL_CONNECTED) return 0;

  JsonDocument payload;
  payload["firmware_version"] = FIRMWARE_VERSION;
  payload["session_id"] = sessionId;
  payload["id_nanas"] = fruitId;
  payload["model"] = model;
  payload["measurement_type"] = measurementType;
  payload["position"] = position;
  payload["repeat_no"] = repeatNo;
  payload["reference_id"] = referenceId;
  payload["reference_role"] = referenceRole;
  payload["dark_reference_id"] = darkReferenceId;
  payload["white_reference_id"] = whiteReferenceId;
  payload["gain_x"] = GAIN_X;
  payload["integration_cycles"] = INTEGRATION_CYCLES;
  payload["integration_ms_est"] = INTEGRATION_MS_EST;
  payload["measurement_mode"] = "6CHAN_ONE_SHOT";
  payload["illumination_mode"] = illuminationMode(model, measurementType);
  payload["led_white"] = bulbsOn;
  payload["led_ir"] = bulbsOn;
  payload["led_uv"] = bulbsOn;
  payload["led_current_ma"] = LED_CURRENT_MA;
  if (capture.distanceMM > 0) payload["distance_mm"] = capture.distanceMM;
  else payload["distance_mm"] = nullptr;
  payload["uptime_ms"] = capture.uptimeMs;

  for (int i = 0; i < 18; i++) {
    payload["raw_" + String(wavelengthNames[i])] = capture.spectrum.raw[i];
    payload["cal_" + String(wavelengthNames[i])] = capture.spectrum.cal[i];
  }

  payload["raw_485_check2"] = capture.spectrum.raw485_check2;
  payload["raw_485_check3"] = capture.spectrum.raw485_check3;
  payload["raw_485_selected"] = capture.spectrum.raw485_selected;
  payload["raw_485_source"] = capture.spectrum.raw485_source;
  payload["raw_485_status"] = capture.spectrum.raw485_status;
  payload["qc_group_id"] = qcGroupId;
  if (qc.medianCv >= 0.0f) payload["qc_median_cv"] = qc.medianCv;
  else payload["qc_median_cv"] = nullptr;
  if (qc.maxCv >= 0.0f) payload["qc_max_cv"] = qc.maxCv;
  else payload["qc_max_cv"] = nullptr;
  payload["qc_valid_channels"] = qc.validChannels;
  payload["qc_channels_over_10"] = qc.channelsOver10;
  payload["qc_applied"] = qc.applied;
  payload["qc_status"] = qc.status;

  String body;
  serializeJson(payload, body);

  HTTPClient http;
  http.begin(flask_server);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  lastBrixPred = -1.0f;
  if (code > 0) {
    String resp = http.getString();
    Serial.print("Flask HTTP "); Serial.print(code); Serial.print(": ");
    Serial.println(resp);
    JsonDocument respDoc;
    if (!deserializeJson(respDoc, resp) && respDoc["prediksi_brix"].is<float>()) {
      lastBrixPred = respDoc["prediksi_brix"].as<float>();
    }
  }
  http.end();
  return code;
}

int sendBatchToFlask(
  const String &sessionId,
  const String &fruitId,
  const String &model,
  const String &measurementType,
  const String &position,
  const String &referenceId,
  const String &referenceRole,
  const String &darkReferenceId,
  const String &whiteReferenceId,
  bool bulbsOn,
  const ScanCapture captures[5],
  const String &qcGroupId,
  const QCResult &qc
) {
  if (WiFi.status() != WL_CONNECTED) return 0;

  JsonDocument doc;
  JsonArray arr = doc["measurements"].to<JsonArray>();

  for (int r = 0; r < 5; r++) {
    JsonObject item = arr.add<JsonObject>();
    item["firmware_version"] = FIRMWARE_VERSION;
    item["session_id"] = sessionId;
    item["id_nanas"] = fruitId;
    item["model"] = model;
    item["measurement_type"] = measurementType;
    item["position"] = position;
    item["repeat_no"] = r + 1;
    item["reference_id"] = referenceId;
    item["reference_role"] = referenceRole;
    item["dark_reference_id"] = darkReferenceId;
    item["white_reference_id"] = whiteReferenceId;
    item["gain_x"] = GAIN_X;
    item["integration_cycles"] = INTEGRATION_CYCLES;
    item["integration_ms_est"] = INTEGRATION_MS_EST;
    item["measurement_mode"] = "6CHAN_ONE_SHOT";
    item["illumination_mode"] = illuminationMode(model, measurementType);
    item["led_white"] = bulbsOn;
    item["led_ir"] = bulbsOn;
    item["led_uv"] = bulbsOn;
    item["led_current_ma"] = LED_CURRENT_MA;
    if (captures[r].distanceMM > 0) item["distance_mm"] = captures[r].distanceMM;
    else item["distance_mm"] = nullptr;
    item["uptime_ms"] = captures[r].uptimeMs;

    for (int i = 0; i < 18; i++) {
      item["raw_" + String(wavelengthNames[i])] = captures[r].spectrum.raw[i];
      item["cal_" + String(wavelengthNames[i])] = captures[r].spectrum.cal[i];
    }

    item["raw_485_check2"] = captures[r].spectrum.raw485_check2;
    item["raw_485_check3"] = captures[r].spectrum.raw485_check3;
    item["raw_485_selected"] = captures[r].spectrum.raw485_selected;
    item["raw_485_source"] = captures[r].spectrum.raw485_source;
    item["raw_485_status"] = captures[r].spectrum.raw485_status;
    item["qc_group_id"] = qcGroupId;
    if (qc.medianCv >= 0.0f) item["qc_median_cv"] = qc.medianCv;
    else item["qc_median_cv"] = nullptr;
    if (qc.maxCv >= 0.0f) item["qc_max_cv"] = qc.maxCv;
    else item["qc_max_cv"] = nullptr;
    item["qc_valid_channels"] = qc.validChannels;
    item["qc_channels_over_10"] = qc.channelsOver10;
    item["qc_applied"] = qc.applied;
    item["qc_status"] = qc.status;
  }

  String body;
  serializeJson(doc, body);

  HTTPClient http;
  String targetUrl = getBatchFlaskUrl();
  http.begin(targetUrl);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(4000);

  int code = http.POST(body);
  lastBrixPred = -1.0f;
  if (code > 0) {
    String resp = http.getString();
    Serial.print("Batch Flask HTTP "); Serial.print(code); Serial.print(": ");
    Serial.println(resp);
    JsonDocument respDoc;
    if (!deserializeJson(respDoc, resp) && respDoc["prediksi_brix"].is<float>()) {
      lastBrixPred = respDoc["prediksi_brix"].as<float>();
    }
  } else {
    Serial.print("Batch Flask HTTP Error: ");
    Serial.println(http.errorToString(code));
  }
  http.end();
  return code;
}

// ======================================================
// CORS
// ======================================================
void sendCors() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

void handleOptions() {
  sendCors();
  server.send(204);
}

// ======================================================
// CAPTURE SATU MEASUREMENT
// ======================================================
ScanCapture captureOne(const String &model, const String &measurementType) {
  ScanCapture c;
  bool bulbsOn = shouldBulbsBeOn(model, measurementType);
  if (bulbsOn) {
    sensorSpektrum.enableBulb(AS7265x_LED_WHITE);
    sensorSpektrum.enableBulb(AS7265x_LED_IR);
    sensorSpektrum.enableBulb(AS7265x_LED_UV);
    sensorSpektrum.takeMeasurementsWithBulb();
  } else {
    allBulbsOff();
    sensorSpektrum.takeMeasurements();
  }
  readSpectrum(c.spectrum);
  allBulbsOff();
  c.distanceMM = readDistanceMM();
  c.uptimeMs = millis();
  return c;
}

QCResult singleScanQC() {
  QCResult q;
  for (int i = 0; i < 18; i++) q.cv[i] = -1.0f;
  q.medianCv = -1.0f;
  q.maxCv = -1.0f;
  q.validChannels = 0;
  q.channelsOver10 = 0;
  q.applied = false;
  q.status = "SINGLE_SCAN";
  return q;
}

// ======================================================
// VALIDASI REQUEST UMUM
// ======================================================
bool validateRequestFields(
  const String &model,
  const String &measurementType,
  String &position,
  String &referenceRole,
  const String &fruitId,
  String &error
) {
  if (!validModel(model)) { error = "model harus M0-M5"; return false; }
  if (!validMeasurementType(measurementType)) { error = "measurement_type invalid"; return false; }
  if (!validReferenceRole(referenceRole)) { error = "reference_role invalid"; return false; }

  position = normalizePosition(position);

  if (measurementType == "SAMPLE") {
    if (fruitId.length() == 0) { error = "id_nanas wajib untuk SAMPLE"; return false; }
    if (!(position == "Atas" || position == "Tengah" || position == "Bawah")) {
      error = "position SAMPLE harus Atas/Tengah/Bawah";
      return false;
    }
  } else {
    position = "REFERENCE";
  }

  if (model == "M5") {
    if (!validM5RoleForType(measurementType, referenceRole)) {
      error = "reference_role tidak sesuai measurement_type M5";
      return false;
    }
  } else {
    // Mode selain M5 tidak memakai reference_role khusus drift.
    referenceRole = "NONE";
  }

  return true;
}

// ======================================================
// /trigger - SATU SCAN
// ======================================================
void handleTrigger() {
  sendCors();
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"Body kosong\"}");
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, server.arg("plain"))) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"JSON invalid\"}");
    return;
  }

  String sessionId = doc["session_id"] | "S01";
  String fruitId = doc["id_nanas"] | "";
  String model = doc["model"] | "";
  String measurementType = doc["measurement_type"] | "SAMPLE";
  measurementType.toUpperCase();
  String position = doc["position"] | "";
  String referenceRole = doc["reference_role"] | "NONE";
  referenceRole.toUpperCase();
  String referenceId = doc["reference_id"] | "";
  String darkReferenceId = doc["dark_reference_id"] | "";
  String whiteReferenceId = doc["white_reference_id"] | "";
  int repeatNo = doc["repeat_no"] | 1;

  if (repeatNo < 1 || repeatNo > 5) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"repeat_no 1-5\"}");
    return;
  }

  String error;
  if (!validateRequestFields(model, measurementType, position, referenceRole, fruitId, error)) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"" + error + "\"}");
    return;
  }

  if (measurementType != "SAMPLE" && referenceId.length() == 0) {
    referenceId = autoReferenceId(sessionId, model, measurementType, referenceRole, fruitId);
  }

  ScanCapture c = captureOne(model, measurementType);
  QCResult qc = singleScanQC();
  String qcGroupId = "";
  bool bulbsOn = shouldBulbsBeOn(model, measurementType);

  bool sdOK = saveToSD(
    sessionId, fruitId, model, measurementType, position, repeatNo,
    referenceId, referenceRole, darkReferenceId, whiteReferenceId,
    bulbsOn, c, qcGroupId, qc
  );

  int flaskCode = sendToFlask(
    sessionId, fruitId, model, measurementType, position, repeatNo,
    referenceId, referenceRole, darkReferenceId, whiteReferenceId,
    bulbsOn, c, qcGroupId, qc
  );

  if (measurementType == "SAMPLE" && lastBrixPred > 0.0f) {
    showSuccess(position + " BRIX", String(lastBrixPred, 1) + " Bx");
  } else {
    showSuccess("SCAN OK", "R" + String(repeatNo));
  }

  JsonDocument response;
  response["status"] = (sdOK || flaskCode == 200 || flaskCode == 201) ? "sukses" : "error";
  if (lastBrixPred > 0.0f) {
    response["prediksi_brix"] = lastBrixPred;
  }
  response["reference_id"] = referenceId;
  response["repeat_no"] = repeatNo;
  response["raw_485_selected"] = c.spectrum.raw485_selected;
  response["raw_485_status"] = c.spectrum.raw485_status;
  String out; serializeJson(response, out);
  server.send(200, "application/json", out);
}

// ======================================================
// /trigger5 - 5 SCAN + QC (BATCH OPTIMIZED)
// ======================================================
void handleTrigger5() {
  sendCors();
  if (!server.hasArg("plain")) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"Body kosong\"}");
    return;
  }

  JsonDocument doc;
  if (deserializeJson(doc, server.arg("plain"))) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"JSON invalid\"}");
    return;
  }

  String sessionId = doc["session_id"] | "S01";
  String fruitId = doc["id_nanas"] | "";
  String model = doc["model"] | "";
  String measurementType = doc["measurement_type"] | "SAMPLE";
  measurementType.toUpperCase();
  String position = doc["position"] | "";
  String referenceRole = doc["reference_role"] | "NONE";
  referenceRole.toUpperCase();
  String referenceId = doc["reference_id"] | "";
  String darkReferenceId = doc["dark_reference_id"] | "";
  String whiteReferenceId = doc["white_reference_id"] | "";

  String error;
  if (!validateRequestFields(model, measurementType, position, referenceRole, fruitId, error)) {
    server.send(400, "application/json", "{\"status\":\"error\",\"pesan\":\"" + error + "\"}");
    return;
  }

  if (measurementType != "SAMPLE" && referenceId.length() == 0) {
    referenceId = autoReferenceId(sessionId, model, measurementType, referenceRole, fruitId);
  }

  String qcGroupId = makeQCGroupId(sessionId, model, fruitId, position);
  ScanCapture captures[5];

  for (int r = 0; r < 5; r++) {
    showMessage(model + " " + measurementType, position + " R" + String(r + 1));
    captures[r] = captureOne(model, measurementType);
    delay(20);
  }

  QCResult qc = calculateQC(captures, model, measurementType);
  bool bulbsOn = shouldBulbsBeOn(model, measurementType);

  if (sdReady) {
    for (int r = 0; r < 5; r++) {
      saveToSD(
        sessionId, fruitId, model, measurementType, position, r + 1,
        referenceId, referenceRole, darkReferenceId, whiteReferenceId,
        bulbsOn, captures[r], qcGroupId, qc
      );
    }
  }

  int flaskCode = sendBatchToFlask(
    sessionId, fruitId, model, measurementType, position,
    referenceId, referenceRole, darkReferenceId, whiteReferenceId,
    bulbsOn, captures, qcGroupId, qc
  );

  if (measurementType == "SAMPLE" && lastBrixPred > 0.0f) {
    showSuccess(position + " BRIX", String(lastBrixPred, 1) + " Bx");
  } else if (measurementType == "SAMPLE" && (model == "M4" || model == "M5")) {
    showSuccess("QC " + qc.status, String(qc.medianCv, 1) + "%");
  } else {
    showSuccess("SCAN 5x OK", qc.medianCv >= 0 ? String(qc.medianCv, 1) + "%" : "-");
  }

  JsonDocument response;
  response["status"] = (flaskCode == 200 || flaskCode == 201 || flaskCode == 0) ? "selesai" : "parsial";
  response["berhasil"] = 5;
  response["total"] = 5;
  response["flask_code"] = flaskCode;
  if (lastBrixPred > 0.0f) {
    response["prediksi_brix"] = lastBrixPred;
  }
  response["reference_id"] = referenceId;
  response["reference_role"] = referenceRole;
  response["qc_group_id"] = qcGroupId;
  if (qc.medianCv >= 0.0f) response["qc_median_cv"] = qc.medianCv;
  else response["qc_median_cv"] = nullptr;
  if (qc.maxCv >= 0.0f) response["qc_max_cv"] = qc.maxCv;
  else response["qc_max_cv"] = nullptr;
  response["qc_valid_channels"] = qc.validChannels;
  response["qc_channels_over_10"] = qc.channelsOver10;
  response["qc_applied"] = qc.applied;
  response["qc_status"] = qc.status;
  response["repeat_recommended"] =
    qc.applied && (qc.status == "WATCH_REPEAT" || qc.status == "FLAG" || qc.status == "LOW_SIGNAL");

  String out; serializeJson(response, out);
  server.send(200, "application/json", out);
}

// ======================================================
// /diagnostic485
// ======================================================
void handleDiagnostic485() {
  sendCors();
  int zeroFirst = 0;
  int intermittent = 0;

  for (int i = 1; i <= 20; i++) {
    ScanCapture c = captureOne("M3", "WHITE");
    if (c.spectrum.raw[3] == 0) zeroFirst++;
    if (c.spectrum.raw485_status == "INTERMITTENT_READ") intermittent++;

    if (sdReady) {
      File f = SD.open("/diag485_v4.csv", FILE_APPEND);
      if (f) {
        f.print(c.uptimeMs); f.print(",");
        f.print(i); f.print(",");
        f.print(c.distanceMM); f.print(",");
        f.print(c.spectrum.raw[3]); f.print(",");
        f.print(c.spectrum.raw485_check2); f.print(",");
        f.print(c.spectrum.raw485_check3); f.print(",");
        f.print(c.spectrum.raw485_selected); f.print(",");
        f.print(c.spectrum.raw485_source); f.print(",");
        f.print(c.spectrum.cal[3], 6); f.print(",");
        f.println(c.spectrum.raw485_status);
        f.close();
      }
    }
    delay(150);
  }

  JsonDocument response;
  response["status"] = "selesai";
  response["total"] = 20;
  response["raw485_zero_first"] = zeroFirst;
  response["intermittent_read"] = intermittent;
  response["file_sd"] = "/diag485_v4.csv";
  String out; serializeJson(response, out);
  server.send(200, "application/json", out);
}

void handleSetFlaskUrl() {
  sendCors();
  if (server.hasArg("plain")) {
    JsonDocument doc;
    if (!deserializeJson(doc, server.arg("plain"))) {
      String newUrl = doc["flask_server"] | "";
      if (newUrl.length() > 0) {
        flask_server = newUrl;
        prefs.begin("necta", false);
        prefs.putString("flask_url", flask_server);
        prefs.end();
      }
    }
  }
  JsonDocument response;
  response["status"] = "sukses";
  response["flask_server"] = flask_server;
  String out; serializeJson(response, out);
  server.send(200, "application/json", out);
}

// ======================================================
// /config
// ======================================================
void handleConfig() {
  sendCors();
  JsonDocument response;
  response["firmware"] = FIRMWARE_VERSION;
  response["flask_server"] = flask_server;
  response["wifi_ssid"] = wifi_ssid;
  response["esp32_ip"] = WiFi.localIP().toString();
  response["gain_x"] = GAIN_X;
  response["integration_cycles"] = INTEGRATION_CYCLES;
  response["integration_ms_est"] = INTEGRATION_MS_EST;
  response["led_current_ma"] = LED_CURRENT_MA;
  response["qc_accept_max"] = QC_ACCEPT_MAX;
  response["qc_watch_max"] = QC_WATCH_MAX;
  response["m5_drift_engine"] = "FLASK_SUPABASE";
  String out; serializeJson(response, out);
  server.send(200, "application/json", out);
}

// ======================================================
// SETUP / LOOP
// ======================================================
void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  Wire1.begin(5, 4);

  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    display.setRotation(2);
    showMessage("NECTA v4", "START");
  }

  pinMode(pinRed, OUTPUT);
  pinMode(pinYellow, OUTPUT);
  pinMode(pinGreen, OUTPUT);
  pinMode(pinButton, INPUT_PULLUP);
  digitalWrite(pinRed, LOW);
  digitalWrite(pinYellow, LOW);
  digitalWrite(pinGreen, LOW);

  initSD();
  loadDynamicConfig();

  if (!sensorJarak.begin()) {
    Serial.println("VL53L0X gagal.");
    while (1) delay(10);
  }

  if (!sensorSpektrum.begin()) {
    Serial.println("AS7265X gagal.");
    while (1) delay(10);
  }

  sensorSpektrum.setGain(GAIN_ENUM);
  sensorSpektrum.setIntegrationCycles(INTEGRATION_CYCLES);
  sensorSpektrum.setMeasurementMode(AS7265X_MEASUREMENT_MODE_6CHAN_ONE_SHOT);
  sensorSpektrum.setBulbCurrent(AS7265X_LED_CURRENT_LIMIT_12_5MA, AS7265x_LED_WHITE);
  sensorSpektrum.setBulbCurrent(AS7265X_LED_CURRENT_LIMIT_12_5MA, AS7265x_LED_UV);
  sensorSpektrum.setBulbCurrent(AS7265X_LED_CURRENT_LIMIT_12_5MA, AS7265x_LED_IR);
  sensorSpektrum.disableIndicator();
  allBulbsOff();

  if (MDNS.begin("necta-esp32")) {
    Serial.println("mDNS responder necta-esp32.local aktif");
  }

  WiFi.begin(wifi_ssid.c_str(), wifi_password.c_str());
  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 15000) {
    delay(500);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("ESP32 IP: "); Serial.println(WiFi.localIP());
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 5);
    display.println("WiFi TERHUBUNG!");
    display.setTextSize(1);
    display.setCursor(0, 22);
    display.print("IP: ");
    display.println(WiFi.localIP());
    display.setCursor(0, 42);
    display.print("Flask: ");
    display.println(flask_server);
    display.display();
    delay(3000);
  } else {
    Serial.println("WiFi belum terhubung. SD tetap aktif.");
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 10);
    display.println("WiFi OFFLINE");
    display.setCursor(0, 30);
    display.println("Mode SD Aktif");
    display.display();
    delay(2000);
  }

  server.on("/trigger", HTTP_OPTIONS, handleOptions);
  server.on("/trigger", HTTP_POST, handleTrigger);
  server.on("/trigger5", HTTP_OPTIONS, handleOptions);
  server.on("/trigger5", HTTP_POST, handleTrigger5);
  server.on("/diagnostic485", HTTP_OPTIONS, handleOptions);
  server.on("/diagnostic485", HTTP_POST, handleDiagnostic485);
  server.on("/config", HTTP_OPTIONS, handleOptions);
  server.on("/config", HTTP_GET, handleConfig);
  server.on("/set_flask_url", HTTP_OPTIONS, handleOptions);
  server.on("/set_flask_url", HTTP_POST, handleSetFlaskUrl);
  server.begin();

  if (WiFi.status() == WL_CONNECTED) {
    showMessage("NECTA READY", WiFi.localIP().toString());
  } else {
    showMessage("NECTA READY", "WiFi OFFLINE");
  }
  Serial.println(FIRMWARE_VERSION);
}

void loop() {
  server.handleClient();

  if (statusSukses && millis() - waktuSukses > 2500) {
    statusSukses = false;
    digitalWrite(pinGreen, LOW);
  }

  if (!statusSukses && millis() - waktuTerakhirCek > 300) {
    waktuTerakhirCek = millis();
    int d = readDistanceMM();

    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 2);
    display.println("NECTA AS7265X v4-M5");
    display.setCursor(0, 18);
    display.print("Jarak: ");
    if (d > 0) {
      display.print(d); display.println(" mm");
    } else {
      display.println("INVALID");
    }
    display.setCursor(0, 36);
    display.print("IP: ");
    if (WiFi.status() == WL_CONNECTED) {
      display.println(WiFi.localIP());
    } else {
      display.println("WiFi OFFLINE");
    }
    display.setCursor(0, 50);
    display.print("Flask: ");
    int startHost = flask_server.indexOf("://");
    startHost = (startHost >= 0) ? startHost + 3 : 0;
    int endHost = flask_server.indexOf(":", startHost);
    if (endHost < 0) endHost = flask_server.indexOf("/", startHost);
    if (endHost > startHost) {
      display.println(flask_server.substring(startHost, endHost));
    } else {
      display.println("192.168.1.4");
    }
    display.display();

    digitalWrite(pinYellow, HIGH);
    digitalWrite(pinRed, LOW);
  }
}