"use client";

import { useMemo, useState } from "react";
import styles from "./M5AcquisitionPanel.module.css";

type Position = "Atas" | "Tengah" | "Bawah";

type ScanResponse = {
  status: string;
  berhasil?: number;
  total?: number;
  reference_id?: string;
  reference_role?: string;
  qc_group_id?: string;
  qc_median_cv?: number | null;
  qc_max_cv?: number | null;
  qc_status?: string;
  qc_applied?: boolean;
  repeat_recommended?: boolean;
};

type M5State = {
  session_id: string;
  status: "ACTIVE" | "CLOSED";
  active_dark_reference_id: string;
  active_white_reference_id: string;
  fruit_count_total: number;
  fruits_since_white_check: number;
  white_check_required: boolean;
  recalibration_required: boolean;
  last_check_reference_id?: string | null;
  last_drift_percent?: number | null;
  last_drift_status?: string | null;
};

type DriftResponse = {
  status: string;
  drift_check_id?: number;
  drift_method?: string;
  median_drift_percent?: number;
  max_drift_percent?: number;
  drift_status?: "ACCEPT" | "WARNING" | "RECALIBRATE" | "STOP_CHECK";
  recalibration_required?: boolean;
  state?: M5State;
};

interface M5AcquisitionPanelProps {
  esp32Url?: string;
  flaskUrl?: string;
}

async function postJSON<T>(url: string, payload: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.pesan || data?.message || `HTTP ${res.status}`);
  }
  return data as T;
}

