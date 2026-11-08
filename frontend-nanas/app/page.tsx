"use client";

import { useEffect, useState } from "react";
import M5AcquisitionPanel from "./M5AcquisitionPanel";

type ModelScan = "M0" | "M1" | "M2" | "M3" | "M4" | "M5";
type PosisiScan = "Dark" | "White" | "Atas" | "Tengah" | "Bawah";

type ScanLogItem = {
  id: string;
  time: string;
  idNanas: string;
  model: ModelScan;
  posisi: PosisiScan;
  repeats: number;
  medianCv?: number | null;
  qcStatus?: string;
};

// ESP32 & Flask IP default
const DEFAULT_ESP32_IP = process.env.NEXT_PUBLIC_ESP32_URL || "http://10.200.135.106";
const DEFAULT_FLASK_IP = process.env.NEXT_PUBLIC_FLASK_URL || "http://10.200.135.209:5000";

// Konfigurasi per model M0 - M5
const MODEL_CONFIG: Record<
  ModelScan,
  {
    label: string;
    deskripsi: string;
    led: "OFF" | "ON";
    reference: boolean;
    qc: boolean;
    drift: boolean;
  }
> = {
  M0: {
    label: "M0 — Baseline",
    deskripsi: "LED OFF · Ambient · Tanpa Reference",
    led: "OFF",
    reference: false,
    qc: false,
    drift: false,
  },
  M1: {
    label: "M1 — Geometry",
    deskripsi: "LED OFF · Fixture · Tanpa Reference",
    led: "OFF",
    reference: false,
    qc: false,
    drift: false,
  },
  M2: {
    label: "M2 — Reference",
    deskripsi: "LED OFF · Dark/White Ref · Reflectance",
    led: "OFF",
    reference: true,
    qc: false,
    drift: false,
  },
  M3: {
    label: "M3 — Illumination",
    deskripsi: "LED ON · Controlled Illumination · Reference",
    led: "ON",
    reference: true,
    qc: false,
    drift: false,
  },
  M4: {
    label: "M4 — QC Real-Time",
    deskripsi: "LED ON · Real-time QC (CV Check: ACCEPT/REPEAT/FLAG)",
    led: "ON",
    reference: true,
    qc: true,
    drift: false,
  },
  M5: {
    label: "M5 — QC + Drift Control",
    deskripsi: "LED ON · QC + Drift Monitoring + Recalibration Sesi",
    led: "ON",
    reference: true,
    qc: true,
    drift: true,
  },
};

// Helper auto-increment ID yang menaikkan bagian angka (contoh: 777A -> 778A, 777 -> 778, N-0001 -> N-0002)
function autoIncrementId(id: string): string {
  if (!id || !id.trim()) return "777A";
  const trimmed = id.trim();

  // Pencocokan Angka Utama + Akhiran Huruf (opsional)
  // Contoh: "777A" -> prefix="", numStr="777", suffixLetters="A"
  // Contoh: "N-0001" -> prefix="N-", numStr="0001", suffixLetters=""
  const matchNum = trimmed.match(/^(.*?)(\d+)([a-zA-Z]*)$/);
  if (matchNum) {
    const prefix = matchNum[1];
    const numStr = matchNum[2];
    const suffixLetters = matchNum[3];

    const nextNum = parseInt(numStr, 10) + 1;
    const paddedNum = nextNum.toString().padStart(numStr.length, "0");
    return `${prefix}${paddedNum}${suffixLetters}`;
  }

  // Fallback jika tidak ada angka sama sekali (contoh: SAMPLE_A -> SAMPLE_B)
  const matchLetter = trimmed.match(/^(.*?)([a-zA-Z])$/);
  if (matchLetter) {
    const prefix = matchLetter[1];
    const charStr = matchLetter[2];
    const isUpper = charStr === charStr.toUpperCase();
    const charCode = charStr.toUpperCase().charCodeAt(0);
    if (charCode < 90) { // 'Z' = 90
      const nextChar = String.fromCharCode(charCode + 1);
      return `${prefix}${isUpper ? nextChar : nextChar.toLowerCase()}`;
    }
  }

  return `${trimmed}-1`;
}

