import os
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.cross_decomposition import PLSRegression
from sklearn.metrics import r2_score, mean_squared_error

# ==============================================================================
# PROTOKOL AS7265X - PEMODELAN PLSR PREDIKSI °BRIX NANAS (NON-DESTRUKTIF)
# Sesuai: "Protokol Akuisisi Multispektral AS7265X Tanpa LED Eksternal"
# ==============================================================================

# 18 Kanal AS7265X (410 nm s.d 940 nm)
FEATURE_COLS = [
    'ch_a', 'ch_b', 'ch_c', 'ch_d', 'ch_e', 'ch_f', 
    'ch_g', 'ch_h', 'ch_i', 'ch_j', 'ch_k', 'ch_l', 
    'ch_r', 'ch_s', 'ch_t', 'ch_u', 'ch_v', 'ch_w'
]

def standard_normal_variate(spectra):
    """
    Standard Normal Variate (SNV):
    Menghilangkan variasi hamburan cahaya (light scattering) akibat tekstur buah.
    Rumus: (x - mean(x)) / std(x) untuk setiap spektrum sampel.
    """
    mean = np.mean(spectra, axis=1, keepdims=True)
    std = np.std(spectra, axis=1, keepdims=True)
    std = np.where(std == 0, 1e-6, std)
    return (spectra - mean) / std

def hitung_absorbansi(S, W, D, eps=1e-5):
    """
    Perhitungan Reflektansi & Absorbansi Sesuai Protokol (Baris 77-79):
    R = (S - D) / (W - D)
    A = -log10(R)
    """
    numerator = S - D
    denominator = W - D
    
    # Pencegahan pembagian dengan nol
    denominator = np.where(np.abs(denominator) < eps, eps, denominator)
    R = numerator / denominator
    
    # Clip nilai R ke rentang positif untuk menghindari log negatif / tak terdefinisi
    R = np.clip(R, 1e-4, 2.0)
    A = -np.log10(R)
    return A

