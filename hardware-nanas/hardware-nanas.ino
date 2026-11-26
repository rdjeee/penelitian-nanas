#include <WiFi.h>
#include <WebServer.h>

// =================================
// PENGATURAN HOTSPOT HP (WIFI_STA)
// =================================
const char* ssid = "Oke";
const char* password = "dimaskece";

// Pengaturan Timeout Koneksi Wi-Fi (15 detik)
const unsigned long wifiTimeout = 15000;
bool modeFallbackAP = false;

WebServer server(80);

// =================================
// PIN L298N & SENSOR PIR
// =================================
// MOTOR KIRI
#define ENA 32
#define IN1 26
#define IN2 25

// MOTOR KANAN
#define ENB 33
#define IN3 27
#define IN4 14

// SENSOR PIR
#define PIN_PIR 13

// KECEPATAN MOTOR (0 - 255)
int kecepatan = 155;
bool sedangBergerak = false;
bool statusGerakan = false;

// PENGAMAN KONEKSI (TIMEOUT PERINTAH MOTOR)
unsigned long waktuPerintah = 0;
const unsigned long batasWaktu = 700;

// =================================
// FUNGSI KECEPATAN SOFT-START
// =================================
void aturKecepatan() {
  if (!sedangBergerak) {
    for (int pwm = 100; pwm <= kecepatan; pwm += 15) {
      analogWrite(ENA, pwm);
      analogWrite(ENB, pwm);
      delay(15);
    }
    sedangBergerak = true;
  } else {
    analogWrite(ENA, kecepatan);
    analogWrite(ENB, kecepatan);
  }
}

// =================================
// FUNGSI PERGERAKAN ROBOT
// =================================
void berhenti() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  analogWrite(ENA, 0);
  analogWrite(ENB, 0);
  sedangBergerak = false;
}

void maju() {
  if (digitalRead(PIN_PIR) == HIGH) {
    berhenti();
    return;
  }
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
  aturKecepatan();
}

void mundur() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
  aturKecepatan();
}

void kiri() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
  aturKecepatan();
}

void kanan() {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
  aturKecepatan();
}

// =================================
// TAMPILAN WEB REMOT HP + STATUS PIR
// =================================
const char halaman[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ROBOT WIFI + PIR</title>
<style>
body {
  background: #0f172a;
  color: white;
  font-family: Arial, sans-serif;
  text-align: center;
  padding: 20px;
  margin: 0;
}
h2 { margin-top: 10px; letter-spacing: 1px; }
.status-box, .pir-box {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 10px;
  margin: 10px auto;
  max-width: 280px;
  font-weight: bold;
}
.status-box { color: #4ade80; }
.pir-box { color: #facc15; }
.grid {
  display: grid;
  grid-template-columns: 95px 95px 95px;
  gap: 12px;
  justify-content: center;
  margin-top: 18px;
}
button {
  height: 85px;
  border: none;
  border-radius: 18px;
  font-size: 16px;
  font-weight: bold;
  background: #334155;
  color: white;
  touch-action: none;
  user-select: none;
  box-shadow: 0 4px 6px rgba(0,0,0,0.3);
}
button:active { background: #0284c7; transform: scale(0.96); }
.stop { background: #dc2626; }
.empty { visibility: hidden; }
.speed-box {
  margin: 20px auto 10px auto;
  max-width: 280px;
  background: #1e293b;
  padding: 15px;
  border-radius: 14px;
}
input[type=range] { width: 100%; margin-top: 10px; }
</style>
</head>
<body>

<h2>ROBOT WIFI CONTROL</h2>
<div class="status-box" id="statusText">MOTOR: SIAP (STOP)</div>
<div class="pir-box" id="pirText">PIR: MEMANTAU...</div>

<div class="grid">
  <button class="empty"></button>
  <button data-cmd="F" data-label="MAJU">▲<br>MAJU</button>
  <button class="empty"></button>

  <button data-cmd="L" data-label="BELOK KIRI">◀<br>KIRI</button>
  <button class="stop" data-cmd="S" data-label="STOP">STOP</button>
  <button data-cmd="R" data-label="BELOK KANAN">▶<br>KANAN</button>

  <button class="empty"></button>
  <button data-cmd="B" data-label="MUNDUR">▼<br>MUNDUR</button>
  <button class="empty"></button>
</div>

<div class="speed-box">
  <div>KECEPATAN MOTOR: <span id="speedVal">155</span></div>
  <input type="range" min="110" max="255" value="155" id="speedSlider">
</div>

<script>
let active = null;
let heartbeat = null;
const statusText = document.getElementById('statusText');
const pirText = document.getElementById('pirText');
const speedSlider = document.getElementById('speedSlider');
const speedVal = document.getElementById('speedVal');

async function fetchDenganTimeout(url, ms = 1000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), ms);
  try {
    const res = await fetch(url, { cache: 'no-store', signal: controller.signal });
    clearTimeout(id);
    return res;
  } catch (e) {
    clearTimeout(id);
    throw e;
  }
}

setInterval(async () => {
  try {
    const res = await fetchDenganTimeout('/pir', 800);
    const data = await res.text();
    if (data === '1') {
      pirText.textContent = 'PIR: GERAKAN TERDETEKSI!';
      pirText.style.background = '#7f1d1d';
      pirText.style.color = '#fca5a5';
    } else {
      pirText.textContent = 'PIR: AMAN (TIDAK ADA GERAK)';
      pirText.style.background = '#1e293b';
      pirText.style.color = '#4ade80';
    }
  } catch(e) {
    pirText.textContent = 'KONEKSI TIMEOUT / TERPUTUS!';
    pirText.style.background = '#78350f';
    pirText.style.color = '#fde047';
  }
}, 400);

speedSlider.addEventListener('input', () => {
  speedVal.textContent = speedSlider.value;
});

speedSlider.addEventListener('change', async () => {
  try {
    await fetchDenganTimeout('/speed?val=' + speedSlider.value, 1000);
  } catch(e) {}
});

async function kirim(cmd) {
  try {
    await fetchDenganTimeout('/' + cmd, 800);
  } catch(e) {}
}

function mulai(cmd, label) {
  statusText.textContent = 'MOTOR: ' + label;
  statusText.style.color = '#38bdf8';
  kirim(cmd);
  heartbeat = setInterval(() => { kirim(cmd); }, 200);
}

function selesai() {
  if (heartbeat) {
    clearInterval(heartbeat);
    heartbeat = null;
  }
  active = null;
  statusText.textContent = 'MOTOR: STOP';
  statusText.style.color = '#4ade80';
  kirim('S');
}

document.querySelectorAll('[data-cmd]').forEach(btn => {
  btn.addEventListener('pointerdown', e => {
    e.preventDefault();
    if (active) return;
    active = btn;
    btn.setPointerCapture(e.pointerId);
    if (btn.dataset.cmd === 'S') {
      selesai();
    } else {
      mulai(btn.dataset.cmd, btn.dataset.label);
    }
  });
  btn.addEventListener('pointerup', () => { if (active === btn) selesai(); });
  btn.addEventListener('pointercancel', () => { if (active === btn) selesai(); });
  btn.addEventListener('lostpointercapture', () => { if (active === btn) selesai(); });
});

window.addEventListener('blur', selesai);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) selesai();
});
</script>
</body>
</html>
)rawliteral";

