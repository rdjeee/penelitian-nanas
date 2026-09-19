"use client";
import { useState, useEffect } from "react";

type PosisiScan = "White" | "Dark" | "Atas" | "Tengah" | "Bawah";

// Default IP Address ESP32 (sesuaikan jika berpindah jaringan)
const ESP32_IP = "192.168.1.7";

export default function DataCollector() {
  const [idNanas, setIdNanas] = useState("N-0001");
  const [posisi, setPosisi] = useState<PosisiScan>("White");
  const [status, setStatus] = useState("");
  const [statusType, setStatusType] = useState<"info" | "success" | "error">("info");
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);

  const [lastBrix, setLastBrix] = useState<number | null>(null);

  // Helper untuk mendapatkan target jumlah scan berdasarkan posisi
  const getTargetScan = (pos: PosisiScan): number => {
    if (pos === "White" || pos === "Dark") {
      return 10; // Target kalibrasi 10 kali
    }
    return 5; // Target titik nanas 5 kali
  };

  // Fungsi batch scan otomatis (Pilihan A)
  const handleBatchScan = async () => {
    setIsLoading(true);
    const targetCount = getTargetScan(posisi);
    setProgress({ current: 0, total: targetCount });
    setStatus(`Memulai pengambilan ${targetCount} data untuk mode [${posisi}]...`);
    setStatusType("info");

    const endpoint = `http://${ESP32_IP.replace(/^https?:\/\//, "").replace(/\/+$/, "")}/trigger`;

    // Tentukan ID yang dikirim
    const idToSend =
      posisi === "White" ? "REF_WHITE" : posisi === "Dark" ? "REF_DARK" : idNanas;

    let successCount = 0;

    for (let i = 1; i <= targetCount; i++) {
      setProgress({ current: i, total: targetCount });
      setStatus(`Mengambil data ${posisi}: [${i} / ${targetCount}]...`);
      setStatusType("info");

      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            id_nanas: idToSend,
            titik_pindai: posisi,
          }),
        });

        if (!response.ok) {
          throw new Error(`ESP32 mengembalikan status ${response.status}`);
        }

        try {
          const resData = await response.json();
          if (resData.prediksi_brix !== undefined && resData.prediksi_brix !== null) {
            setLastBrix(resData.prediksi_brix);
          }
        } catch {
          // ignore json parse error
        }

        successCount++;

        // Jeda singkat 300ms antar-scan agar stabil
        if (i < targetCount) {
          await new Promise((resolve) => setTimeout(resolve, 300));
        }
      } catch (error) {
        setStatus(
          `Gagal pada scan ke-${i} (${posisi}). Error: ${
            error instanceof Error ? error.message : "Tidak dapat terhubung ke ESP32"
          }`
        );
        setStatusType("error");
        setIsLoading(false);
        return;
      }
    }

    // Jika seluruh scan berhasil
    setIsLoading(false);
    setProgress(null);
    setStatus(`Sukses! ${successCount} data [${posisi}] berhasil disimpan.`);
    setStatusType("success");

    // --- LOGIKA PERPINDAHAN OTOMATIS ---
    if (posisi === "White") {
      setPosisi("Dark");
    } else if (posisi === "Dark") {
      setPosisi("Atas");
    } else if (posisi === "Atas") {
      setPosisi("Tengah");
    } else if (posisi === "Tengah") {
      setPosisi("Bawah");
    } else if (posisi === "Bawah") {
      // Ekstrak nomor nanas (contoh: "N-0001" -> 1)
      const parts = idNanas.split("-");
      if (parts.length === 2 && !isNaN(parseInt(parts[1], 10))) {
        const nomorSaatIni = parseInt(parts[1], 10);
        const nomorBerikutnya = nomorSaatIni + 1;
        const idBerikutnya = `N-${nomorBerikutnya.toString().padStart(4, "0")}`;
        setIdNanas(idBerikutnya);
      }
      setPosisi("Atas"); // Reset posisi ke Atas untuk buah berikutnya
    }
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-100 to-slate-200 flex flex-col items-center justify-center p-4 sm:p-6 font-sans">
      <div className="bg-white rounded-2xl shadow-xl border border-slate-100 p-6 sm:p-8 w-full max-w-lg">
        {/* Input ID Nanas */}
        <div className="mb-5">
          <label className="block text-sm font-semibold text-slate-700 mb-1.5">
            ID Nanas
          </label>
          <input
            type="text"
            value={idNanas}
            onChange={(e) => setIdNanas(e.target.value)}
            disabled={isLoading}
            className="w-full border border-slate-300 rounded-xl px-4 py-2.5 text-slate-800 font-medium focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            placeholder="N-0001"
          />
        </div>

        {/* Pilihan Posisi Scan */}
        <div className="mb-6">
          <label className="block text-sm font-semibold text-slate-700 mb-2">
            Posisi / Mode Pengukuran
          </label>
          <div className="grid grid-cols-2 gap-2 mb-2">
            {(["White", "Dark"] as PosisiScan[]).map((pos) => (
              <button
                key={pos}
                type="button"
                onClick={() => setPosisi(pos)}
                disabled={isLoading}
                className={`py-2 px-3 rounded-xl font-medium text-xs border transition-all flex flex-col items-center justify-center ${
                  posisi === pos
                    ? pos === "White"
                      ? "bg-slate-100 border-slate-400 text-slate-800 shadow-sm"
                      : "bg-slate-800 border-slate-900 text-white shadow-sm"
                    : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                }`}
              >
                <span className="font-semibold text-sm">{pos} Ref</span>
              </button>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {(["Atas", "Tengah", "Bawah"] as PosisiScan[]).map((pos) => (
              <button
                key={pos}
                type="button"
                onClick={() => setPosisi(pos)}
                disabled={isLoading}
                className={`py-2.5 px-3 rounded-xl font-medium text-xs border transition-all flex flex-col items-center justify-center ${
                  posisi === pos
                    ? "bg-blue-600 border-blue-600 text-white shadow-md shadow-blue-500/20"
                    : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                }`}
              >
                <span className="font-bold text-sm">{pos}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Tombol Eksekusi Batch Scan */}
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
            <>
              <svg
                className="animate-spin h-5 w-5 text-white"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                ></circle>
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v8H4z"
                ></path>
              </svg>
              <span>Mengambil Data ({progress?.current}/{progress?.total})...</span>
            </>
          ) : (
            <span>Mulai Scan Otomatis ({getTargetScan(posisi)}x {posisi})</span>
          )}
        </button>

        {/* Kartu Prediksi Brix Real-Time */}
        {lastBrix !== null && (
          <div className="mt-4 p-4 rounded-2xl bg-amber-50/80 border border-amber-200 flex items-center justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-amber-800">
                Estimasi Kemanisan
              </p>
              <p className="text-xs text-amber-600">Model PLSR Spektroskopi</p>
            </div>
            <div className="flex items-baseline gap-1">
              <span className="text-3xl font-black text-amber-950">{lastBrix}</span>
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
      </div>
    </main>
  );
}
