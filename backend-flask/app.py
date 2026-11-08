"""
NECTA AS7265X Flask API v4-M5
=============================

Fungsi:
1. menerima scan ESP32 -> public.spectral_scans_v2
2. menyimpan metadata M5 reference_role
3. mengelola state sesi M5
4. menghitung drift WHITE CHECK terhadap active WHITE baseline
5. memutuskan ACCEPT / WARNING / RECALIBRATE / STOP_CHECK
6. mencatat recalibration dan mengganti active baseline

Drift threshold pilot (bukan standar resmi):
  <= 3%      ACCEPT
  >3 - 5%    WARNING
  >5 - 10%   RECALIBRATE
  >10%       STOP_CHECK
"""

import os
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Load Model Machine Learning PLSR & Calibration Bundle
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "MODEL", "output_model"))
try:
    ml_model = joblib.load(os.path.join(MODEL_DIR, "model_plsr_nanas.pkl"))
    ml_scaler = joblib.load(os.path.join(MODEL_DIR, "scaler_nanas.pkl"))
    ml_bundle = joblib.load(os.path.join(MODEL_DIR, "calibration_bundle.pkl"))
    print(f"[ML] Model PLSR & Calibration Bundle berhasil dimuat dari {MODEL_DIR}")
except Exception as _e:
    ml_model, ml_scaler, ml_bundle = None, None, None
    print(f"[ML] Peringatan: Model PLSR belum dapat dimuat: {_e}")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")
SCAN_TABLE = os.getenv("SUPABASE_TABLE", "spectral_scans_v2")

WAVELENGTHS = [
    410, 435, 460, 485, 510, 535,
    560, 585, 610, 645, 680, 705,
    730, 760, 810, 860, 900, 940,
]

VALID_MODELS = {"M0", "M1", "M2", "M3", "M4", "M5"}
VALID_TYPES = {"SAMPLE", "WHITE", "DARK"}
VALID_485_STATUS = {"OK", "INTERMITTENT_READ", "RAW_ZERO_CAL_NONZERO", "ZERO"}
VALID_485_SOURCE = {"FIRST", "CHECK2", "CHECK3", "NONE"}
VALID_QC_STATUS = {
    "SINGLE_SCAN", "REFERENCE", "POSTHOC_ONLY",
    "ACCEPT", "WATCH_REPEAT", "FLAG", "LOW_SIGNAL",
}
VALID_REFERENCE_ROLES = {
    "NONE", "BASELINE_DARK", "BASELINE_WHITE", "DRIFT_CHECK_WHITE",
    "RECAL_DARK", "RECAL_WHITE", "FINAL_WHITE", "FINAL_DARK",
}

EXPECTED_GAIN = 64
EXPECTED_INTEGRATION = 49
DRIFT_ACCEPT_MAX = 3.0
DRIFT_WARNING_MAX = 5.0
DRIFT_RECAL_MAX = 10.0


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/<path:path>", methods=["OPTIONS"])
def options_any(path):
    return ("", 204)


@app.route("/api/m5/session/state/ping", methods=["GET"])
def ping():
    return jsonify({"status": "online", "service": "NECTA AS7265X Flask API v4-M5"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_config():
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL belum diset")
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY belum diset")


def headers(prefer: Optional[str] = None) -> Dict[str, str]:
    require_config()
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def sb_get(table: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers(),
        params=params,
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase GET {table} {r.status_code}: {r.text}")
    return r.json()


def sb_insert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers("return=representation"),
        json=payload,
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase INSERT {table} {r.status_code}: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


def sb_upsert(table: str, payload: Dict[str, Any], on_conflict: str) -> Dict[str, Any]:
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers("resolution=merge-duplicates,return=representation"),
        params={"on_conflict": on_conflict},
        json=payload,
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase UPSERT {table} {r.status_code}: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


def sb_patch(table: str, filters: Dict[str, str], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    params = {k: f"eq.{v}" for k, v in filters.items()}
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers("return=representation"),
        params=params,
        json=payload,
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Supabase PATCH {table} {r.status_code}: {r.text}")
    return r.json()


def clean_text(value: Any, field: str, required: bool = False) -> str:
    result = "" if value is None else str(value).strip()
    if required and not result:
        raise ValueError(f"{field} wajib diisi")
    return result


def to_int(value: Any, field: str, min_value=None, max_value=None, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field} wajib diisi")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} harus integer")
    if min_value is not None and result < min_value:
        raise ValueError(f"{field} minimal {min_value}")
    if max_value is not None and result > max_value:
        raise ValueError(f"{field} maksimal {max_value}")
    return result


def to_float(value: Any, field: str, allow_none=False):
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field} wajib diisi")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} harus numerik")


def to_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "TRUE", "True"):
        return True
    if value in (0, "0", "false", "FALSE", "False"):
        return False
    raise ValueError(f"{field} harus boolean")


