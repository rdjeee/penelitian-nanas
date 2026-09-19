import os
import joblib
import numpy as np
from flask import Flask, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

# ==============================================================================
# KONFIGURASI SUPABASE
# ==============================================================================
SUPABASE_URL = "https://lecxngcahtadmgubcrhj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxlY3huZ2NhaHRhZG1ndWJjcmhqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk3NTI0NjUsImV4cCI6MjEwNTMyODQ2NX0.IqS7lHDshcodVescshb7sU2JRSnDxRyMIDuypewq6qA"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==============================================================================
# 18 KANAL SENSOR AS7265X & LOAD MODEL PLSR
# ==============================================================================
FEATURE_COLS = [
    'ch_a', 'ch_b', 'ch_c', 'ch_d', 'ch_e', 'ch_f', 
    'ch_g', 'ch_h', 'ch_i', 'ch_j', 'ch_k', 'ch_l', 
    'ch_r', 'ch_s', 'ch_t', 'ch_u', 'ch_v', 'ch_w'
]

MODEL_PATH = "model_plsr_nanas.pkl"
SCALER_PATH = "scaler_nanas.pkl"
BUNDLE_PATH = "calibration_bundle.pkl"

pls_model = None
scaler = None
cal_bundle = None

try:
    if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH) and os.path.exists(BUNDLE_PATH):
        pls_model = joblib.load(MODEL_PATH)
        scaler = joblib.load(SCALER_PATH)
        cal_bundle = joblib.load(BUNDLE_PATH)
        print("=> [ML SUCCESS] Model PLSR, Scaler, dan Bundel Kalibrasi berhasil dimuat!")
        print(f"   Sumber Referensi: {cal_bundle.get('reference_source', 'N/A')}")
    else:
        print("=> [ML WARNING] Salah satu file model (.pkl) belum ditemukan di folder backend-flask/")
except Exception as e:
    print(f"=> [ML ERROR] Gagal memuat file model: {e}")

def hitung_prediksi_brix(data_sensor):
    """
    Menghitung prediksi °Brix menggunakan protokol AS7265X:
    1. Absorbansi: A = -log10((S - D) / (W - D))
    2. Preprocessing: Standard Normal Variate (SNV)
    3. Scaling: StandardScaler
    4. Inferensi: PLSRegression
    """
    if pls_model is None or scaler is None or cal_bundle is None:
        return None

    try:
        raw_vals = np.array([float(data_sensor.get(col, 0.0)) for col in FEATURE_COLS], dtype=float)
        w_ref = np.array(cal_bundle.get('white_reference', np.ones(18)), dtype=float)
        d_ref = np.array(cal_bundle.get('dark_reference', np.zeros(18)), dtype=float)

        # 1. Reflektansi & Absorbansi
        denom = np.where(np.abs(w_ref - d_ref) < 1e-5, 1e-5, w_ref - d_ref)
        R = (raw_vals - d_ref) / denom
        R = np.clip(R, 1e-4, 2.0)
        A = -np.log10(R).reshape(1, -1)

        # 2. Standard Normal Variate (SNV)
        mean_A = np.mean(A)
        std_A = np.std(A)
        std_A = 1e-6 if std_A == 0 else std_A
        A_snv = (A - mean_A) / std_A

        # 3. Scaling & Prediksi PLSR
        X_scaled = scaler.transform(A_snv)
        pred_raw = float(pls_model.predict(X_scaled).flatten()[0])

        # Batasi ke rentang wajar (misal 0.0 s.d 35.0 Brix)
        prediksi_brix = round(max(0.0, min(35.0, pred_raw)), 1)
        return prediksi_brix
    except Exception as err:
        print(f"=> [ML INFERENCE ERROR]: {err}")
        return None

# ==============================================================================
# ENDPOINT API
# ==============================================================================
@app.route('/api/simpan_sensor', methods=['POST'])
def simpan_sensor():
    try:
        data_masuk = request.get_json() or {}
        id_nanas = str(data_masuk.get('id_nanas', ''))
        titik_pindai = str(data_masuk.get('titik_pindai', ''))

        # Hitung prediksi Brix jika ini adalah scan buah (bukan White/Dark reference)
        is_ref = titik_pindai.lower() in ['white', 'dark'] or 'ref_' in id_nanas.lower()
        prediksi_brix = None
        if not is_ref:
            prediksi_brix = hitung_prediksi_brix(data_masuk)
            print(f"=> [HASIL PREDIKSI] ID: {id_nanas} ({titik_pindai}) -> Brix: {prediksi_brix} °Bx")

        # 1. Simpan ke database Supabase (Non-blocking jika koneksi internet terganggu)
        db_response_data = None
        try:
            res = supabase.table('dataset_nanas').insert(data_masuk).execute()
            db_response_data = res.data
        except Exception as db_err:
            print(f"=> [SUPABASE WARNING] Gagal simpan ke DB: {db_err}")

        # 2. Kembalikan respons JSON lengkap dengan prediksi_brix untuk ESP32 & Web
        return jsonify({
            "status": "sukses",
            "pesan": "Data sensor berhasil diproses",
            "id_nanas": id_nanas,
            "titik_pindai": titik_pindai,
            "prediksi_brix": prediksi_brix,
            "data": db_response_data or data_masuk
        }), 200

    except Exception as e:
        print(f"=> [API ERROR]: {e}")
        return jsonify({
            "status": "error",
            "pesan": str(e)
        }), 500

@app.route('/api/prediksi_brix', methods=['POST'])
def endpoint_prediksi():
    """Endpoint mandiri untuk menguji prediksi langsung tanpa simpan ke Supabase"""
    try:
        data_masuk = request.get_json() or {}
        prediksi = hitung_prediksi_brix(data_masuk)
        return jsonify({
            "status": "sukses",
            "prediksi_brix": prediksi
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "pesan": str(e)
        }), 500

@app.route('/api/lihat_data', methods=['GET'])
def lihat_data():
    try:
        response = supabase.table('dataset_nanas').select('*').limit(100).execute()
        return jsonify({
            "status": "sukses",
            "total_data": len(response.data),
            "data": response.data
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "pesan": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)