// Helper kalkulasi QC Kestabilan (Median CV) 5x repeat di frontend
function computeBatchQC(
  captures: number[][],
  model: ModelScan,
  measurementType: string
) {
  let validChannels = 0;
  let channelsOver10 = 0;
  let maxCv = -1;
  const validCvs: number[] = [];

  for (let ch = 0; ch < 18; ch++) {
    let sum = 0;
    for (let r = 0; r < 5; r++) {
      sum += captures[r]?.[ch] || 0;
    }
    const mean = sum / 5;
    if (Math.abs(mean) < 0.001) continue;

    let sumSq = 0;
    for (let r = 0; r < 5; r++) {
      const diff = (captures[r]?.[ch] || 0) - mean;
      sumSq += diff * diff;
    }
    const sd = Math.sqrt(sumSq / 4);
    const cv = (sd / Math.abs(mean)) * 100;
    validCvs.push(cv);
    validChannels++;
    if (cv > 10.0) channelsOver10++;
    if (maxCv < 0 || cv > maxCv) maxCv = cv;
  }

  let medianCv: number | null = null;
  if (validCvs.length > 0) {
    validCvs.sort((a, b) => a - b);
    const mid = Math.floor(validCvs.length / 2);
    medianCv = validCvs.length % 2 !== 0 ? validCvs[mid] : (validCvs[mid - 1] + validCvs[mid]) / 2;
  }

  let status = "REFERENCE";
  let applied = false;

  if (measurementType !== "SAMPLE") {
    status = validChannels === 0 ? "AMBIENT_NO_LED" : "REFERENCE";
  } else if (!(model === "M4" || model === "M5")) {
    status = validChannels === 0 ? (model === "M0" || model === "M1" ? "AMBIENT_NO_LED" : "LOW_SIGNAL") : "POSTHOC_ONLY";
  } else {
    applied = true;
    if (validChannels === 0 || medianCv === null) status = "LOW_SIGNAL";
    else if (medianCv <= 5.0) status = "ACCEPT";
    else if (medianCv <= 10.0) status = "WATCH_REPEAT";
    else status = "FLAG";
  }

  const repeatRecommended = applied && (status === "WATCH_REPEAT" || status === "FLAG" || status === "LOW_SIGNAL");

  return { medianCv, maxCv, validChannels, channelsOver10, applied, status, repeatRecommended };
}