def validate_scan_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    model = clean_text(data.get("model"), "model", True).upper()
    if model not in VALID_MODELS:
        raise ValueError("model harus M0-M5")

    measurement_type = clean_text(data.get("measurement_type"), "measurement_type", True).upper()
    if measurement_type not in VALID_TYPES:
        raise ValueError("measurement_type harus SAMPLE/WHITE/DARK")

    session_id = clean_text(data.get("session_id"), "session_id", False) or "S01"
    fruit_id = clean_text(data.get("id_nanas"), "id_nanas", False) or "N01"
    position = clean_text(data.get("position"), "position", False)

    if measurement_type == "SAMPLE":
        if position not in {"Atas", "Tengah", "Bawah"}:
            position = "Atas"
    else:
        position = "REFERENCE"

    reference_role = clean_text(data.get("reference_role"), "reference_role") or "NONE"
    reference_role = reference_role.upper()
    if reference_role not in VALID_REFERENCE_ROLES:
        reference_role = "NONE"

    if model == "M5":
        if measurement_type == "SAMPLE":
            reference_role = "NONE"
    else:
        reference_role = "NONE"

    repeat_no = to_int(data.get("repeat_no"), "repeat_no", 1, 10)
    gain_x = to_int(data.get("gain_x"), "gain_x", 1, 64)
    integration_cycles = to_int(data.get("integration_cycles"), "integration_cycles", 0, 255)

    led_white = to_bool(data.get("led_white", False), "led_white")
    led_ir = to_bool(data.get("led_ir", False), "led_ir")
    led_uv = to_bool(data.get("led_uv", False), "led_uv")

    distance = data.get("distance_mm")
    if distance is not None:
        distance = to_int(distance, "distance_mm", allow_none=True)
        if distance is not None and distance <= 0:
            distance = None

    row = {
        "received_at": now_iso(),
        "firmware_version": clean_text(data.get("firmware_version"), "firmware_version") or None,
        "session_id": session_id,
        "fruit_id": fruit_id or None,
        "model": model,
        "measurement_type": measurement_type,
        "position": position,
        "repeat_no": repeat_no,
        "reference_id": clean_text(data.get("reference_id"), "reference_id") or None,
        "reference_role": reference_role,
        "dark_reference_id": clean_text(data.get("dark_reference_id"), "dark_reference_id") or None,
        "white_reference_id": clean_text(data.get("white_reference_id"), "white_reference_id") or None,
        "gain_x": gain_x,
        "integration_cycles": integration_cycles,
        "integration_ms_est": to_float(data.get("integration_ms_est"), "integration_ms_est", True),
        "measurement_mode": clean_text(data.get("measurement_mode"), "measurement_mode") or None,
        "illumination_mode": clean_text(data.get("illumination_mode"), "illumination_mode", False).upper() or ("DARK" if measurement_type == "DARK" else ("ONBOARD" if model in {"M3","M4","M5"} else "AMBIENT")),
        "led_white": led_white,
        "led_ir": led_ir,
        "led_uv": led_uv,
        "led_current_ma": to_float(data.get("led_current_ma"), "led_current_ma", True),
        "distance_mm": distance,
        "uptime_ms": to_int(data.get("uptime_ms"), "uptime_ms", 0, allow_none=True),
        "data_type": "RAW+CAL",
    }

    for wl in WAVELENGTHS:
        row[f"raw_{wl}"] = to_int(data.get(f"raw_{wl}"), f"raw_{wl}", 0, 65535)
        row[f"cal_{wl}"] = to_float(data.get(f"cal_{wl}"), f"cal_{wl}")

    raw_485_val = row.get("raw_485", 0)
    check2_raw = data.get("raw_485_check2") if data.get("raw_485_check2") is not None else raw_485_val
    check3_raw = data.get("raw_485_check3") if data.get("raw_485_check3") is not None else raw_485_val
    sel_raw = data.get("raw_485_selected") if data.get("raw_485_selected") is not None else raw_485_val

    row["raw_485_check2"] = to_int(check2_raw, "raw_485_check2", 0, 65535)
    row["raw_485_check3"] = to_int(check3_raw, "raw_485_check3", 0, 65535)
    row["raw_485_selected"] = to_int(sel_raw, "raw_485_selected", 0, 65535)

    raw_source = clean_text(data.get("raw_485_source"), "raw_485_source") or "FIRST"
    if raw_source not in VALID_485_SOURCE:
        raw_source = "FIRST"
    row["raw_485_source"] = raw_source

    raw_status = clean_text(data.get("raw_485_status"), "raw_485_status") or "OK"
    if raw_status not in VALID_485_STATUS:
        raw_status = "OK"
    row["raw_485_status"] = raw_status
    row["raw_485_flag"] = raw_status != "OK"

    row["qc_group_id"] = clean_text(data.get("qc_group_id"), "qc_group_id") or None
    row["qc_median_cv"] = to_float(data.get("qc_median_cv"), "qc_median_cv", True)
    row["qc_max_cv"] = to_float(data.get("qc_max_cv"), "qc_max_cv", True)
    row["qc_valid_channels"] = to_int(data.get("qc_valid_channels"), "qc_valid_channels", 0, 18, allow_none=True)
    row["qc_channels_over_10"] = to_int(data.get("qc_channels_over_10"), "qc_channels_over_10", 0, 18, allow_none=True)
    row["qc_applied"] = to_bool(data.get("qc_applied", False), "qc_applied")

    qc_status = clean_text(data.get("qc_status"), "qc_status") or "SINGLE_SCAN"
    if qc_status not in VALID_QC_STATUS:
        qc_status = "SINGLE_SCAN"
    row["qc_status"] = qc_status

    return row


