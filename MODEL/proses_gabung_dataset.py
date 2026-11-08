import os
import pandas as pd
import numpy as np

def main():
    source_excel = r'c:\laragon\www\penelitian-nanas\MODEL\data\DATASET_NANAS_OKTOBER_copy.xlsx'
    output_dir = r'c:\laragon\www\penelitian-nanas\MODEL\dataset_per_mode'
    os.makedirs(output_dir, exist_ok=True)

    print("1. Membaca data dari Excel sumber...")
    df_nanass = pd.read_excel(source_excel, sheet_name='data_set_nanass')
    df_brix = pd.read_excel(source_excel, sheet_name='data_set_brix')

    print(f"   - data_set_nanass: {df_nanass.shape[0]} baris, {df_nanass.shape[1]} kolom")
    print(f"   - data_set_brix: {df_brix.shape[0]} baris, {df_brix.shape[1]} kolom")

    # 2. Hitung Brix rata-rata dari 4 ulangan (1_simplo, 1_duplo, 2_simplo, 2_duplo)
    print("\n2. Menghitung rata-rata °Brix per titik buah (Atas, Tengah, Bawah)...")
    df_brix['fruit_id_str'] = df_brix['nanas_id'].astype(str) + 'A'

    df_brix['mean_brix_atas'] = df_brix[['1_simplo_a', '1_duplo_a', '2_simplo_a', '2_duplo_a']].mean(axis=1).round(3)
    df_brix['mean_brix_tengah'] = df_brix[['1_simplo_b', '1_duplo_b', '2_simplo_b', '2_duplo_b']].mean(axis=1).round(3)
    df_brix['mean_brix_bawah'] = df_brix[['1_simplo_c', '1_duplo_c', '2_simplo_c', '2_duplo_c']].mean(axis=1).round(3)

    # Buat lookup table untuk merge
    brix_lookup = {}
    lab_details_lookup = {}

    for _, row in df_brix.iterrows():
        fid = row['fruit_id_str']
        # Lookup Brix final
        brix_lookup[(fid, 'Atas')] = row['mean_brix_atas']
        brix_lookup[(fid, 'Tengah')] = row['mean_brix_tengah']
        brix_lookup[(fid, 'Bawah')] = row['mean_brix_bawah']

        # Lookup detail lab
        lab_details_lookup[(fid, 'Atas')] = {
            'alat1_simplo': row['1_simplo_a'],
            'alat1_duplo': row['1_duplo_a'],
            'alat2_simplo': row['2_simplo_a'],
            'alat2_duplo': row['2_duplo_a'],
            'Brix_Final': row['mean_brix_atas']
        }
        lab_details_lookup[(fid, 'Tengah')] = {
            'alat1_simplo': row['1_simplo_b'],
            'alat1_duplo': row['1_duplo_b'],
            'alat2_simplo': row['2_simplo_b'],
            'alat2_duplo': row['2_duplo_b'],
            'Brix_Final': row['mean_brix_tengah']
        }
        lab_details_lookup[(fid, 'Bawah')] = {
            'alat1_simplo': row['1_simplo_c'],
            'alat1_duplo': row['1_duplo_c'],
            'alat2_simplo': row['2_simplo_c'],
            'alat2_duplo': row['2_duplo_c'],
            'Brix_Final': row['mean_brix_bawah']
        }

    # 3. Pemetaan kanal AS7265X ke nama standar ch_a s.d ch_w
    WAVELENGTHS = [
        410, 435, 460, 485, 510, 535,
        560, 585, 610, 645, 680, 705,
        730, 760, 810, 860, 900, 940
    ]
    FEATURE_COLS = [
        'ch_a', 'ch_b', 'ch_c', 'ch_d', 'ch_e', 'ch_f',
        'ch_g', 'ch_h', 'ch_i', 'ch_j', 'ch_k', 'ch_l',
        'ch_r', 'ch_s', 'ch_t', 'ch_u', 'ch_v', 'ch_w'
    ]
    RAW_COLS = [f"raw_{wl}" for wl in WAVELENGTHS]
    CAL_COLS = [f"cal_{wl}" for wl in WAVELENGTHS]

    print("\n3. Menggabungkan data spektra dengan nilai °Brix...")
    # Tentukan titik_pindai yang sesuai standar
    def get_titik_pindai(row):
        mtype = str(row['measurement_type']).upper()
        if mtype == 'WHITE':
            return 'White'
        elif mtype == 'DARK':
            return 'Dark'
        else:
            return row['position']

    def get_id_nanas(row):
        mtype = str(row['measurement_type']).upper()
        if mtype == 'WHITE':
            return 'REF_WHITE'
        elif mtype == 'DARK':
            return 'REF_DARK'
        else:
            return str(row['fruit_id'])

    df_nanass['titik_pindai_std'] = df_nanass.apply(get_titik_pindai, axis=1)
    df_nanass['id_nanas_std'] = df_nanass.apply(get_id_nanas, axis=1)

    # Petakan nilai Brix
    def assign_brix(row):
        if str(row['measurement_type']).upper() in ('WHITE', 'DARK'):
            return np.nan
        fid = str(row['fruit_id'])
        pos = row['position']
        return brix_lookup.get((fid, pos), np.nan)

    df_nanass['Brix'] = df_nanass.apply(assign_brix, axis=1)

    # Tambahkan rincian pengukuran lab ke data lengkap
    for col in ['alat1_simplo', 'alat1_duplo', 'alat2_simplo', 'alat2_duplo']:
        def assign_lab(row, c=col):
            if str(row['measurement_type']).upper() in ('WHITE', 'DARK'):
                return np.nan
            fid = str(row['fruit_id'])
            pos = row['position']
            info = lab_details_lookup.get((fid, pos), {})
            return info.get(c, np.nan)
        df_nanass[col] = df_nanass.apply(assign_lab, axis=1)

    # 4. Buat dataset per mode
    models = ['M0', 'M1', 'M2', 'M3']
    print(f"\n4. Menyimpan dataset per mode ke folder '{output_dir}'...")

    for m in models:
        df_mode = df_nanass[df_nanass['model'] == m].copy()
        
        # Buat Sheet 1: Dataset Standar (23 Kolom, RAW, Kompatibel 100% dengan model.py)
        df_std_raw = pd.DataFrame()
        df_std_raw['id'] = df_mode['id'].values
        df_std_raw['waktu_pindai'] = df_mode['received_at'].values
        df_std_raw['id_nanas'] = df_mode['id_nanas_std'].values
        df_std_raw['titik_pindai'] = df_mode['titik_pindai_std'].values
        for feat, raw_col in zip(FEATURE_COLS, RAW_COLS):
            df_std_raw[feat] = df_mode[raw_col].values
        df_std_raw['Brix'] = df_mode['Brix'].values

        # Buat Sheet 2: Dataset Standar (23 Kolom, CAL)
        df_std_cal = pd.DataFrame()
        df_std_cal['id'] = df_mode['id'].values
        df_std_cal['waktu_pindai'] = df_mode['received_at'].values
        df_std_cal['id_nanas'] = df_mode['id_nanas_std'].values
        df_std_cal['titik_pindai'] = df_mode['titik_pindai_std'].values
        for feat, cal_col in zip(FEATURE_COLS, CAL_COLS):
            df_std_cal[feat] = df_mode[cal_col].values
        df_std_cal['Brix'] = df_mode['Brix'].values

        # Buat Sheet 3: Detail Lengkap (Semua metadata, kanal raw + cal, dan rincian lab)
        cols_priority = [
            'id', 'received_at', 'model', 'measurement_type', 'fruit_id', 'position', 'repeat_no',
            'alat1_simplo', 'alat1_duplo', 'alat2_simplo', 'alat2_duplo', 'Brix'
        ] + RAW_COLS + CAL_COLS
        remaining_cols = [c for c in df_mode.columns if c not in cols_priority and c not in ['titik_pindai_std', 'id_nanas_std']]
        df_detail = df_mode[cols_priority + remaining_cols].copy()

        # Simpan ke file Excel
        filename = f"dataset_{m}.xlsx"
        filepath = os.path.join(output_dir, filename)

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df_std_raw.to_excel(writer, sheet_name='Dataset_RAW_Standar', index=False)
            df_std_cal.to_excel(writer, sheet_name='Dataset_CAL_Standar', index=False)
            df_detail.to_excel(writer, sheet_name='Detail_Lengkap', index=False)

        print(f"   [OK] Tersimpan: {filename} ({len(df_mode)} baris)")

    # 5. Buat juga satu file master gabungan semua mode
    master_file = os.path.join(output_dir, "dataset_gabungan_semua_mode.xlsx")
    with pd.ExcelWriter(master_file, engine='openpyxl') as writer:
        for m in models:
            sub = df_nanass[df_nanass['model'] == m].copy()
            df_m_std = pd.DataFrame()
            df_m_std['id'] = sub['id'].values
            df_m_std['waktu_pindai'] = sub['received_at'].values
            df_m_std['id_nanas'] = sub['id_nanas_std'].values
            df_m_std['titik_pindai'] = sub['titik_pindai_std'].values
            for feat, raw_col in zip(FEATURE_COLS, RAW_COLS):
                df_m_std[feat] = sub[raw_col].values
            df_m_std['Brix'] = sub['Brix'].values
            df_m_std.to_excel(writer, sheet_name=f'Mode_{m}', index=False)
    print(f"   [OK] Tersimpan: dataset_gabungan_semua_mode.xlsx")

    # 6. Buat file ringkasan CSV untuk M3 (karena M3 adalah mode utama) agar bisa langsung dibaca juga jika perlu format CSV
    m3_csv_path = os.path.join(output_dir, "dataset_M3.csv")
    sub_m3 = df_nanass[df_nanass['model'] == 'M3'].copy()
    df_m3_std = pd.DataFrame()
    df_m3_std['id'] = sub_m3['id'].values
    df_m3_std['waktu_pindai'] = sub_m3['received_at'].values
    df_m3_std['id_nanas'] = sub_m3['id_nanas_std'].values
    df_m3_std['titik_pindai'] = sub_m3['titik_pindai_std'].values
    for feat, raw_col in zip(FEATURE_COLS, RAW_COLS):
        df_m3_std[feat] = sub_m3[raw_col].values
    df_m3_std['Brix'] = sub_m3['Brix'].values
    df_m3_std.to_csv(m3_csv_path, index=False)
    print(f"   [OK] Tersimpan: dataset_M3.csv")

    print("\n=== SEMUA DATA BERHASIL DIGABUNGKAN DAN DIPISAHKAN RAPI! ===")

if __name__ == '__main__':
    main()

