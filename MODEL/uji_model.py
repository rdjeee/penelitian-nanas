"""
Script Pengujian Model PLSR Nanas
=================================
Fungsi:
1. Membaca model yang sudah dilatih (model_plsr_nanas.pkl & calibration_bundle.pkl).
2. Membaca data uji (file Excel/CSV yang memiliki data spektra + nilai acuan Brix).
3. Melakukan prediksi Brix untuk setiap buah dan posisi (Atas, Tengah, Bawah).
4. Membandingkan Nilai Aktual (Lab) vs Nilai Prediksi Model.
5. Menghitung metrik akurasi: MAE, RMSE, R², dan rata-rata persentase error.
6. Menyimpan tabel perbandingan ke file Excel: 'MODEL/output_model/hasil_uji_prediksi_vs_aktual.xlsx'.

Cara Penggunaan:
  python uji_model.py                     (akan menguji data dataset_M3.xlsx)
  python uji_model.py dataset_M1.xlsx     (menguji file tertentu)
  python uji_model.py nama_file_baru.xlsx
"""

import os
import sys
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    model_dir = os.path.join(base_dir, 'output_model')

    path_model = os.path.join(model_dir, 'model_plsr_nanas.pkl')
    path_scaler = os.path.join(model_dir, 'scaler_nanas.pkl')
    path_bundle = os.path.join(model_dir, 'calibration_bundle.pkl')

    # 1. Cek kelengkapan model
    if not (os.path.exists(path_model) and os.path.exists(path_scaler) and os.path.exists(path_bundle)):
        print(f"[ERROR] File model tidak lengkap di folder: {model_dir}")
        print("Silakan jalankan 'python model.py' terlebih dahulu untuk melatih model.")
        return

    print("==================================================================")
    print("1. MEMUAT MODEL & PARAMETER KALIBRASI")
    print("==================================================================")
    model = joblib.load(path_model)
    scaler = joblib.load(path_scaler)
    bundle = joblib.load(path_bundle)

    FEATURE_COLS = bundle.get('feature_cols', [
        'ch_a', 'ch_b', 'ch_c', 'ch_d', 'ch_e', 'ch_f', 
        'ch_g', 'ch_h', 'ch_i', 'ch_j', 'ch_k', 'ch_l', 
        'ch_r', 'ch_s', 'ch_t', 'ch_u', 'ch_v', 'ch_w'
    ])
    white_ref = np.array(bundle['white_reference'], dtype=float)
    dark_ref = np.array(bundle['dark_reference'], dtype=float)

    print(f"-> Model: PLSR (Komponen Laten: {bundle.get('best_n_components', 1)})")
    print(f"-> Jumlah Kanal: {len(FEATURE_COLS)} kanal")

    # 2. Menentukan file data uji
    target_file = None
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        candidates = [
            arg,
            os.path.join(base_dir, arg),
            os.path.join(base_dir, 'dataset_per_mode', arg),
            os.path.join(base_dir, 'dataset_per_mode', f'dataset_{arg.upper()}.xlsx')
        ]
        for c in candidates:
            if os.path.exists(c):
                target_file = c
                break

    if not target_file:
        default_candidates = [
            os.path.join(base_dir, 'dataset_per_mode', 'dataset_M3.xlsx'),
            os.path.join(base_dir, 'dataset_per_mode', 'dataset_M1.xlsx'),
            os.path.join(base_dir, 'dataset_lama.xlsx')
        ]
        for c in default_candidates:
            if os.path.exists(c):
                target_file = c
                break

    if not target_file:
        print("[ERROR] Tidak ada file data uji yang ditemukan!")
        return

    print("\n==================================================================")
    print(f"2. MEMBACA DATA UJI: {os.path.basename(target_file)}")
    print("==================================================================")

    if target_file.endswith('.csv'):
        df = pd.read_csv(target_file)
    else:
        xls = pd.ExcelFile(target_file)
        df = pd.read_excel(target_file, sheet_name=xls.sheet_names[0])

    # Standardisasi kolom
    if 'id' in df.iloc[0].values:
        df.columns = df.iloc[0]
        df = df[1:].copy()
        df.columns.name = None

    for col in FEATURE_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['Brix'] = pd.to_numeric(df['Brix'], errors='coerce')

    # Filter baris sampel (bukan White / Dark) yang memiliki nilai Brix
    mask_dark = df['titik_pindai'].astype(str).str.upper() == 'DARK'
    mask_white = df['titik_pindai'].astype(str).str.upper() == 'WHITE'
    df_samples = df[~mask_dark & ~mask_white].dropna(subset=FEATURE_COLS + ['Brix']).copy()

    if len(df_samples) == 0:
        print("[ERROR] Tidak ada data sampel dengan nilai 'Brix' pada file ini!")
        return

    print(f"-> Total baris scan sampel: {len(df_samples)} baris.")

    # Agregasi repetisi per buah dan titik
    df_grouped = df_samples.groupby(['id_nanas', 'titik_pindai'], as_index=False).agg(
        {**{col: 'mean' for col in FEATURE_COLS}, 'Brix': 'mean'}
    )
    print(f"-> Total titik biologis setelah rata-rata repetisi: {len(df_grouped)} titik.")

    # 3. Preprocessing (Absorbansi + SNV + Scaling)
    S = df_grouped[FEATURE_COLS].values
    y_actual = df_grouped['Brix'].values

    # Absorbansi A = -log10((S - D)/(W - D))
    denom = np.where(np.abs(white_ref - dark_ref) < 1e-5, 1e-5, white_ref - dark_ref)
    R = np.clip((S - dark_ref) / denom, 1e-4, 2.0)
    A = -np.log10(R)

    # SNV (Standard Normal Variate)
    mean_a = np.mean(A, axis=1, keepdims=True)
    std_a = np.std(A, axis=1, keepdims=True)
    std_a = np.where(std_a == 0, 1e-6, std_a)
    X_snv = (A - mean_a) / std_a

    # Standard Scaler
    X_scaled = scaler.transform(X_snv)

    # 4. Prediksi dengan Model
    y_pred = model.predict(X_scaled).flatten()

    # 5. Hitung Hasil & Evaluasi
    df_grouped['Brix_Aktual_Lab'] = np.round(y_actual, 2)
    df_grouped['Brix_Prediksi_Model'] = np.round(y_pred, 2)
    df_grouped['Selisih_Error'] = np.round(df_grouped['Brix_Prediksi_Model'] - df_grouped['Brix_Aktual_Lab'], 2)
    df_grouped['Abs_Error'] = np.round(np.abs(df_grouped['Selisih_Error']), 2)
    df_grouped['Persen_Error_%'] = np.round((df_grouped['Abs_Error'] / df_grouped['Brix_Aktual_Lab']) * 100, 2)

    mae = mean_absolute_error(y_actual, y_pred)
    rmse = np.sqrt(mean_squared_error(y_actual, y_pred))
    r2 = r2_score(y_actual, y_pred)
    mape = np.mean(df_grouped['Persen_Error_%'])

    print("\n==================================================================")
    print("3. HASIL AKURASI EVALUASI PADA DATA UJI")
    print("==================================================================")
    print(f"R² (Koefisien Determinasi) : {r2:.4f}")
    print(f"RMSE (Root Mean Sq Error)  : {rmse:.4f} °Brix")
    print(f"MAE (Mean Absolute Error)  : {mae:.4f} °Brix")
    print(f"Rata-rata Error Persentase : {mape:.2f} %")

    print("\nContoh 15 Hasil Prediksi vs Aktual:")
    print("-" * 75)
    cols_show = ['id_nanas', 'titik_pindai', 'Brix_Aktual_Lab', 'Brix_Prediksi_Model', 'Selisih_Error', 'Persen_Error_%']
    print(df_grouped[cols_show].head(15).to_string(index=False))
    print("-" * 75)

    # 6. Simpan hasil ke Excel
    out_excel = os.path.join(model_dir, 'hasil_uji_prediksi_vs_aktual.xlsx')
    cols_export = ['id_nanas', 'titik_pindai', 'Brix_Aktual_Lab', 'Brix_Prediksi_Model', 'Selisih_Error', 'Abs_Error', 'Persen_Error_%']
    df_grouped[cols_export].to_excel(out_excel, index=False)
    print("\n[SUKSES] Tabel hasil pengujian lengkap disimpan ke:")
    print(f"File Excel: {out_excel}")

if __name__ == '__main__':
    main()