def fetch_reference_mean(
    reference_id: str,
    measurement_type: str,
    expected_role: Optional[str] = None,
) -> Dict[int, float]:
    if not reference_id:
        raise ValueError("reference_id kosong")

    select = ",".join(["repeat_no", "reference_role"] + [f"cal_{wl}" for wl in WAVELENGTHS])
    params = {
        "select": select,
        "reference_id": f"eq.{reference_id}",
        "model": "eq.M5",
        "measurement_type": f"eq.{measurement_type}",
        "order": "repeat_no.asc",
    }
    if expected_role:
        params["reference_role"] = f"eq.{expected_role}"

    rows = sb_get(SCAN_TABLE, params)

    if len(rows) < 5:
        raise ValueError(
            f"Reference {reference_id} hanya memiliki {len(rows)} scan; diperlukan 5 scan"
        )

    means: Dict[int, float] = {}
    for wl in WAVELENGTHS:
        vals = [float(r[f"cal_{wl}"]) for r in rows if r.get(f"cal_{wl}") is not None]
        if not vals:
            raise ValueError(f"cal_{wl} kosong pada reference {reference_id}")
        means[wl] = sum(vals) / len(vals)
    return means


def drift_status(median_drift: float):
    if median_drift <= DRIFT_ACCEPT_MAX:
        return "ACCEPT", False
    if median_drift <= DRIFT_WARNING_MAX:
        return "WARNING", False
    if median_drift <= DRIFT_RECAL_MAX:
        return "RECALIBRATE", True
    return "STOP_CHECK", True


def get_m5_state(session_id: str) -> Optional[Dict[str, Any]]:
    rows = sb_get(
        "m5_session_state",
        {"select": "*", "session_id": f"eq.{session_id}", "limit": 1},
    )
    return rows[0] if rows else None