export default function M5AcquisitionPanel({
  esp32Url = process.env.NEXT_PUBLIC_ESP32_URL || "http://10.111.1.116",
  flaskUrl = process.env.NEXT_PUBLIC_FLASK_URL || "http://10.111.1.108:5000",
}: M5AcquisitionPanelProps = {}) {
  const targetEsp32 = esp32Url.replace(/\/+$/, "");
  const targetFlask = flaskUrl.replace(/\/+$/, "");

  const [sessionId, setSessionId] = useState("M5-S01");
  const [fruitId, setFruitId] = useState("N-0001");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("Siap memulai M5.");

  const [baselineDark, setBaselineDark] = useState("");
  const [baselineWhite, setBaselineWhite] = useState("");
  const [state, setState] = useState<M5State | null>(null);
  const [positionQC, setPositionQC] = useState<Record<Position, ScanResponse | null>>({
    Atas: null,
    Tengah: null,
    Bawah: null,
  });
  const [lastDrift, setLastDrift] = useState<DriftResponse | null>(null);
  const [recalDark, setRecalDark] = useState("");
  const [recalWhite, setRecalWhite] = useState("");
  const [finalWhite, setFinalWhite] = useState("");
  const [finalDark, setFinalDark] = useState("");

  const allAccepted = useMemo(
    () => (["Atas", "Tengah", "Bawah"] as Position[]).every(
      (p) => positionQC[p]?.qc_status === "ACCEPT"
    ),
    [positionQC]
  );

  async function refreshState() {
    if (!sessionId) return;
    try {
      const res = await fetch(`${targetFlask}/api/m5/session/state/${encodeURIComponent(sessionId)}`);
      if (res.status === 404) {
        setState(null);
        setMessage(`Sesi "${sessionId}" belum dibuat di Flask Server.`);
        return;
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data?.pesan || `HTTP ${res.status}`);
      setState(data.state);
      setMessage(`State sesi "${sessionId}" berhasil diperbarui.`);
    } catch (e: any) {
      setMessage(`❌ Gagal terhubung ke Flask Server (${targetFlask}): ${e.message}`);
    }
  }

  async function scanReference(
    type: "DARK" | "WHITE",
    role: string,
    fruit = ""
  ) {
    return postJSON<ScanResponse>(`${targetEsp32}/trigger5`, {
      session_id: sessionId,
      id_nanas: fruit,
      model: "M5",
      measurement_type: type,
      position: "REFERENCE",
      reference_role: role,
      dark_reference_id: state?.active_dark_reference_id || baselineDark || "",
      white_reference_id: state?.active_white_reference_id || baselineWhite || "",
    });
  }

  async function doBaselineDark() {
    try {
      setBusy("baseline-dark");
      setMessage("Tutup sensor rapat. Mengambil DARK baseline 5x...");
      const r = await scanReference("DARK", "BASELINE_DARK");
      setBaselineDark(r.reference_id || "");
      setMessage(`DARK baseline selesai: ${r.reference_id}`);
    } catch (e: any) {
      setMessage(`Gagal DARK baseline: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function doBaselineWhite() {
    try {
      setBusy("baseline-white");
      setMessage("Pasang white reference. Mengambil WHITE baseline 5x...");
      const r = await scanReference("WHITE", "BASELINE_WHITE");
      setBaselineWhite(r.reference_id || "");
      setMessage(`WHITE baseline selesai: ${r.reference_id}`);
    } catch (e: any) {
      setMessage(`Gagal WHITE baseline: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function startSession() {
    if (!baselineDark || !baselineWhite) {
      setMessage("Ambil DARK dan WHITE baseline terlebih dahulu.");
      return;
    }
    try {
      setBusy("start");
      const r = await postJSON<{ state: M5State }>(`${targetFlask}/api/m5/session/start`, {
        session_id: sessionId,
        baseline_dark_reference_id: baselineDark,
        baseline_white_reference_id: baselineWhite,
      });
      setState(r.state);
      setMessage("Sesi M5 aktif. Silakan scan buah.");
    } catch (e: any) {
      setMessage(`Gagal memulai sesi: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function scanSample(position: Position) {
    if (!state) return setMessage("Mulai sesi M5 terlebih dahulu.");
    if (state.white_check_required || state.recalibration_required) {
      return setMessage("Selesaikan white check/recalibration sebelum scan buah.");
    }
    if (!fruitId.trim()) return setMessage("Isi Fruit ID terlebih dahulu.");

    try {
      setBusy(`sample-${position}`);
      setMessage(`Scan ${fruitId} - ${position} sebanyak 5x...`);
      const r = await postJSON<ScanResponse>(`${targetEsp32}/trigger5`, {
        session_id: sessionId,
        id_nanas: fruitId.trim(),
        model: "M5",
        measurement_type: "SAMPLE",
        position,
        reference_role: "NONE",
        dark_reference_id: state.active_dark_reference_id,
        white_reference_id: state.active_white_reference_id,
      });
      setPositionQC((prev) => ({ ...prev, [position]: r }));
      setMessage(
        `${position}: CV median ${r.qc_median_cv?.toFixed?.(2) ?? "-"}% - ${r.qc_status}`
      );
    } catch (e: any) {
      setMessage(`Gagal scan ${position}: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function completeFruit() {
    if (!state || !allAccepted) {
      setMessage("Atas, Tengah, dan Bawah harus ACCEPT terlebih dahulu.");
      return;
    }
    try {
      setBusy("fruit-complete");
      const r = await postJSON<{ state: M5State; white_check_required: boolean }>(
        `${targetFlask}/api/m5/fruit-completed`,
        { session_id: sessionId, fruit_id: fruitId.trim() }
      );
      setState(r.state);
      setPositionQC({ Atas: null, Tengah: null, Bawah: null });
      setMessage(
        r.white_check_required
          ? "Buah ke-5 selesai. WHITE CHECK sekarang wajib."
          : "Buah selesai dicatat. Masukkan Fruit ID berikutnya."
      );
    } catch (e: any) {
      setMessage(`Gagal menutup buah: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function doWhiteCheck() {
    if (!state) return;
    try {
      setBusy("white-check");
      setMessage("Mengambil WHITE CHECK 5x lalu menghitung drift...");
      const scan = await scanReference("WHITE", "DRIFT_CHECK_WHITE");
      if (!scan.reference_id) throw new Error("reference_id white check tidak diterima");

      const drift = await postJSON<DriftResponse>(`${targetFlask}/api/m5/drift`, {
        session_id: sessionId,
        baseline_reference_id: state.active_white_reference_id,
        baseline_dark_reference_id: state.active_dark_reference_id,
        check_reference_id: scan.reference_id,
      });

      setLastDrift(drift);
      if (drift.state) setState(drift.state);
      setMessage(
        `Drift median ${drift.median_drift_percent?.toFixed(2)}% - ${drift.drift_status}`
      );
    } catch (e: any) {
      setMessage(`Gagal white check: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function doRecalDark() {
    try {
      setBusy("recal-dark");
      const r = await scanReference("DARK", "RECAL_DARK");
      setRecalDark(r.reference_id || "");
      setMessage(`DARK recalibration selesai: ${r.reference_id}`);
    } catch (e: any) {
      setMessage(`Gagal DARK recalibration: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function doRecalWhite() {
    try {
      setBusy("recal-white");
      const r = await scanReference("WHITE", "RECAL_WHITE");
      setRecalWhite(r.reference_id || "");
      setMessage(`WHITE recalibration selesai: ${r.reference_id}`);
    } catch (e: any) {
      setMessage(`Gagal WHITE recalibration: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function applyRecalibration() {
    if (!recalDark || !recalWhite) {
      return setMessage("Scan DARK dan WHITE recalibration terlebih dahulu.");
    }
    try {
      setBusy("apply-recal");
      const r = await postJSON<{ state: M5State }>(`${targetFlask}/api/m5/recalibration`, {
        session_id: sessionId,
        drift_check_id: lastDrift?.drift_check_id,
        new_dark_reference_id: recalDark,
        new_white_reference_id: recalWhite,
        reason: lastDrift?.drift_status || "MANUAL",
      });
      setState(r.state);
      setRecalDark("");
      setRecalWhite("");
      setLastDrift(null);
      setMessage("Recalibration diterapkan. Baseline aktif sudah diperbarui.");
    } catch (e: any) {
      setMessage(`Gagal menerapkan recalibration: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function doFinalWhite() {
    try {
      setBusy("final-white");
      const r = await scanReference("WHITE", "FINAL_WHITE");
      setFinalWhite(r.reference_id || "");
      setMessage(`FINAL WHITE selesai: ${r.reference_id}`);
    } catch (e: any) {
      setMessage(`Gagal FINAL WHITE: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function doFinalDark() {
    try {
      setBusy("final-dark");
      const r = await scanReference("DARK", "FINAL_DARK");
      setFinalDark(r.reference_id || "");
      setMessage(`FINAL DARK selesai: ${r.reference_id}`);
    } catch (e: any) {
      setMessage(`Gagal FINAL DARK: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  async function finishSession() {
    if (!state) return;
    try {
      setBusy("finish");
      const r = await postJSON<{ state: M5State }>(`${targetFlask}/api/m5/session/finish`, {
        session_id: sessionId,
        final_white_reference_id: finalWhite,
        final_dark_reference_id: finalDark,
      });
      setState(r.state);
      setMessage("Sesi M5 ditutup.");
    } catch (e: any) {
      setMessage(`Gagal menutup sesi: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  const blocked = !!busy;
  const needsCheck = !!state?.white_check_required;
  const needsRecal = !!state?.recalibration_required;

  return (
    <main className={styles.page}>
      <section className={styles.header}>
        <div>
          <p className={styles.eyebrow}>NECTA · AS7265X</p>
          <h1>M5 Acquisition & Drift Control</h1>
          <p>Gain 64× · Integration 49 · LED controlled · QC + drift + recalibration</p>
        </div>
        <button className={styles.secondaryButton} onClick={() => refreshState()} disabled={blocked}>
          Refresh State
        </button>
      </section>

      <section className={styles.statusBar}>
        <strong>{message}</strong>
      </section>

      <section className={styles.grid2}>
        <div className={styles.card}>
          <h2>1. Sesi & Baseline</h2>
          <label>Session ID</label>
          <input value={sessionId} onChange={(e) => setSessionId(e.target.value)} disabled={!!state} />

          <div className={styles.actionRow}>
            <button onClick={doBaselineDark} disabled={blocked || !!state}>Scan DARK Baseline 5×</button>
            <button onClick={doBaselineWhite} disabled={blocked || !!state}>Scan WHITE Baseline 5×</button>
          </div>

          <div className={styles.refBox}>
            <div><span>DARK</span><code>{baselineDark || state?.active_dark_reference_id || "-"}</code></div>
            <div><span>WHITE</span><code>{baselineWhite || state?.active_white_reference_id || "-"}</code></div>
          </div>

          {!state && (
            <button className={styles.primaryButton} onClick={startSession} disabled={blocked || !baselineDark || !baselineWhite}>
              Mulai Sesi M5
            </button>
          )}
        </div>

        <div className={styles.card}>
          <h2>Status M5</h2>
          <div className={styles.metrics}>
            <Metric label="Status" value={state?.status || "BELUM MULAI"} />
            <Metric label="Buah total" value={String(state?.fruit_count_total ?? 0)} />
            <Metric label="Sejak white check" value={`${state?.fruits_since_white_check ?? 0} / 5`} />
            <Metric label="White check" value={needsCheck ? "WAJIB" : "Belum"} danger={needsCheck} />
            <Metric label="Recalibration" value={needsRecal ? "WAJIB" : "Tidak"} danger={needsRecal} />
            <Metric label="Drift terakhir" value={state?.last_drift_percent != null ? `${Number(state.last_drift_percent).toFixed(2)}%` : "-"} />
          </div>
        </div>
      </section>

      <section className={styles.card}>
        <h2>2. Scan Buah + QC Real-time</h2>
        <div className={styles.fruitRow}>
          <label>Fruit ID</label>
          <input value={fruitId} onChange={(e) => setFruitId(e.target.value)} disabled={!state || blocked || needsCheck || needsRecal} />
        </div>

        {(needsCheck || needsRecal) && (
          <div className={styles.warning}>
            Scan sample dikunci sampai {needsRecal ? "recalibration" : "WHITE CHECK"} selesai.
          </div>
        )}

        <div className={styles.positionGrid}>
          {(["Atas", "Tengah", "Bawah"] as Position[]).map((p) => (
            <div className={styles.positionCard} key={p}>
              <h3>{p}</h3>
              <button
                onClick={() => scanSample(p)}
                disabled={!state || blocked || needsCheck || needsRecal || state.status !== "ACTIVE"}
              >
                {positionQC[p]?.repeat_recommended ? "Ulangi 5 Scan" : "Scan 5×"}
              </button>
              <QCView result={positionQC[p]} />
            </div>
          ))}
        </div>

        <button className={styles.primaryButton} onClick={completeFruit} disabled={!allAccepted || blocked || !state}>
          Tandai Buah Selesai
        </button>
      </section>

      <section className={styles.grid2}>
        <div className={styles.card}>
          <h2>3. WHITE CHECK setiap 5 buah</h2>
          <p>Gunakan white reference dengan geometri dan LED yang sama seperti baseline.</p>
          <button className={styles.primaryButton} onClick={doWhiteCheck} disabled={!state || blocked || !needsCheck || needsRecal}>
            Scan WHITE CHECK + Hitung Drift
          </button>

          {lastDrift && (
            <div className={styles.driftBox}>
              <Metric label="Median drift" value={`${lastDrift.median_drift_percent?.toFixed(2)}%`} />
              <Metric label="Max drift" value={`${lastDrift.max_drift_percent?.toFixed(2)}%`} />
              <Metric label="Status" value={lastDrift.drift_status || "-"} danger={!!lastDrift.recalibration_required} />
              <Metric label="Method" value={lastDrift.drift_method || "-"} />
            </div>
          )}
        </div>

        <div className={styles.card}>
          <h2>4. Recalibration</h2>
          <p>Aktif ketika drift berstatus RECALIBRATE atau STOP_CHECK.</p>
          <div className={styles.actionRow}>
            <button onClick={doRecalDark} disabled={!state || blocked || !needsRecal}>Scan DARK Recal 5×</button>
            <button onClick={doRecalWhite} disabled={!state || blocked || !needsRecal}>Scan WHITE Recal 5×</button>
          </div>
          <div className={styles.refBox}>
            <div><span>DARK baru</span><code>{recalDark || "-"}</code></div>
            <div><span>WHITE baru</span><code>{recalWhite || "-"}</code></div>
          </div>
          <button className={styles.dangerButton} onClick={applyRecalibration} disabled={blocked || !needsRecal || !recalDark || !recalWhite}>
            Terapkan Baseline Baru
          </button>
        </div>
      </section>

      <section className={styles.card}>
        <h2>5. Akhir Sesi</h2>
        <div className={styles.actionRow}>
          <button onClick={doFinalWhite} disabled={!state || blocked || state.status !== "ACTIVE"}>FINAL WHITE 5×</button>
          <button onClick={doFinalDark} disabled={!state || blocked || state.status !== "ACTIVE"}>FINAL DARK 5×</button>
          <button className={styles.primaryButton} onClick={finishSession} disabled={!state || blocked || state.status !== "ACTIVE"}>
            Tutup Sesi M5
          </button>
        </div>
        <div className={styles.refBox}>
          <div><span>FINAL WHITE</span><code>{finalWhite || "-"}</code></div>
          <div><span>FINAL DARK</span><code>{finalDark || "-"}</code></div>
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className={`${styles.metric} ${danger ? styles.metricDanger : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function QCView({ result }: { result: ScanResponse | null }) {
  if (!result) return <p className={styles.muted}>Belum ada scan.</p>;
  const bad = result.qc_status !== "ACCEPT";
  return (
    <div className={`${styles.qcBox} ${bad ? styles.qcBad : styles.qcGood}`}>
      <div>Median CV: <strong>{result.qc_median_cv != null ? `${Number(result.qc_median_cv).toFixed(2)}%` : "-"}</strong></div>
      <div>Status: <strong>{result.qc_status || "-"}</strong></div>
      {result.repeat_recommended && <div>Ulangi titik yang sama sebelum lanjut.</div>}
    </div>
  );
}
