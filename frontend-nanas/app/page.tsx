"use client";
import { useState } from "react";

export default function DataCollector() {
  const [idNanas, setIdNanas] = useState("N-0001");
  const [posisi, setPosisi] = useState("Atas");
  const [status, setStatus] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  // Fungsi untuk mengirim perintah ke ESP32 dengan Otomatisasi
  const handleTrigger = async () => {
    setIsLoading(true);
    setStatus("Meminta data dari sensor...");

    try {
      const esp32Endpoint = "http://10.100.12.138/trigger";

      const response = await fetch(esp32Endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          id_nanas: idNanas,
          titik_pindai: posisi,
        }),
      });

      if (response.ok) {
        setStatus(`Sukses: Data ${idNanas} posisi ${posisi} berhasil direkam!`);

        // --- LOGIKA OTOMATISASI ---
        if (posisi === "Atas") {
          setPosisi("Tengah");
        } else if (posisi === "Tengah") {
          setPosisi("Bawah");
        } else if (posisi === "Bawah") {
          // Ekstrak angka dari string (contoh: "N-0001" menjadi 1)
          const nomorSaatIni = parseInt(idNanas.split("-")[1], 10);
          const nomorBerikutnya = nomorSaatIni + 1;

          // Format kembali menjadi N-XXXX (contoh: 2 menjadi "N-0002")
          const idBerikutnya = `N-${nomorBerikutnya.toString().padStart(4, "0")}`;

          setIdNanas(idBerikutnya);
          setPosisi("Atas"); // Reset posisi ke Atas untuk nanas baru
        }
        // -------------------------
      } else {
        setStatus("Gagal: Jarak tidak ideal.");
      }
    } catch (error) {
      setStatus(
        "Error: Tidak dapat terhubung ke ESP32. Pastikan satu jaringan Wi-Fi.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex flex-col items-center justify-center bg-gray-50 p-6">
      <div className="bg-white rounded-xl shadow-lg p-8 w-full max-w-md">
        <h1 className="text-2xl font-bold text-gray-800 mb-6 text-center">
          Panel Perekam Nanas
        </h1>

        {/* Input ID Nanas */}
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            ID Nanas
          </label>
          <input
            type="text"
            value={idNanas}
            onChange={(e) => setIdNanas(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-4 py-2 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="N-0001"
          />
        </div>

        {/* Pemilihan Posisi */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Posisi Pindaian
          </label>
          <div className="grid grid-cols-3 gap-2">
            {["Atas", "Tengah", "Bawah"].map((pos) => (
              <button
                key={pos}
                onClick={() => setPosisi(pos)}
                className={`py-2 rounded-md border text-sm font-medium transition-colors ${
                  posisi === pos
                    ? "bg-blue-600 text-white border-blue-600"
                    : "bg-white text-gray-700 border-gray-300 hover:bg-gray-100"
                }`}
              >
                {pos}
              </button>
            ))}
          </div>
        </div>

        {/* Tombol Trigger */}
        <button
          onClick={handleTrigger}
          disabled={isLoading}
          className={`w-full py-3 rounded-md text-white font-semibold transition-all ${
            isLoading
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-green-600 hover:bg-green-700 active:scale-95"
          }`}
        >
          {isLoading ? "Mengambil Data..." : "Ambil Data Sensor"}
        </button>

        {/* Notifikasi Status */}
        {status && (
          <div
            className={`mt-4 p-3 rounded-md text-sm text-center ${
              status.includes("Sukses")
                ? "bg-green-100 text-green-800"
                : "bg-red-100 text-red-800"
            }`}
          >
            {status}
          </div>
        )}
      </div>
    </main>
  );
}