def hitung_prediksi_brix(scan_dict: Dict[str, Any]) -> Optional[float]:
    """
    Menghitung prediksi °Brix menggunakan model PLSR terlatih.
    Preprocessing: Absorbansi A = -log10((S - D)/(W - D)) -> SNV -> StandardScaler -> PLSR
    """
    if ml_model is None or ml_scaler is None or ml_bundle is None:
        return None
    try:
        feature_cols = ml_bundle.get("feature_cols", [
            'ch_a', 'ch_b', 'ch_c', 'ch_d', 'ch_e', 'ch_f',
            'ch_g', 'ch_h', 'ch_i', 'ch_j', 'ch_k', 'ch_l',
            'ch_r', 'ch_s', 'ch_t', 'ch_u', 'ch_v', 'ch_w'
        ])
        
        # Ekstrak 18 kanal dari payload (raw_*, cal_*, atau ch_*)
        vals = []
        if any(f"raw_{wl}" in scan_dict for wl in WAVELENGTHS):
            vals = [float(scan_dict.get(f"raw_{wl}", 0)) for wl in WAVELENGTHS]
        elif any(f"cal_{wl}" in scan_dict for wl in WAVELENGTHS):
            vals = [float(scan_dict.get(f"cal_{wl}", 0)) for wl in WAVELENGTHS]
        elif any(col in scan_dict for col in feature_cols):
            vals = [float(scan_dict.get(col, 0)) for col in feature_cols]
        else:
            return None

        S = np.array(vals, dtype=float)
        W = np.array(ml_bundle["white_reference"], dtype=float)
        D = np.array(ml_bundle["dark_reference"], dtype=float)

        denom = np.where(np.abs(W - D) < 1e-5, 1e-5, W - D)
        R = np.clip((S - D) / denom, 1e-4, 2.0)
        A = -np.log10(R).reshape(1, -1)

        mean_a = np.mean(A, axis=1, keepdims=True)
        std_a = np.std(A, axis=1, keepdims=True)
        std_a = np.where(std_a == 0, 1e-6, std_a)
        X_snv = (A - mean_a) / std_a

        X_scaled = ml_scaler.transform(X_snv)
        pred = ml_model.predict(X_scaled).flatten()[0]
        return round(float(pred), 2)
    except Exception as e:
        app.logger.warning(f"Gagal memprediksi Brix: {e}")
        return None


LATEST_PREDICTION: Dict[str, Any] = {
    "prediksi_brix": None,
    "fruit_id": None,
    "position": None,
    "model": None,
    "timestamp": None,
}


@app.get("/api/latest_prediction")
def get_latest_prediction():
    return jsonify(LATEST_PREDICTION), 200


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        service="NECTA AS7265X M0-M5 v4-M5",
        ml_model_loaded=ml_model is not None,
        scan_table=SCAN_TABLE,
        expected_gain=EXPECTED_GAIN,
        expected_integration_cycles=EXPECTED_INTEGRATION,
        drift_thresholds={
            "accept_max": DRIFT_ACCEPT_MAX,
            "warning_max": DRIFT_WARNING_MAX,
            "recalibrate_max": DRIFT_RECAL_MAX,
        },
    )


@app.post("/api/predict_brix")
def api_predict_brix():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify(status="error", pesan="Payload JSON kosong"), 400
        pred_brix = hitung_prediksi_brix(data)
        if pred_brix is None:
            return jsonify(
                status="error",
                pesan="Gagal menghitung prediksi Brix. Pastikan 18 kanal spektral (raw_*, cal_*, atau ch_*) terisi."
            ), 400
        return jsonify(
            status="sukses",
            prediksi_brix=pred_brix,
            model="PLSR AS7265X (SNV + Absorbance)"
        ), 200
    except Exception as exc:
        return jsonify(status="error", pesan=str(exc)), 500


@app.post("/api/simpan_sensor")
def simpan_sensor():
    try:
        row = validate_scan_payload(request.get_json(force=True))
        inserted = sb_insert(SCAN_TABLE, row)

        pred_brix = None
        if row.get("measurement_type") == "SAMPLE":
            pred_brix = hitung_prediksi_brix(row)
            if pred_brix is not None:
                LATEST_PREDICTION["prediksi_brix"] = pred_brix
                LATEST_PREDICTION["fruit_id"] = row.get("fruit_id")
                LATEST_PREDICTION["position"] = row.get("position")
                LATEST_PREDICTION["model"] = row.get("model")
                LATEST_PREDICTION["timestamp"] = now_iso()

        return jsonify(
            status="sukses",
            prediksi_brix=pred_brix,
            id=inserted.get("id") if isinstance(inserted, dict) else None,
            session_id=row["session_id"],
            model=row["model"],
            measurement_type=row["measurement_type"],
            reference_id=row["reference_id"],
            reference_role=row["reference_role"],
            qc_group_id=row["qc_group_id"],
            qc_median_cv=row["qc_median_cv"],
            qc_status=row["qc_status"],
            raw_485_selected=row["raw_485_selected"],
            raw_485_status=row["raw_485_status"],
        ), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500


