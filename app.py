import pdfplumber
import pandas as pd
import streamlit as st

def get_color_label(color):
    """Menentukan warna Merah atau Biru dari atribut non_stroking_color."""
    if not color:
        return "Biru"
    
    if isinstance(color, (list, tuple)):
        # Format RGB
        if len(color) == 3:
            r, g, b = color[0], color[1], color[2]
            if max(r, g, b) <= 1.0:
                r, g, b = r * 255, g * 255, b * 255
            
            if r > b and r > (g + 15):
                return "Merah"
            elif b > r and b > (g + 15):
                return "Biru"
                
        # Format CMYK
        elif len(color) == 4:
            c, m, y, k = color
            if m > c and y > c:
                return "Merah"
            elif c > y:
                return "Biru"
                
    return "Biru"


def group_words_to_lines(words, line_tolerance=3):
    """Mengelompokkan kata-kata terpisah menjadi baris kalimat utuh."""
    lines = []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))

    for w in sorted_words:
        placed = False
        for line in lines:
            if abs(line["top_avg"] - w["top"]) < line_tolerance:
                line["words"].append(w)
                line["top_avg"] = sum(item["top"] for item in line["words"]) / len(line["words"])
                placed = True
                break
        if not placed:
            lines.append({"top_avg": w["top"], "words": [w]})

    for line in lines:
        line["words"].sort(key=lambda item: item["x0"])
        line["text"] = " ".join([item["text"] for item in line["words"]])
        line["x0"] = line["words"][0]["x0"]
        line["x1"] = line["words"][-1]["x1"]
        line["top"] = min(item["top"] for item in line["words"])
        line["bottom"] = max(item["bottom"] for item in line["words"])

    return lines


def process_pdf(pdf_file, target_club):
    pdf_file.seek(0)
    results = []

    with pdfplumber.open(pdf_file) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_width = page.width
            page_height = page.height

            words = page.extract_words(extra_attrs=["non_stroking_color"])
            if not words:
                continue

            lines = group_words_to_lines(words)

            # 1. Cari baris klub target
            club_lines = [
                line for line in lines 
                if target_club.lower() in line["text"].lower()
            ]
            if not club_lines:
                continue

            # 2. Filter angka partai:
            # - Berada di area bagan (30% s/d 82% lebar)
            # - Nilai angka HARUS antara 1 s/d 999 (Mengabaikan tahun lahir seperti 2009, 2010, 2024 dll)
            valid_partai_words = []
            for w in words:
                txt = w["text"].strip()
                if txt.isdigit():
                    val = int(txt)
                    if 1 <= val <= 999:  # Filter tahun & angka > 999
                        x_in_bracket = (0.30 * page_width) <= w["x0"] and w["x1"] <= (0.82 * page_width)
                        y_in_bracket = (0.10 * page_height) <= w["top"] and w["bottom"] <= (0.88 * page_height)
                        
                        if x_in_bracket and y_in_bracket:
                            valid_partai_words.append(w)

            # 3. Kelompokkan angka partai berdasarkan Kolom X (Babak / Round)
            x_columns = []
            if valid_partai_words:
                sorted_partai = sorted(valid_partai_words, key=lambda item: item["x0"])
                for item in sorted_partai:
                    x0 = item["x0"]
                    placed = False
                    for col in x_columns:
                        if abs(col["x_avg"] - x0) < 35:  # Toleransi 35px per kolom babak
                            col["items"].append(item)
                            col["x_avg"] = sum(i["x0"] for i in col["items"]) / len(col["items"])
                            placed = True
                            break
                    if not placed:
                        x_columns.append({"x_avg": x0, "items": [item]})

                # Urutkan kolom dari kiri ke kanan
                x_columns.sort(key=lambda c: c["x_avg"])

            # 4. Extrak data per atlet/klub
            for club_line in club_lines:
                club_x0 = club_line["x0"]
                club_y_center = (club_line["top"] + club_line["bottom"]) / 2

                # A. Cari Nama Atlet (Baris tepat di atas nama klub)
                atlet_nama = "TIDAK DITEMUKAN"
                atlet_color = club_line["words"][0].get("non_stroking_color")

                candidate_atlet_lines = []
                for line in lines:
                    if not line["text"].isdigit() and target_club.lower() not in line["text"].lower():
                        dy = club_line["top"] - line["bottom"]
                        dx = abs(line["x0"] - club_x0)
                        if -5 <= dy <= 30 and dx < 80:
                            candidate_atlet_lines.append((dy, line))

                if candidate_atlet_lines:
                    candidate_atlet_lines.sort(key=lambda c: c[0])
                    best_line = candidate_atlet_lines[0][1]
                    atlet_nama = best_line["text"]
                    atlet_color = best_line["words"][0].get("non_stroking_color")

                # B. Deteksi Warna (Merah / Biru)
                warna = get_color_label(atlet_color)

                # C. Cari Partai Awal & Partai Akhir
                candidate_cols = [col for col in x_columns if col["x_avg"] > (club_x0 + 10)]

                partai_awal = "?"
                partai_akhir = "?"

                if candidate_cols:
                    # Partai Awal: Kolom babak pertama
                    col_awal = candidate_cols[0]["items"]
                    best_awal = min(
                        col_awal,
                        key=lambda d: abs(((d["top"] + d["bottom"]) / 2) - club_y_center)
                    )
                    partai_awal = best_awal["text"]

                    # Partai Akhir: Kolom babak paling kanan (Final)
                    # Syarat:
                    # 1. Angka partai akhir >= partai awal
                    # 2. Selisih angka partai akhir dari partai awal <= 150 (Mencegah lonjakan aneh)
                    val_awal = int(partai_awal) if partai_awal.isdigit() else 0
                    
                    valid_final_cols = []
                    for col in reversed(candidate_cols):
                        valid_items = [
                            it for it in col["items"] 
                            if it["text"].isdigit() and val_awal <= int(it["text"]) <= (val_awal + 150)
                        ]
                        if valid_items:
                            valid_final_cols.append(valid_items)
                            break

                    if valid_final_cols:
                        col_akhir = valid_final_cols[0]
                        best_akhir = min(
                            col_akhir,
                            key=lambda d: abs(((d["top"] + d["bottom"]) / 2) - club_y_center)
                        )
                        partai_akhir = best_akhir["text"]
                    else:
                        partai_akhir = partai_awal

                results.append({
                    "nama": atlet_nama,
                    "partai_awal": partai_awal,
                    "warna": warna,
                    "partai_akhir": partai_akhir
                })

    return results