// =================================
// FUNGSI KONTROL PERINTAH
// =================================
void perintahMotor(char cmd) {
  switch (cmd) {
    case 'F': maju(); break;
    case 'B': mundur(); break;
    case 'L': kiri(); break;
    case 'R': kanan(); break;
    default:
      berhenti();
      waktuPerintah = 0;
      return;
  }
  waktuPerintah = millis();
}

// =================================
// SETUP
// =================================
void setup() {
  Serial.begin(115200);
  delay(2000);

  pinMode(ENA, OUTPUT);
  pinMode(ENB, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  pinMode(PIN_PIR, INPUT);

  berhenti();

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  Serial.print("\nMenghubungkan ke Hotspot HP (");
  Serial.print(ssid);
  Serial.print(")");

  unsigned long mulaiKonek = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - mulaiKonek < wifiTimeout)) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n=============================");
  if (WiFi.status() == WL_CONNECTED) {
    modeFallbackAP = false;
    Serial.println("BERHASIL TERHUBUNG KE HOTSPOT HP!");
    Serial.print("IP ADDRESS ROBOT : ");
    Serial.println(WiFi.localIP());
  } else {
    modeFallbackAP = true;
    WiFi.disconnect();
    WiFi.mode(WIFI_AP);
    WiFi.softAP("ROBOT_FALLBACK", "12345678");
    Serial.println("TIMEOUT! Gagal menyambung ke Hotspot HP.");
    Serial.println("Beralih ke Wi-Fi Cadangan: ROBOT_FALLBACK (pw: 12345678)");
    Serial.print("IP ADDRESS CADANGAN : ");
    Serial.println(WiFi.softAPIP());
  }
  Serial.println("=============================");

  server.on("/", []() {
    server.send_P(200, "text/html", halaman);
  });

  server.on("/F", []() { perintahMotor('F'); server.send(200, "text/plain", "MAJU"); });
  server.on("/B", []() { perintahMotor('B'); server.send(200, "text/plain", "MUNDUR"); });
  server.on("/L", []() { perintahMotor('L'); server.send(200, "text/plain", "KIRI"); });
  server.on("/R", []() { perintahMotor('R'); server.send(200, "text/plain", "KANAN"); });
  server.on("/S", []() { perintahMotor('S'); server.send(200, "text/plain", "STOP"); });

  server.on("/speed", []() {
    if (server.hasArg("val")) {
      kecepatan = server.arg("val").toInt();
    }
    server.send(200, "text/plain", "OK");
  });

  server.on("/pir", []() {
    int baca = digitalRead(PIN_PIR);
    server.send(200, "text/plain", baca == HIGH ? "1" : "0");
  });

  server.begin();
}

// =================================
// LOOP
// =================================
unsigned long waktuCetakIP = 0;

void loop() {
  server.handleClient();

  bool deteksi = (digitalRead(PIN_PIR) == HIGH);
  if (deteksi != statusGerakan) {
    statusGerakan = deteksi;
    Serial.println(deteksi ? "[PIR] GERAKAN TERDETEKSI!" : "[PIR] AMAN");
  }

  if (deteksi && digitalRead(IN1) == HIGH && digitalRead(IN3) == HIGH) {
    berhenti();
    waktuPerintah = 0;
  }

  if (millis() - waktuCetakIP > 5000) {
    if (!modeFallbackAP) {
      if (WiFi.status() == WL_CONNECTED) {
        Serial.print("IP Robot di Hotspot HP: ");
        Serial.println(WiFi.localIP());
      } else {
        Serial.println("Terputus dari Hotspot! Mencoba menyambung ulang...");
        WiFi.reconnect();
      }
    } else {
      Serial.print("Mode Cadangan Aktif | IP: ");
      Serial.println(WiFi.softAPIP());
    }
    waktuCetakIP = millis();
  }

  if (waktuPerintah != 0 && millis() - waktuPerintah > batasWaktu) {
    berhenti();
    waktuPerintah = 0;
  }
}