@app.post("/api/simpan_sensor_batch")
def simpan_sensor_batch():
    try:
        raw_data = request.get_json(force=True)
        items = raw_data.get("measurements", raw_data) if isinstance(raw_data, dict) else raw_data
        if not isinstance(items, list) or len(items) == 0:
            return jsonify(status="error", jenis="validasi", pesan="Data batch harus berupa list/array"), 400

        validated_rows = [validate_scan_payload(item) for item in items]
        inserted = sb_insert(SCAN_TABLE, validated_rows)

        first_row = validated_rows[0]

        pred_brix = None
        for item in reversed(validated_rows):
            if item.get("measurement_type") == "SAMPLE":
                pred_brix = hitung_prediksi_brix(item)
                if pred_brix is not None:
                    LATEST_PREDICTION["prediksi_brix"] = pred_brix
                    LATEST_PREDICTION["fruit_id"] = item.get("fruit_id")
                    LATEST_PREDICTION["position"] = item.get("position")
                    LATEST_PREDICTION["model"] = item.get("model")
                    LATEST_PREDICTION["timestamp"] = now_iso()
                    break

        return jsonify(
            status="sukses",
            prediksi_brix=pred_brix,
            count=len(validated_rows),
            session_id=first_row["session_id"],
            model=first_row["model"],
            measurement_type=first_row["measurement_type"],
            reference_id=first_row["reference_id"],
            reference_role=first_row["reference_role"],
            qc_group_id=first_row["qc_group_id"],
            qc_median_cv=first_row["qc_median_cv"],
            qc_status=first_row["qc_status"],
        ), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500



@app.post("/api/m5/session/start")
def m5_session_start():
    try:
        data = request.get_json(force=True)
        session_id = clean_text(data.get("session_id"), "session_id", True)
        dark_ref = clean_text(data.get("baseline_dark_reference_id"), "baseline_dark_reference_id", True)
        white_ref = clean_text(data.get("baseline_white_reference_id"), "baseline_white_reference_id", True)

        # Jangan reset sesi ACTIVE yang sudah ada.
        existing_state = get_m5_state(session_id)
        if existing_state and existing_state.get("status") == "ACTIVE":
            return jsonify(status="sukses", existing=True, state=existing_state), 200

        # Verifikasi reference tersedia, lengkap, dan berperan sebagai baseline.
        fetch_reference_mean(dark_ref, "DARK", "BASELINE_DARK")
        fetch_reference_mean(white_ref, "WHITE", "BASELINE_WHITE")

        payload = {
            "session_id": session_id,
            "status": "ACTIVE",
            "active_dark_reference_id": dark_ref,
            "active_white_reference_id": white_ref,
            "fruit_count_total": 0,
            "fruits_since_white_check": 0,
            "white_check_required": False,
            "recalibration_required": False,
            "last_check_reference_id": None,
            "last_drift_percent": None,
            "last_drift_status": None,
            "updated_at": now_iso(),
        }
        state = sb_upsert("m5_session_state", payload, "session_id")
        return jsonify(status="sukses", state=state), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500


@app.get("/api/m5/session/state/<session_id>")
def m5_session_state(session_id):
    try:
        state = get_m5_state(session_id)
        if not state:
            return jsonify(status="not_found", session_id=session_id), 404
        return jsonify(status="sukses", state=state), 200
    except Exception as exc:
        return jsonify(status="error", pesan=str(exc)), 500