# --- TAMPILAN STREAMLIT ---
st.set_page_config(page_title="Sortir Bagan PDF", page_icon="🥋", layout="wide")

st.title("🥋 Sortir Atlet, Partai Awal & Partai Akhir")
st.caption("Aplikasi otomatisasi pencarian dan sortir urutan tanding atlet dari PDF bagan kejuaraan.")

col1, col2 = st.columns([2, 1])

with col1:
    uploaded_files = st.file_uploader("Upload File PDF Bracket", type=["pdf"], accept_multiple_files=True)

with col2:
    club_input = st.text_input("Nama Klub yang Dicari:", value="BULUNGAN PAMULANG")

if st.button("Cari & Sortir Data", type="primary") and uploaded_files:
    all_results = []
    for pdf_file in uploaded_files:
        res = process_pdf(pdf_file, club_input)
        all_results.extend(res)

    if all_results:
        st.success(f"Berhasil menemukan {len(all_results)} data untuk klub '{club_input}'.")

        # Format Teks Output Sesuai Request
        output_lines = []
        for i, item in enumerate(all_results, 1):
            line = f"{i}. {item['nama']}-{item['partai_awal']} {item['warna']}-{item['partai_akhir']}"
            output_lines.append(line)

        output_text = "\n".join(output_lines)

        # Tabel Preview
        st.subheader("📊 Ringkasan Tabel")
        df = pd.DataFrame(all_results)
        df.index = df.index + 1
        st.dataframe(df, use_container_width=True)

        # Output Teks Hasil Format Tanding
        st.subheader("📝 Format Output Tanding")
        st.code(output_text, language="text")

        # Tombol Download
        st.download_button(
            label="💾 Download Hasil (.txt)",
            data=output_text,
            file_name=f"hasil_sortir_{club_input.replace(' ', '_').lower()}.txt",
            mime="text/plain"
        )
    else:
        st.warning(f"Tidak ditemukan data atlet untuk klub '{club_input}'.")