export default function DataCollector() {
  const [model, setModel] = useState<ModelScan>("M0");
  const [idNanas, setIdNanas] = useState("777A");
  const [posisi, setPosisi] = useState<PosisiScan>("Dark");
  const [gain, setGain] = useState<number>(64);
  const [startDelayMs, setStartDelayMs] = useState<number>(800);

  // Connection & IP State
  const [esp32Url, setEsp32Url] = useState(DEFAULT_ESP32_IP);
  const [flaskUrl, setFlaskUrl] = useState(DEFAULT_FLASK_IP);
  const [esp32Status, setEsp32Status] = useState<"idle" | "checking" | "online" | "offline">("idle");
  const [flaskStatus, setFlaskStatus] = useState<"idle" | "checking" | "online" | "offline">("idle");
  const [esp32Firmware, setEsp32Firmware] = useState<string>("");

  const [status, setStatus] = useState("");
  const [statusType, setStatusType] = useState<"info" | "success" | "error">("info");
  const [isLoading, setIsLoading] = useState(false);
  const [scanStep, setScanStep] = useState<number>(0);

  // Tracking Counter Scan & Repeat
  const [scanCounts, setScanCounts] = useState<Record<string, Record<PosisiScan, number>>>({});
  const [scanLogs, setScanLogs] = useState<ScanLogItem[]>([]);

  const [lastBrix, setLastBrix] = useState<number | null>(null);
  const [darkRefId, setDarkRefId] = useState<string>("");
  const [whiteRefId, setWhiteRefId] = useState<string>("");
  const [qcStatus, setQcStatus] = useState<{
    medianCv?: number | null;
    status?: string;
    repeatRecommended?: boolean;
  } | null>(null);

  const config = MODEL_CONFIG[model];

  const checkConnections = async () => {
    setEsp32Status("checking");
    setFlaskStatus("checking");

    try {
      const res = await fetch(`${esp32Url.replace(/\/+$/, "")}/config`, { signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        const data = await res.json();
        setEsp32Status("online");
        setEsp32Firmware(data.firmware || "v4.0-M5");
      } else {
        setEsp32Status("offline");
      }
    } catch {
      setEsp32Status("offline");
    }

    try {
      const res = await fetch(`${flaskUrl.replace(/\/+$/, "")}/api/m5/session/state/ping`, { signal: AbortSignal.timeout(3000) });
      if (res.ok || res.status === 404) {
        setFlaskStatus("online");
      } else {
        setFlaskStatus("offline");
      }
    } catch {
      setFlaskStatus("offline");
    }
  };

  useEffect(() => {
    checkConnections();
  }, []);

  // Hitung jumlah batch & total repeat data untuk sampel saat ini
  const currentFruitCounts = scanCounts[idNanas] || { Dark: 0, White: 0, Atas: 0, Tengah: 0, Bawah: 0 };
  const totalBatchesCurrentFruit = Object.values(currentFruitCounts).reduce((a, b) => a + b, 0);
  const totalSpektrumCurrentFruit = totalBatchesCurrentFruit * 5;

  // Fungsi batch scan otomatis untuk M0-M4 via /trigger5 ESP32
  const handleBatchScan = async () => {
    setIsLoading(true);
    setScanStep(1);
    setQcStatus(null);

    if (startDelayMs > 0) {
      setStatus(`⏱️ Persiapan scan [${posisi}] (${startDelayMs}ms)... Posisikan sensor ke sampel!`);
      setStatusType("info");
      await new Promise((resolve) => setTimeout(resolve, startDelayMs));
    }

    setStatus(`[${model}] Memulai 5x scan repeat untuk [${posisi}] (Gain ${gain}x)...`);
    setStatusType("info");

    const endpoint = `${esp32Url.replace(/\/+$/, "")}/trigger5`;

    const measurementType =
      posisi === "White" ? "WHITE" : posisi === "Dark" ? "DARK" : "SAMPLE";
    const scanPosition = measurementType === "SAMPLE" ? posisi : "REFERENCE";
    const isLedOn =
      measurementType !== "DARK" &&
      (model === "M3" || model === "M4" || model === "M5");

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: "S01",
          id_nanas: idNanas,
          titik_pindai: posisi,
          position: scanPosition,
          measurement_type: measurementType,
          reference_role: "NONE",
          dark_reference_id: darkRefId,
          white_reference_id: whiteRefId,
          model: model,
          gain_x: gain,
          integration_cycles: 49,
          illumination_mode:
            measurementType === "DARK"
              ? "DARK"
              : isLedOn
              ? "ONBOARD"
              : "AMBIENT",
          led_white: isLedOn,
          led_ir: isLedOn,
          led_uv: isLedOn,
          led_current_ma: 12.5,
        }),
      });

      if (!response.ok) {
        const errJson = await response.json().catch(() => null);
        const errMsg = errJson?.pesan || errJson?.message || `ESP32 status HTTP ${response.status}`;
        throw new Error(errMsg);
      }

      const resData = await response.json();

      if (resData.reference_id) {
        if (measurementType === "DARK") {
          setDarkRefId(resData.reference_id);
        } else if (measurementType === "WHITE") {
          setWhiteRefId(resData.reference_id);
        }
      }

      if (resData.qc_status) {
        setQcStatus({
          medianCv: resData.qc_median_cv,
          status: resData.qc_status,
          repeatRecommended: resData.repeat_recommended,
        });
      }

      if (
        resData.prediksi_brix !== undefined &&
        resData.prediksi_brix !== null
      ) {
        setLastBrix(resData.prediksi_brix);
      } else if (measurementType === "SAMPLE") {
        // Ambil hasil prediksi Brix terbaru dari Flask Backend
        try {
          const predRes = await fetch(`${flaskUrl.replace(/\/+$/, "")}/api/latest_prediction`, {
            signal: AbortSignal.timeout(2000),
          });
          if (predRes.ok) {
            const predJson = await predRes.json();
            if (predJson.prediksi_brix !== null && predJson.prediksi_brix !== undefined) {
              setLastBrix(predJson.prediksi_brix);
            }
          }
        } catch (err) {
          console.warn("Gagal mengambil prediksi_brix dari Flask:", err);
        }
      }

      // Update Counter Pengulangan per Sampel & Posisi
      setScanCounts((prev) => {
        const fruitMap = prev[idNanas] || { Dark: 0, White: 0, Atas: 0, Tengah: 0, Bawah: 0 };
        return {
          ...prev,
          [idNanas]: {
            ...fruitMap,
            [posisi]: (fruitMap[posisi] || 0) + 1,
          },
        };
      });

      // Tambahkan ke Riwayat Scan Log
      const nowStr = new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      setScanLogs((prev) => [
        {
          id: Math.random().toString(36).substring(2, 9),
          time: nowStr,
          idNanas,
          model,
          posisi,
          repeats: 5,
          medianCv: resData.qc_median_cv,
          qcStatus: resData.qc_status,
        },
        ...prev.slice(0, 14),
      ]);

      setIsLoading(false);
      setScanStep(0);

      const displayQc =
        resData.qc_status === "AMBIENT_NO_LED" || (resData.qc_status === "LOW_SIGNAL" && (model === "M0" || model === "M1"))
          ? "AMBIENT (LED OFF)"
          : resData.qc_status || "OK";

      setStatus(
        `Sukses! 5x scan repeat [${model} - ${posisi}] berhasil disimpan (Batch ke-${(currentFruitCounts[posisi] || 0) + 1}). QC: ${displayQc}`
      );
      setStatusType("success");

      // --- LOGIKA PERPINDAHAN OTOMATIS ---
      if (posisi === "Dark") {
        setPosisi("White");
      } else if (posisi === "White") {
        setPosisi("Atas");
      } else if (posisi === "Atas") {
        setPosisi("Tengah");
      } else if (posisi === "Tengah") {
        setPosisi("Bawah");
      } else if (posisi === "Bawah") {
        const nextId = autoIncrementId(idNanas);
        setIdNanas(nextId);
        setPosisi("White");
      }
    } catch (error) {
      setStatus(
        `Gagal scan ${posisi}. Error: ${
          error instanceof Error
            ? error.message
            : "Tidak dapat terhubung ke ESP32"
        }`
      );
      setStatusType("error");
      setIsLoading(false);
      setScanStep(0);
    }
  };

  // Event listener tombol keyboard (Spasi) untuk trigger scan tanpa pointer mouse
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      // Abaikan jika user sedang mengetik di input / textarea
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }

      if ((e.code === "Space" || e.key === " ") && !isLoading) {
        e.preventDefault();
        handleBatchScan();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isLoading, handleBatchScan]);

  const syncFlaskUrlToEsp32 = async () => {
    try {
      setStatus("Mengirim IP Flask baru ke ESP32...");
      setStatusType("info");
      const targetFlask = flaskUrl.includes("/api/simpan_sensor")
        ? flaskUrl
        : `${flaskUrl.replace(/\/+$/, "")}/api/simpan_sensor`;
      const res = await fetch(`${esp32Url.replace(/\/+$/, "")}/set_flask_url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flask_server: targetFlask }),
      });
      if (res.ok) {
        setStatus(`✅ URL Flask Server (${flaskUrl}) berhasil disimpan di memori ESP32!`);
        setStatusType("success");
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (e: any) {
      setStatus(`❌ Gagal menyimpan URL Flask ke ESP32: ${e.message}`);
      setStatusType("error");
    }
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-100 to-slate-200 flex flex-col items-center justify-center p-4 sm:p-6 font-sans">
      <div className="w-full max-w-4xl">
        {/* Header Dashboard */}
        <div className="mb-4 text-center">
          <p className="text-xs font-black uppercase tracking-widest text-blue-600 mb-1">
            NECTA · AS7265X v4 Akuisisi Nanas
          </p>
          <h1 className="text-3xl font-extrabold text-slate-800">
            Sistem Spektroskopi Nanas (M0 – M5)
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Pilih model akuisisi yang diinginkan untuk memulai pengambilan data.
          </p>
        </div>

        {/* Panel Status Koneksi & IP Alat */}
        <div className="bg-white rounded-2xl shadow-md border border-slate-200 p-4 mb-6">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
            {/* Status ESP32 Hardware */}
            <div className="flex items-center gap-3 w-full sm:w-auto">
              <div className="flex flex-col">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  ESP32 Hardware IP
                </span>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={esp32Url}
                    onChange={(e) => setEsp32Url(e.target.value)}
                    className="text-xs font-mono font-bold bg-slate-50 border border-slate-300 rounded-lg px-2.5 py-1 text-slate-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <span
                    className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                      esp32Status === "online"
                        ? "bg-emerald-100 text-emerald-700 border border-emerald-300"
                        : esp32Status === "offline"
                        ? "bg-rose-100 text-rose-700 border border-rose-300"
                        : "bg-amber-100 text-amber-700 border border-amber-300"
                    }`}
                  >
                    {esp32Status === "checking"
                      ? "MEMERIKSA..."
                      : esp32Status === "online"
                      ? "TERHUBUNG"
                      : "TERPUTUS"}
                  </span>
                </div>
                {esp32Firmware && (
                  <span className="text-[10px] text-slate-400 mt-0.5">
                    FW: {esp32Firmware}
                  </span>
                )}
              </div>
            </div>

            {/* Status Backend Flask */}
            <div className="flex items-center gap-3 w-full sm:w-auto">
              <div className="flex flex-col">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  Backend Flask IP
                </span>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={flaskUrl}
                    onChange={(e) => setFlaskUrl(e.target.value)}
                    className="text-xs font-mono font-bold bg-slate-50 border border-slate-300 rounded-lg px-2.5 py-1 text-slate-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                  <button
                    type="button"
                    onClick={syncFlaskUrlToEsp32}
                    title="Kirim dan simpan IP Flask ini ke memori ESP32"
                    className="text-[10px] font-bold px-2 py-1 bg-blue-100 hover:bg-blue-200 text-blue-800 border border-blue-300 rounded-lg transition"
                  >
                    💾 Sync IP
                  </button>
                  <span
                    className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                      flaskStatus === "online"
                        ? "bg-emerald-100 text-emerald-700 border border-emerald-300"
                        : flaskStatus === "offline"
                        ? "bg-rose-100 text-rose-700 border border-rose-300"
                        : "bg-amber-100 text-amber-700 border border-amber-300"
                    }`}
                  >
                    {flaskStatus === "checking"
                      ? "MEMERIKSA..."
                      : flaskStatus === "online"
                      ? "TERHUBUNG"
                      : "TERPUTUS"}
                  </span>
                </div>
              </div>
            </div>

            {/* Tombol Cek Ulang */}
            <button
              type="button"
              onClick={checkConnections}
              className="w-full sm:w-auto text-xs font-semibold px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl transition border border-slate-200"
            >
              🔄 Refresh Koneksi
            </button>
          </div>
        </div>

        {/* Navigation Selector Model M0-M5 */}
        <div className="bg-white rounded-2xl shadow-xl border border-slate-100 p-4 mb-6">
          <label className="block text-xs font-bold uppercase tracking-wider text-slate-500 mb-3 text-center">
            Pilih Model Penelitian
          </label>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
            {(Object.keys(MODEL_CONFIG) as ModelScan[]).map((m) => {
              const cfg = MODEL_CONFIG[m];
              const isSelected = model === m;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => setModel(m)}
                  disabled={isLoading}
                  className={`py-3 px-2 rounded-xl font-bold transition-all flex flex-col items-center justify-center gap-1 border ${
                    isSelected
                      ? "bg-blue-600 border-blue-600 text-white shadow-lg shadow-blue-500/30 scale-[1.02]"
                      : "bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  <span className="text-base">{m}</span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                      isSelected
                        ? "bg-blue-700 text-blue-100"
                        : "bg-slate-200 text-slate-600"
                    }`}
                  >
                    LED {cfg.led}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Info Banner Model */}
          <div className="mt-3 p-3 rounded-xl bg-blue-50/70 border border-blue-100 text-center">
            <span className="font-bold text-sm text-blue-900">
              {config.label}
            </span>
            <span className="text-slate-600 text-xs ml-2">
              — {config.deskripsi}
            </span>
          </div>
        </div>

        {/* Jika Model = M5, tampilkan Dashboard Khusus M5 Session & Drift Control */}
        {model === "M5" ? (
          <div className="bg-white rounded-2xl shadow-xl border border-slate-100 p-2">
            <M5AcquisitionPanel esp32Url={esp32Url} flaskUrl={flaskUrl} />
          </div>
        ) : (
          /* Jika Model = M0, M1, M2, M3, M4, tampilkan Panel Akuisisi Langsung */
          <div className="bg-white rounded-2xl shadow-xl border border-slate-100 p-6 sm:p-8 space-y-6">

            {/* Widget Counter Pengulangan per Sampel & Posisi */}
            <div className="bg-gradient-to-r from-blue-900 to-indigo-900 text-white rounded-2xl p-4 sm:p-5 shadow-lg">
              <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-black uppercase tracking-widest text-blue-300">
                      📊 Tracker Pengulangan (Repeat Counter)
                    </span>
                    <span className="bg-blue-800 text-blue-200 text-[10px] font-bold px-2 py-0.5 rounded-full">
                      Sampel: {idNanas}
                    </span>
                  </div>
                  <h3 className="text-lg font-black text-white mt-0.5">
                    Progres & Jumlah Scan Tersimpan
                  </h3>
                </div>
                <div className="bg-white/10 backdrop-blur-md px-3.5 py-1.5 rounded-xl border border-white/20 text-right">
                  <span className="text-[10px] text-blue-200 uppercase font-semibold block">
                    Total Spektrum Terkumpul
                  </span>
                  <span className="text-xl font-black text-amber-300">
                    {totalSpektrumCurrentFruit} <span className="text-xs text-white">data</span>
                  </span>
                </div>
              </div>

              {/* Status Repeat Per Titik Scan */}
              <div className="grid grid-cols-5 gap-2 text-center text-xs">
                {(["Dark", "White", "Atas", "Tengah", "Bawah"] as PosisiScan[]).map((p) => {
                  const count = currentFruitCounts[p] || 0;
                  const isCurrentTarget = posisi === p;
                  return (
                    <div
                      key={p}
                      className={`p-2.5 rounded-xl border transition-all ${
                        isCurrentTarget
                          ? "bg-amber-400 text-slate-950 font-bold border-amber-300 shadow-md scale-[1.03]"
                          : count > 0
                          ? "bg-emerald-500/20 text-emerald-100 border-emerald-400/40"
                          : "bg-white/5 text-blue-200 border-white/10"
                      }`}
                    >
                      <div className="text-[10px] uppercase font-bold tracking-wider">
                        {p}
                      </div>
                      <div className="text-base font-black mt-0.5">
                        {count}× <span className="text-[10px] font-normal">batch</span>
                      </div>
                      <div className="text-[9px] opacity-80 mt-0.5">
                        ({count * 5} repeat)
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Sisi Kiri: Input ID Nanas & Setting Gain */}
              <div className="space-y-5">
                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="block text-sm font-semibold text-slate-700">
                      ID Nanas / Sampel
                    </label>
                    <span className="text-[11px] text-blue-600 font-semibold">
                      ✨ Auto Next ID
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={idNanas}
                      onChange={(e) => setIdNanas(e.target.value)}
                      disabled={isLoading}
                      className="w-full border border-slate-300 rounded-xl px-4 py-2.5 text-slate-800 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                      placeholder="777A"
                    />
                    <button
                      type="button"
                      onClick={() => setIdNanas(autoIncrementId(idNanas))}
                      disabled={isLoading}
                      title="Naikkan ID Nanas / Sampel Otomatis"
                      className="px-3.5 py-2.5 bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 rounded-xl font-bold text-xs whitespace-nowrap transition flex items-center gap-1 active:scale-95 shadow-sm"
                    >
                      <span>Next ID ➡️</span>
                    </button>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-2">
                    Amplifikasi Gain Sensor
                  </label>
                  <div className="grid grid-cols-4 gap-2">
                    {[1, 3.7, 16, 64].map((g) => (
                      <button
                        key={g}
                        type="button"
                        onClick={() => setGain(g)}
                        disabled={isLoading}
                        className={`py-2.5 px-2 rounded-xl font-medium text-xs border transition-all flex items-center justify-center ${
                          gain === g
                            ? "bg-amber-600 border-amber-600 text-white shadow-md font-bold"
                            : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        {g}×
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="block text-sm font-semibold text-slate-700">
                      ⏱️ Jeda Persiapan Tangan (Delay)
                    </label>
                    <span className="text-[11px] text-slate-500 font-medium">
                      Persiapan Posisikan Sensor
                    </span>
                  </div>
                  <div className="grid grid-cols-5 gap-1.5">
                    {[0, 300, 500, 800, 1200].map((d) => (
                      <button
                        key={d}
                        type="button"
                        onClick={() => setStartDelayMs(d)}
                        disabled={isLoading}
                        className={`py-2 px-1 rounded-xl font-medium text-xs border transition-all flex items-center justify-center ${
                          startDelayMs === d
                            ? "bg-blue-600 border-blue-600 text-white shadow-md font-bold"
                            : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        {d === 0 ? "Off (0s)" : `${d}ms`}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Status Reference Terdaftar */}
                {config.reference && (
                  <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 text-xs space-y-1.5">
                    <span className="font-bold text-slate-700 block uppercase tracking-wider text-[11px]">
                      Status Reference Terdaftar
                    </span>
                    <div className="flex justify-between items-center text-slate-600">
                      <span>⚪ White Ref:</span>
                      <code className="bg-white px-2 py-0.5 rounded border border-slate-200 font-mono text-[11px] max-w-[180px] truncate">
                        {whiteRefId || "Belum discan"}
                      </code>
                    </div>
                    <div className="flex justify-between items-center text-slate-600">
                      <span>⬛ Dark Ref:</span>
                      <code className="bg-white px-2 py-0.5 rounded border border-slate-200 font-mono text-[11px] max-w-[180px] truncate">
                        {darkRefId || "Belum discan"}
                      </code>
                    </div>
                  </div>
                )}
              </div>

              {/* Sisi Kanan: Target Posisi / Tipe Scan */}
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Target / Posisi Scan
                </label>

                {/* Tombol Reference */}
                <div className="grid grid-cols-2 gap-2 mb-3">
                  {(["Dark", "White"] as PosisiScan[]).map((pos) => {
                    const count = currentFruitCounts[pos] || 0;
                    return (
                      <button
                        key={pos}
                        type="button"
                        onClick={() => setPosisi(pos)}
                        disabled={isLoading}
                        className={`py-3 px-3 rounded-xl font-medium text-xs border transition-all flex flex-col items-center justify-center relative ${
                          posisi === pos
                            ? pos === "White"
                              ? "bg-slate-800 border-slate-800 text-white shadow-md"
                              : "bg-purple-700 border-purple-700 text-white shadow-md"
                            : "bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200"
                        }`}
                      >
                        <span className="font-bold text-xs uppercase tracking-wider">
                          {pos === "White" ? "⚪ White Ref" : "⬛ Dark Ref"}
                        </span>
                        {count > 0 && (
                          <span className="text-[10px] mt-0.5 opacity-90">
                            {count}× batch ({count * 5} data)
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>

                {/* Tombol Sampel Nanas */}
                <label className="block text-xs font-semibold text-slate-500 mb-1.5">
                  Posisi Daging Nanas
                </label>
                <div className="grid grid-cols-3 gap-2 mb-4">
                  {(["Atas", "Tengah", "Bawah"] as PosisiScan[]).map((pos) => {
                    const count = currentFruitCounts[pos] || 0;
                    return (
                      <button
                        key={pos}
                        type="button"
                        onClick={() => setPosisi(pos)}
                        disabled={isLoading}
                        className={`py-3 px-3 rounded-xl font-medium text-xs border transition-all flex flex-col items-center justify-center ${
                          posisi === pos
                            ? "bg-blue-600 border-blue-600 text-white shadow-md shadow-blue-500/20"
                            : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        <span className="font-bold text-sm">{pos}</span>
                        {count > 0 && (
                          <span className="text-[10px] mt-0.5 opacity-80">
                            {count}× ({count * 5} repeat)
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>

                {/* Status QC Real-Time (Jika Model M4/M5) */}
                {qcStatus && (
                  <div
                    className={`p-3 rounded-xl border text-xs mb-4 ${
                      qcStatus.status === "ACCEPT"
                        ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                        : "bg-amber-50 border-amber-200 text-amber-800"
                    }`}
                  >
                    <div className="font-bold">
                      Hasil QC Real-Time: {qcStatus.status}
                    </div>
                    {qcStatus.medianCv != null && (
                      <div>
                        Median CV 5 Scan: {qcStatus.medianCv.toFixed(2)}%
                      </div>
                    )}
                    {qcStatus.repeatRecommended && (
                      <div className="font-semibold mt-1 text-amber-900">
                        ⚠️ Disarankan ulang scan pada titik ini.
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Animasi Live Step Scan Progress Bar (R1 -> R5) */}
            {isLoading && (
              <div className="p-4 bg-blue-50 border border-blue-200 rounded-2xl space-y-2">
                <div className="flex justify-between items-center text-xs font-bold text-blue-900">
                  <span>🔄 Proses Akusisi Scan 5x Repeat ({posisi})...</span>
                  <span>Repeat {scanStep} / 5</span>
                </div>

                {/* Visual Steps Indicator R1 - R5 */}
                <div className="grid grid-cols-5 gap-1.5 py-1">
                  {[1, 2, 3, 4, 5].map((step) => (
                    <div
                      key={step}
                      className={`h-2 rounded-full transition-all duration-300 ${
                        step <= scanStep
                          ? "bg-blue-600 shadow-sm"
                          : "bg-blue-200/60"
                      }`}
                    />
                  ))}
                </div>
                <div className="text-[11px] text-blue-700 text-center font-medium">
                  {scanStep < 5
                    ? `Membaca Repeat R${scanStep} dari sensor AS7265X...`
                    : "Menghitung Kestabilan CV 18 Kanal & Menyimpan Data..."}
                </div>
              </div>
            )}

            {/* Tombol Eksekusi Batch Scan 5x */}
            <button
              onClick={handleBatchScan}
              disabled={isLoading}
              className={`w-full py-4 rounded-xl text-white font-bold text-base shadow-lg transition-all flex items-center justify-center gap-2 ${
                isLoading
                  ? "bg-slate-400 cursor-not-allowed"
                  : "bg-emerald-600 hover:bg-emerald-700 active:scale-[0.98] shadow-emerald-600/20"
              }`}
            >
              {isLoading ? (
                <span>Proses Scan Repeat R{scanStep}/5 ({posisi})...</span>
              ) : (
                <span className="flex items-center gap-2">
                  <span>Mulai Scan 5x Repeat [{model} — {posisi}]</span>
                  <span className="text-xs font-semibold bg-emerald-700/80 px-2 py-0.5 rounded-md border border-emerald-500/50 shadow-sm">
                    ⌨️ [Spasi]
                  </span>
                </span>
              )}
            </button>

            {/* Kartu Prediksi Brix Real-Time */}
            {lastBrix !== null && (
              <div className="mt-4 p-4 rounded-2xl bg-amber-50/80 border border-amber-200 flex items-center justify-between">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-amber-800">
                    Estimasi Kemanisan
                  </p>
                  <p className="text-xs text-amber-600">
                    Model PLSR Spektroskopi
                  </p>
                </div>
                <div className="flex items-baseline gap-1">
                  <span className="text-3xl font-black text-amber-950">
                    {lastBrix}
                  </span>
                  <span className="text-sm font-bold text-amber-700">°Bx</span>
                </div>
              </div>
            )}

            {/* Status Notifikasi */}
            {status && (
              <div
                className={`mt-4 p-3.5 rounded-xl text-sm font-medium border text-center transition-all ${
                  statusType === "success"
                    ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                    : statusType === "error"
                    ? "bg-rose-50 text-rose-800 border-rose-200"
                    : "bg-blue-50 text-blue-800 border-blue-200"
                }`}
              >
                {status}
              </div>
            )}

            {/* Tabel Riwayat Batch Scan Terbaru */}
            {scanLogs.length > 0 && (
              <div className="mt-6 pt-4 border-t border-slate-200">
                <div className="flex justify-between items-center mb-3">
                  <span className="text-xs font-bold uppercase tracking-wider text-slate-500">
                    📋 Riwayat Pengulangan Scan (Sesi Ini)
                  </span>
                  <span className="text-[11px] text-slate-400">
                    {scanLogs.length} batch recorded
                  </span>
                </div>
                <div className="overflow-x-auto rounded-xl border border-slate-200">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-100 text-slate-600 font-bold uppercase text-[10px]">
                      <tr>
                        <th className="p-2.5">Waktu</th>
                        <th className="p-2.5">ID Sampel</th>
                        <th className="p-2.5">Model</th>
                        <th className="p-2.5">Posisi</th>
                        <th className="p-2.5">Repeat</th>
                        <th className="p-2.5">Median CV</th>
                        <th className="p-2.5">Status QC</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 font-medium">
                      {scanLogs.map((log) => (
                        <tr key={log.id} className="hover:bg-slate-50">
                          <td className="p-2.5 text-slate-500 font-mono text-[11px]">{log.time}</td>
                          <td className="p-2.5 font-bold text-slate-800">{log.idNanas}</td>
                          <td className="p-2.5">{log.model}</td>
                          <td className="p-2.5">
                            <span className="bg-slate-100 px-2 py-0.5 rounded font-bold">
                              {log.posisi}
                            </span>
                          </td>
                          <td className="p-2.5 text-emerald-700 font-bold">
                            5x (R1–R5)
                          </td>
                          <td className="p-2.5">
                            {log.medianCv != null ? `${log.medianCv.toFixed(2)}%` : "-"}
                          </td>
                          <td className="p-2.5">
                            <span
                              className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                                log.qcStatus === "ACCEPT"
                                  ? "bg-emerald-100 text-emerald-800"
                                  : log.qcStatus === "WATCH_REPEAT"
                                  ? "bg-amber-100 text-amber-800"
                                  : log.qcStatus === "POSTHOC_ONLY"
                                  ? "bg-blue-100 text-blue-800"
                                  : log.qcStatus === "AMBIENT_NO_LED" || (log.qcStatus === "LOW_SIGNAL" && (log.model === "M0" || log.model === "M1"))
                                  ? "bg-slate-200 text-slate-700"
                                  : "bg-rose-100 text-rose-800"
                              }`}
                            >
                              {log.qcStatus === "AMBIENT_NO_LED" || (log.qcStatus === "LOW_SIGNAL" && (log.model === "M0" || log.model === "M1"))
                                ? "AMBIENT (LED OFF)"
                                : log.qcStatus || "OK"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

          </div>
        )}
      </div>
    </main>
  );
}