@app.post("/api/m5/fruit-completed")
def m5_fruit_completed():
    try:
        data = request.get_json(force=True)
        session_id = clean_text(data.get("session_id"), "session_id", True)
        fruit_id = clean_text(data.get("fruit_id"), "fruit_id", True)

        state = get_m5_state(session_id)
        if not state:
            raise ValueError("Sesi M5 belum dimulai")
        if state.get("status") != "ACTIVE":
            raise ValueError("Sesi M5 tidak aktif")
        if state.get("recalibration_required"):
            raise ValueError("Recalibration wajib diselesaikan sebelum buah berikutnya")
        if state.get("white_check_required"):
            raise ValueError("White check wajib dilakukan sebelum buah berikutnya")

        # Backend juga memverifikasi bahwa Atas/Tengah/Bawah sudah ACCEPT
        # dengan baseline aktif saat ini.
        accepted_rows = sb_get(
            SCAN_TABLE,
            {
                "select": "position,qc_status,qc_group_id",
                "session_id": f"eq.{session_id}",
                "fruit_id": f"eq.{fruit_id}",
                "model": "eq.M5",
                "measurement_type": "eq.SAMPLE",
                "white_reference_id": f"eq.{state.get('active_white_reference_id')}",
                "qc_status": "eq.ACCEPT",
            },
        )
        accepted_positions = {r.get("position") for r in accepted_rows}
        if not {"Atas", "Tengah", "Bawah"}.issubset(accepted_positions):
            raise ValueError("Buah belum memiliki QC ACCEPT untuk Atas, Tengah, dan Bawah")

        existing = sb_get(
            "m5_completed_fruits",
            {
                "select": "id,fruit_id",
                "session_id": f"eq.{session_id}",
                "fruit_id": f"eq.{fruit_id}",
                "limit": 1,
            },
        )
        if existing:
            return jsonify(status="sukses", duplicate=True, state=state), 200

        sb_insert(
            "m5_completed_fruits",
            {"session_id": session_id, "fruit_id": fruit_id, "completed_at": now_iso()},
        )

        total = int(state.get("fruit_count_total") or 0) + 1
        since = int(state.get("fruits_since_white_check") or 0) + 1
        required = since >= 5

        updated = sb_patch(
            "m5_session_state",
            {"session_id": session_id},
            {
                "fruit_count_total": total,
                "fruits_since_white_check": since,
                "white_check_required": required,
                "updated_at": now_iso(),
            },
        )
        return jsonify(
            status="sukses",
            duplicate=False,
            white_check_required=required,
            state=updated[0] if updated else None,
        ), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500


@app.post("/api/m5/drift")
def m5_drift():
    try:
        data = request.get_json(force=True)
        session_id = clean_text(data.get("session_id"), "session_id", True)
        check_ref = clean_text(data.get("check_reference_id"), "check_reference_id", True)

        state = get_m5_state(session_id)
        if not state:
            raise ValueError("Sesi M5 belum dimulai")

        baseline_ref = clean_text(
            data.get("baseline_reference_id") or state.get("active_white_reference_id"),
            "baseline_reference_id",
            True,
        )

        baseline_dark_ref = clean_text(
            data.get("baseline_dark_reference_id") or state.get("active_dark_reference_id"),
            "baseline_dark_reference_id",
        )
        check_dark_ref = clean_text(data.get("check_dark_reference_id"), "check_dark_reference_id")

        baseline_white = fetch_reference_mean(baseline_ref, "WHITE")
        check_white = fetch_reference_mean(check_ref, "WHITE", "DRIFT_CHECK_WHITE")

        method = "WHITE_ONLY"
        baseline_signal = dict(baseline_white)
        check_signal = dict(check_white)

        # Bila DARK awal dan DARK check sama-sama tersedia, gunakan W-D.
        if baseline_dark_ref and check_dark_ref:
            baseline_dark = fetch_reference_mean(baseline_dark_ref, "DARK")
            check_dark = fetch_reference_mean(check_dark_ref, "DARK")
            baseline_signal = {wl: baseline_white[wl] - baseline_dark[wl] for wl in WAVELENGTHS}
            check_signal = {wl: check_white[wl] - check_dark[wl] for wl in WAVELENGTHS}
            method = "WHITE_MINUS_DARK"

        channel_drift: Dict[str, float] = {}
        valid: List[float] = []
        for wl in WAVELENGTHS:
            b = baseline_signal[wl]
            c = check_signal[wl]
            if abs(b) < 1e-9:
                continue
            drift = abs(c - b) / abs(b) * 100.0
            channel_drift[str(wl)] = drift
            valid.append(drift)

        if not valid:
            raise ValueError("Tidak ada kanal valid untuk menghitung drift")

        median_drift = statistics.median(valid)
        max_drift = max(valid)
        status, recal_required = drift_status(median_drift)

        drift_row = sb_insert(
            "m5_drift_checks",
            {
                "session_id": session_id,
                "baseline_reference_id": baseline_ref,
                "check_reference_id": check_ref,
                "baseline_dark_reference_id": baseline_dark_ref or None,
                "check_dark_reference_id": check_dark_ref or None,
                "drift_method": method,
                "median_drift_percent": median_drift,
                "max_drift_percent": max_drift,
                "valid_channels": len(valid),
                "channel_drift": channel_drift,
                "baseline_mean": {str(k): v for k, v in baseline_signal.items()},
                "check_mean": {str(k): v for k, v in check_signal.items()},
                "drift_status": status,
                "recalibration_required": recal_required,
                "checked_at": now_iso(),
            },
        )

        state_patch = {
            "last_check_reference_id": check_ref,
            "last_drift_percent": median_drift,
            "last_drift_status": status,
            "recalibration_required": recal_required,
            "white_check_required": recal_required,
            "updated_at": now_iso(),
        }

        # Jika drift masih diterima / warning, check selesai dan hitungan 5 buah direset.
        if not recal_required:
            state_patch["fruits_since_white_check"] = 0
            state_patch["white_check_required"] = False

        updated = sb_patch("m5_session_state", {"session_id": session_id}, state_patch)

        return jsonify(
            status="sukses",
            drift_check_id=drift_row.get("id"),
            drift_method=method,
            median_drift_percent=median_drift,
            max_drift_percent=max_drift,
            valid_channels=len(valid),
            drift_status=status,
            recalibration_required=recal_required,
            channel_drift=channel_drift,
            state=updated[0] if updated else None,
        ), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500