def main():
    candidate_files = [
        'dataset_lama.xlsx',
        'Data_Uji_TPT_Nanas_dan_Brix (4).xlsx',
        'dataset_baru.csv'
    ]
    file_path = None
    for cand in candidate_files:
        if os.path.exists(cand):
            file_path = cand
            break

    if not file_path:
        print("Tidak ada file dataset (.xlsx / .csv) yang ditemukan di folder MODEL/!")
        return

    print("==================================================================")
    print(f"1. MEMBACA & MEMPROSES DATASET: {file_path}")
    print("==================================================================")
    
    # Membaca file Excel atau CSV
    if file_path.endswith('.csv'):
        df_raw = pd.read_csv(file_path)
    else:
        xls = pd.ExcelFile(file_path)
        df_raw = pd.read_excel(file_path, sheet_name=xls.sheet_names[0])
    
    # Penyesuaian nama kolom
    if 'id' in df_raw.iloc[0].values:
        df_raw.columns = df_raw.iloc[0]
        df = df_raw[1:].copy()
        df.columns.name = None
    else:
        df = df_raw.copy()

    # Kolom standar
    standard_cols = ['id', 'waktu_pindai', 'id_nanas', 'titik_pindai'] + FEATURE_COLS + ['Brix']
    if len(df.columns) >= len(standard_cols):
        df.columns = standard_cols[:len(df.columns)]
    
    # Konversi numerik kanal
    for col in FEATURE_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    if 'Brix' in df.columns:
        df['Brix'] = pd.to_numeric(df['Brix'], errors='coerce')

    # --------------------------------------------------------------------------
    # 2. EKSTRAKSI DARK REFERENCE & WHITE REFERENCE
    # --------------------------------------------------------------------------
    # Sesuai protokol: 10 scan White Reference dan 10 scan Dark Reference
    mask_dark = (df['titik_pindai'].astype(str).str.upper() == 'DARK') | (df['id_nanas'].astype(str).str.upper().str.contains('DARK'))
    mask_white = (df['titik_pindai'].astype(str).str.upper() == 'WHITE') | (df['id_nanas'].astype(str).str.upper().str.contains('WHITE'))
    
    df_dark = df[mask_dark]
    df_white = df[mask_white]
    ref_source_desc = ""

    if len(df_dark) > 0 and len(df_white) > 0:
        dark_ref = df_dark[FEATURE_COLS].mean().values
        white_ref = df_white[FEATURE_COLS].mean().values
        ref_source_desc = f"Internal ({file_path})"
        print(f"=> Ditemukan {len(df_dark)} baris Dark Reference dan {len(df_white)} baris White Reference di '{file_path}'.")
        print("=> Nilai rata-rata Dark Reference dan White Reference berhasil dihitung.")
    elif os.path.exists('dataset_baru.csv'):
        print(f"=> [INFO]: File '{file_path}' tidak memiliki baris White/Dark Reference.")
        print("   Mengambil White & Dark Reference fisik dari 'dataset_baru.csv'...")
        try:
            df_ref_source = pd.read_csv('dataset_baru.csv')
            for col in FEATURE_COLS:
                df_ref_source[col] = pd.to_numeric(df_ref_source[col], errors='coerce')
            
            m_dark = (df_ref_source['titik_pindai'].astype(str).str.upper() == 'DARK') | (df_ref_source['id_nanas'].astype(str).str.upper().str.contains('DARK'))
            m_white = (df_ref_source['titik_pindai'].astype(str).str.upper() == 'WHITE') | (df_ref_source['id_nanas'].astype(str).str.upper().str.contains('WHITE'))
            
            if m_dark.sum() > 0 and m_white.sum() > 0:
                dark_ref = df_ref_source[m_dark][FEATURE_COLS].mean().values
                white_ref = df_ref_source[m_white][FEATURE_COLS].mean().values
                ref_source_desc = f"Eksternal ('dataset_baru.csv' - {m_white.sum()}x White, {m_dark.sum()}x Dark)"
                print(f"   [SUKSES] Berhasil memuat {m_dark.sum()} scan Dark dan {m_white.sum()} scan White dari 'dataset_baru.csv'!")
            else:
                raise ValueError("Baris Dark/White tidak lengkap di dataset_baru.csv")
        except Exception as e:
            print(f"   [GAGAL] Menggunakan fallback baseline: {e}")
            dark_ref = np.zeros(len(FEATURE_COLS))
            white_ref = df[FEATURE_COLS].quantile(0.99).values
            ref_source_desc = "Baseline Estimasi (Fallback)"
    else:
        print("=> [CATATAN]: Baris 'White' / 'Dark' reference tidak ditemukan pada file ini.")
        print("   (Menggunakan nilai baseline referensi sementara).")
        dark_ref = np.zeros(len(FEATURE_COLS))
        white_ref = df[FEATURE_COLS].quantile(0.99).values
        ref_source_desc = "Baseline Estimasi (Fallback)"

    # --------------------------------------------------------------------------
    # 3. FILTER DATA SAMPEL BUAH & RATA-RATA 5x REPETISI PER TITIK
    # --------------------------------------------------------------------------
    # Sesuai protokol baris 7: "Setiap posisi menghasilkan lima scan teknis berturut-turut
    # yang diperlakukan sebagai satu titik biologis, bukan lima sampel independen."
    df_samples = df[~mask_dark & ~mask_white].dropna(subset=FEATURE_COLS + ['Brix']).copy()
    
    print(f"\nTotal raw scan buah sebelum perataan: {len(df_samples)} baris.")
    
    # Agregasi (mean) per id_nanas dan titik_pindai
    df_grouped = df_samples.groupby(['id_nanas', 'titik_pindai'], as_index=False).agg(
        {**{col: 'mean' for col in FEATURE_COLS}, 'Brix': 'mean'}
    )
    print(f"Total titik biologis setelah perataan 5x scan: {len(df_grouped)} titik (Atas/Tengah/Bawah).")

    # --------------------------------------------------------------------------
    # 4. KALKULASI ABSORBANSI (R = (S - D)/(W - D), A = -log10(R)) & SNV
    # --------------------------------------------------------------------------
    S_raw = df_grouped[FEATURE_COLS].values
    y = df_grouped['Brix'].values

    # Hitung Absorbansi sesuai rumus protokol
    A_spectra = hitung_absorbansi(S_raw, white_ref, dark_ref)
    
    # Terapkan Standard Normal Variate (SNV)
    X_snv = standard_normal_variate(A_spectra)

    # --------------------------------------------------------------------------
    # 5. TRAIN-TEST SPLIT BERDASARKAN BUAH (GROUP SPLIT)
    # --------------------------------------------------------------------------
    # Memisahkan 80% Latih dan 20% Uji berdasarkan ID Nanas agar tidak data leakage
    unique_fruits = df_grouped['id_nanas'].unique()
    train_fruits, test_fruits = train_test_split(unique_fruits, test_size=0.2, random_state=42)

    mask_train = df_grouped['id_nanas'].isin(train_fruits)
    mask_test = df_grouped['id_nanas'].isin(test_fruits)

    X_train, y_train = X_snv[mask_train], y[mask_train]
    X_test, y_test = X_snv[mask_test], y[mask_test]

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # --------------------------------------------------------------------------
    # 6. OPTIMASI JUMLAH KOMPONEN LATEN PLSR DENGAN CROSS-VALIDATION
    # --------------------------------------------------------------------------
    print("\n==================================================================")
    print("2. MENCARI KOMPONEN LATEN PLSR TERBAIK (5-FOLD CROSS VALIDATION)")
    print("==================================================================")
    
    best_n_comp = 2
    best_rmse_cv = float('inf')
    cv = KFold(n_splits=5, shuffle=True, random_state=42)

    max_components = min(10, len(FEATURE_COLS), len(X_train) - 1)
    for n in range(1, max_components + 1):
        pls_cv = PLSRegression(n_components=n)
        y_cv_pred = cross_val_predict(pls_cv, X_train_scaled, y_train, cv=cv)
        rmse_cv = np.sqrt(mean_squared_error(y_train, y_cv_pred))
        r2_cv = r2_score(y_train, y_cv_pred)
        print(f"Komponen {n:2d} -> RMSE_CV: {rmse_cv:.4f} °Brix | R²_CV: {r2_cv:.4f}")
        
        if rmse_cv < best_rmse_cv:
            best_rmse_cv = rmse_cv
            best_n_comp = n

    print(f"\n=> Komponen Laten Optimal Terpilih: {best_n_comp} (RMSE_CV: {best_rmse_cv:.4f})")

    # --------------------------------------------------------------------------
    # 7. PELATIHAN MODEL FINAL & EVALUASI PADA TEST SET
    # --------------------------------------------------------------------------
    final_pls = PLSRegression(n_components=best_n_comp)
    final_pls.fit(X_train_scaled, y_train)

    y_train_pred = final_pls.predict(X_train_scaled).flatten()
    y_test_pred = final_pls.predict(X_test_scaled).flatten()

    r2_cal = r2_score(y_train, y_train_pred)
    rmsec = np.sqrt(mean_squared_error(y_train, y_train_pred))

    r2_val = r2_score(y_test, y_test_pred)
    rmsep = np.sqrt(mean_squared_error(y_test, y_test_pred))

    print("\n==================================================================")
    print("3. HASIL EVALUASI MODEL PLSR PROTOKOL AS7265X")
    print("==================================================================")
    print(f"Kalibrasi (Train)  -> R²: {r2_cal:.4f} | RMSEC: {rmsec:.4f} °Brix")
    print(f"Validasi  (Test)   -> R²: {r2_val:.4f} | RMSEP: {rmsep:.4f} °Brix")

    # --------------------------------------------------------------------------
    # 8. EXPORT MODEL, SCALER, PARAMETER KALIBRASI, & RINGKASAN
    # --------------------------------------------------------------------------
    output_dir = 'output_model'
    os.makedirs(output_dir, exist_ok=True)

    path_model = os.path.join(output_dir, 'model_plsr_nanas.pkl')
    path_scaler = os.path.join(output_dir, 'scaler_nanas.pkl')
    path_bundle = os.path.join(output_dir, 'calibration_bundle.pkl')
    path_plot = os.path.join(output_dir, 'hasil_evaluasi_plsr.png')
    path_summary = os.path.join(output_dir, 'ringkasan_evaluasi.txt')

    joblib.dump(final_pls, path_model)
    joblib.dump(scaler, path_scaler)
    
    # Simpan juga reference dan fungsi kalibrasi ke file parameter
    calibration_bundle = {
        'white_reference': white_ref,
        'dark_reference': dark_ref,
        'feature_cols': FEATURE_COLS,
        'best_n_components': best_n_comp,
        'reference_source': ref_source_desc,
        'protocol_version': '1.0'
    }
    joblib.dump(calibration_bundle, path_bundle)

    # Simpan ringkasan metrik ke file teks
    with open(path_summary, 'w', encoding='utf-8') as f:
        f.write("=== RINGKASAN HASIL EVALUASI MODEL PLSR NANAS ===\n")
        f.write(f"File Dataset          : {file_path}\n")
        f.write(f"Sumber Referensi      : {ref_source_desc}\n")
        f.write(f"Jumlah Titik Sampel   : {len(df_grouped)} titik biologis (Atas/Tengah/Bawah)\n")
        f.write(f"Komponen Laten Terbaik: {best_n_comp}\n")
        f.write(f"RMSE Cross-Validation : {best_rmse_cv:.4f} °Brix\n")
        f.write(f"R² Kalibrasi (Train)  : {r2_cal:.4f}\n")
        f.write(f"RMSEC (Train)         : {rmsec:.4f} °Brix\n")
        f.write(f"R² Validasi  (Test)   : {r2_val:.4f}\n")
        f.write(f"RMSEP (Test)          : {rmsep:.4f} °Brix\n")
    
    print("\n==================================================================")
    print("4. EXPORT MODEL SELESAI")
    print("==================================================================")
    print(f"Seluruh file hasil tersimpan rapi di folder: '{output_dir}/'")
    print(f"  1. {path_model}    (Model PLSR)")
    print(f"  2. {path_scaler}   (Standard Scaler)")
    print(f"  3. {path_bundle}   (White/Dark reference)")
    print(f"  4. {path_summary}  (Ringkasan evaluasi skor R² & RMSE)")
    print(f"  5. {path_plot}     (Grafik visualisasi scatter plot)")

    # --------------------------------------------------------------------------
    # 9. VISUALISASI HASIL PREDIKSI
    # --------------------------------------------------------------------------
    plt.figure(figsize=(7, 6))
    plt.scatter(y_test, y_test_pred, color='navy', alpha=0.6, edgecolors='k', label='Data Uji (Test)')
    
    # Garis ideal 1:1
    min_val = min(y_test.min(), y_test_pred.min())
    max_val = max(y_test.max(), y_test_pred.max())
    plt.plot([min_val, max_val], [min_val, max_val], 'r--', lw=2, label='Ideal 1:1')
    
    plt.title(f'Prediksi PLSR vs Nilai Asli °Brix\n(R²_val={r2_val:.4f}, RMSEP={rmsep:.4f})')
    plt.xlabel('Nilai Aktual Refraktometer (°Brix)')
    plt.ylabel('Nilai Prediksi Model PLSR (°Brix)')
    plt.legend()
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(path_plot, dpi=300)
    plt.close()

if __name__ == '__main__':
    main()