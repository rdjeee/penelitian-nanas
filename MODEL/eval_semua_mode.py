import os
os.environ.setdefault('MPLCONFIGDIR', os.path.join(os.environ.get('TEMP', os.getcwd()), 'mpl'))

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.cross_decomposition import PLSRegression
from sklearn.metrics import r2_score, mean_squared_error

base_dir = os.path.dirname(os.path.abspath(__file__))
FEATURE_COLS = [
    'ch_a', 'ch_b', 'ch_c', 'ch_d', 'ch_e', 'ch_f', 
    'ch_g', 'ch_h', 'ch_i', 'ch_j', 'ch_k', 'ch_l', 
    'ch_r', 'ch_s', 'ch_t', 'ch_u', 'ch_v', 'ch_w'
]

def standard_normal_variate(spectra):
    mean = np.mean(spectra, axis=1, keepdims=True)
    std = np.std(spectra, axis=1, keepdims=True)
    std = np.where(std == 0, 1e-6, std)
    return (spectra - mean) / std

def hitung_absorbansi(S, W, D, eps=1e-5):
    denom = np.where(np.abs(W - D) < eps, eps, W - D)
    R = np.clip((S - D) / denom, 1e-4, 2.0)
    return -np.log10(R)

results = []

for m in ['M0', 'M1', 'M2', 'M3']:
    excel_path = os.path.join(base_dir, 'dataset_per_mode', f'dataset_{m}.xlsx')
    df = pd.read_excel(excel_path, sheet_name=0)
    
    mask_dark = df['titik_pindai'].astype(str).str.upper() == 'DARK'
    mask_white = df['titik_pindai'].astype(str).str.upper() == 'WHITE'
    
    df_samples = df[~mask_dark & ~mask_white].dropna(subset=FEATURE_COLS + ['Brix']).copy()
    df_grouped = df_samples.groupby(['id_nanas', 'titik_pindai'], as_index=False).agg(
        {**{col: 'mean' for col in FEATURE_COLS}, 'Brix': 'mean'}
    )
    
    S_raw = df_grouped[FEATURE_COLS].values
    y = df_grouped['Brix'].values
    
    if mask_dark.sum() > 0 and mask_white.sum() > 0:
        dark_ref = df[mask_dark][FEATURE_COLS].mean().values
        white_ref = df[mask_white][FEATURE_COLS].mean().values
        A_spectra = hitung_absorbansi(S_raw, white_ref, dark_ref)
        prep_desc = "Absorbansi [ -log10(R) ] + SNV"
    else:
        A_spectra = S_raw
        prep_desc = "Raw Spectra + SNV"
        
    X_snv = standard_normal_variate(A_spectra)
    
    unique_fruits = df_grouped['id_nanas'].unique()
    train_fruits, test_fruits = train_test_split(unique_fruits, test_size=0.2, random_state=42)
    
    mask_train = df_grouped['id_nanas'].isin(train_fruits)
    mask_test = df_grouped['id_nanas'].isin(test_fruits)
    
    X_train, y_train = X_snv[mask_train], y[mask_train]
    X_test, y_test = X_snv[mask_test], y[mask_test]
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    cv = KFold(n_splits=5, shuffle=True, random_state=42)
    best_n = 1
    best_rmse_cv = float('inf')
    
    for n in range(1, 8):
        pls = PLSRegression(n_components=n)
        y_cv_pred = cross_val_predict(pls, X_train_scaled, y_train, cv=cv)
        rmse_cv = np.sqrt(mean_squared_error(y_train, y_cv_pred))
        if rmse_cv < best_rmse_cv:
            best_rmse_cv = rmse_cv
            best_n = n
            
    final_pls = PLSRegression(n_components=best_n)
    final_pls.fit(X_train_scaled, y_train)
    
    y_train_pred = final_pls.predict(X_train_scaled).flatten()
    y_test_pred = final_pls.predict(X_test_scaled).flatten()
    
    r2_train = r2_score(y_train, y_train_pred)
    rmsec = np.sqrt(mean_squared_error(y_train, y_train_pred))
    r2_test = r2_score(y_test, y_test_pred)
    rmsep = np.sqrt(mean_squared_error(y_test, y_test_pred))
    
    results.append({
        'Mode': m,
        'Titik Sampel': len(df_grouped),
        'Preprocessing': prep_desc,
        'Komponen Optimal': best_n,
        'RMSE_CV': round(best_rmse_cv, 4),
        'R2_Train (RMSEC)': f"{r2_train:.4f} ({rmsec:.4f})",
        'R2_Test (RMSEP)': f"{r2_test:.4f} ({rmsep:.4f})"
    })

df_res = pd.DataFrame(results)
print("\n" + "="*80)
print("HASIL PERBANDINGAN EVALUASI MODEL PLSR ANTAR MODE (M0 - M3)")
print("="*80)
print(df_res.to_string(index=False))

res_txt_path = os.path.join(base_dir, 'output_model', 'perbandingan_semua_mode.txt')
with open(res_txt_path, 'w', encoding='utf-8') as f:
    f.write("=== HASIL PERBANDINGAN EVALUASI MODEL PLSR ANTAR MODE (M0 - M3) ===\n\n")
    f.write(df_res.to_string(index=False) + "\n")
print(f"\nTersimpan di: {res_txt_path}")