@app.post("/api/m5/recalibration")
def m5_recalibration():
    try:
        data = request.get_json(force=True)
        session_id = clean_text(data.get("session_id"), "session_id", True)
        new_dark = clean_text(data.get("new_dark_reference_id"), "new_dark_reference_id", True)
        new_white = clean_text(data.get("new_white_reference_id"), "new_white_reference_id", True)
        drift_check_id = data.get("drift_check_id")

        state = get_m5_state(session_id)
        if not state:
            raise ValueError("Sesi M5 belum dimulai")

        fetch_reference_mean(new_dark, "DARK", "RECAL_DARK")
        fetch_reference_mean(new_white, "WHITE", "RECAL_WHITE")

        rec = sb_insert(
            "m5_recalibrations",
            {
                "session_id": session_id,
                "drift_check_id": drift_check_id,
                "old_dark_reference_id": state.get("active_dark_reference_id"),
                "old_white_reference_id": state.get("active_white_reference_id"),
                "new_dark_reference_id": new_dark,
                "new_white_reference_id": new_white,
                "reason": clean_text(data.get("reason"), "reason") or state.get("last_drift_status") or "MANUAL",
                "recalibrated_at": now_iso(),
            },
        )

        updated = sb_patch(
            "m5_session_state",
            {"session_id": session_id},
            {
                "active_dark_reference_id": new_dark,
                "active_white_reference_id": new_white,
                "fruits_since_white_check": 0,
                "white_check_required": False,
                "recalibration_required": False,
                "last_check_reference_id": None,
                "last_drift_percent": None,
                "last_drift_status": "RECALIBRATED",
                "updated_at": now_iso(),
            },
        )

        return jsonify(
            status="sukses",
            recalibration_id=rec.get("id"),
            state=updated[0] if updated else None,
        ), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500


@app.post("/api/m5/session/finish")
def m5_session_finish():
    try:
        data = request.get_json(force=True)
        session_id = clean_text(data.get("session_id"), "session_id", True)
        final_white = clean_text(data.get("final_white_reference_id"), "final_white_reference_id")
        final_dark = clean_text(data.get("final_dark_reference_id"), "final_dark_reference_id")

        if final_white:
            fetch_reference_mean(final_white, "WHITE", "FINAL_WHITE")
        if final_dark:
            fetch_reference_mean(final_dark, "DARK", "FINAL_DARK")

        updated = sb_patch(
            "m5_session_state",
            {"session_id": session_id},
            {
                "status": "CLOSED",
                "final_white_reference_id": final_white or None,
                "final_dark_reference_id": final_dark or None,
                "updated_at": now_iso(),
            },
        )
        if not updated:
            raise ValueError("Sesi M5 tidak ditemukan")
        return jsonify(status="sukses", state=updated[0]), 200
    except ValueError as exc:
        return jsonify(status="error", jenis="validasi", pesan=str(exc)), 400
    except Exception as exc:
        return jsonify(status="error", jenis="server", pesan=str(exc)), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)