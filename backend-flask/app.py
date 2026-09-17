from flask import Flask, request, jsonify
from supabase import create_client, Client

app = Flask(__name__)

# Ganti dengan URL Proyek Supabase Anda yang sesungguhnya
SUPABASE_URL = "https://wlkivyfyusohqdfjjstl.supabase.co"

# Gunakan Secret Key di sini (lingkungan backend yang aman)
# SUPABASE_KEY = "sb_secret_WuJs4TeTORKIABf2sjBnIA_o88Y4OL4"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indsa2l2eWZ5dXNvaHFkZmpqc3RsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzg0ODU0NzQsImV4cCI6MjA5NDA2MTQ3NH0.Xb667ERfbey28WfWOQLS5npQ77iV9axDFCAOeYa8Lno"

# Inisialisasi koneksi ke Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/api/simpan_sensor', methods=['POST'])
def simpan_sensor():
    try:
        # Menerima format JSON yang dikirimkan oleh ESP32
        data_masuk = request.get_json()

        # Menyisipkan data langsung ke tabel dataset_nanas
        # Pastikan key pada JSON (seperti 'id_nanas', 'ch_a', dll) 
        # persis sama dengan nama kolom di tabel Supabase
        response = supabase.table('dataset_nanas').insert(data_masuk).execute()
        
        return jsonify({
            "status": "sukses", 
            "pesan": "Data nanas berhasil disimpan ke database",
            "data": response.data
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error", 
            "pesan": str(e)
        }), 500

@app.route('/api/lihat_data', methods=['GET'])
def lihat_data():
    try:
        # Menarik seluruh data (*) dari tabel dataset_nanas di Supabase
        # Jika data sudah ribuan, Anda bisa membatasi dengan .limit(100) 
        response = supabase.table('dataset_nanas').select('*').execute()
        
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
    # host='0.0.0.0' mengizinkan Flask menerima koneksi dari IP lokal (ESP32)
    # port=5000 adalah port standar Flask
    app.run(host='0.0.0.0', port=5000, debug=True)