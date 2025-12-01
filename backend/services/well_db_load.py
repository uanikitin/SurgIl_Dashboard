from datetime import date
import re
import pandas as pd
from sqlalchemy import create_engine, text

# ---------------------------------------------------------
# 1. НАСТРОЙКИ
# ---------------------------------------------------------

excel_path = "/Users/volodymyrnikitin/Documents/Work/2023/Unitool/UzKorGaz/Заявка 1 UNITOOL.xlsx"

DB_URL = (
    "postgresql+psycopg://telegram_events_db_user:"
    "NBBhfT7BgYyvq3yRmgnJ1UnVXKMkYCX7@"
    "dpg-d43l9pre5dus73a4241g-a.frankfurt-postgres.render.com/"
    "telegram_events_db"
)

engine = create_engine(DB_URL)
DATA_AS_OF = date.today()  # дата актуальности конструкций


# ---------------------------------------------------------
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------

def to_num(x):
    """Конвертация '2451,98' → 2451.98."""
    if pd.isna(x):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return None


def parse_perf_intervals(text_value):
    """
    '2453-2456\n2458-2461' → [(2453,2456),(2458,2461)]
    """
    if pd.isna(text_value) or text_value is None:
        return []

    s = str(text_value).strip().replace("\r", " ")
    parts = re.split(r"[;\n]+", s)

    intervals = []
    for part in parts:
        if "-" not in part:
            continue
        left, right = part.split("-", 1)
        top = to_num(left)
        bottom = to_num(right)
        if top is not None and bottom is not None:
            intervals.append((top, bottom))

    return intervals


# ---------------------------------------------------------
# 3. ЧТЕНИЕ EXCEL
# ---------------------------------------------------------

df_raw = pd.read_excel(excel_path, header=None)

# Нормализуем первый столбец
first_col = (
    df_raw.iloc[:, 0]
    .astype(str)
    .str.replace("\n", " ")
    .str.strip()
)

# Ищем строку "№ скважины / Well No."
header_row_idx = first_col[first_col == "№ скважины / Well No."].index[0]

# Номера скважин (идут горизонтально)
well_numbers = df_raw.iloc[header_row_idx, 1:]

print("\n--- Строка с номерами скважин:", header_row_idx)
print("--- Номера скважин:", list(well_numbers))


# ---------------------------------------------------------
# 4. МАППИНГ ПАРАМЕТРОВ К ПОЛЯМ БАЗЫ (без интервалов)
# ---------------------------------------------------------

label_map = {
    "Диаметр эксплуатационной колонны, мм / Production casing diameter, mm":
        "prod_casing_diam_mm",

    "Глубина спуска эксплуатационной колонны, м / Depth of running production casing, m":
        "prod_casing_depth_m",

    "Текущий забой, м / Current bottomhole, m":
        "current_bottomhole_m",

    "Горизонт / Horizon":
        "horizon",

    "Диаметр НКТ, мм / Diameter of tubing, mm":
        "tubing_diam_mm",

    "Глубина башмака НКТ, м / Tubing shoe depth, m":
        "tubing_shoe_depth_m",

    "Глубина пакера, м / Packer depth, m":
        "packer_depth_m",

    "Глубина переводника, м / Adapter depth, m":
        "adapter_depth_m",

    "Глубина непрохода шаблона, м / Depth of pattern stuck, m":
        "pattern_stuck_depth_m",

    "Диаметр штуцера, мм / Choke diameter, mm":
        "choke_diam_mm",
}

# Находим индекс строки для каждого параметра
row_idx_by_label = {}

for label in label_map:
    matches = first_col[header_row_idx + 1 :]
    idxs = matches[matches == label].index
    if not idxs.empty:
        row_idx_by_label[label] = int(idxs[0])
    else:
        print(f"⚠ Не найдено в файле: {label}")

print("\n--- Строки параметров:", row_idx_by_label)


# ---------------------------------------------------------
# 5. ПАРСИНГ КОНСТРУКЦИЙ (ПЕРВЫЙ ПРОХОД)
# ---------------------------------------------------------

records = []

for col_idx, well_value in well_numbers.items():
    if pd.isna(well_value):
        continue

    well_no = str(well_value).strip()

    record = {
        "well_no": well_no,
        "horizon": None,
        "prod_casing_diam_mm": None,
        "prod_casing_depth_m": None,
        "current_bottomhole_m": None,
        "tubing_diam_mm": None,
        "tubing_shoe_depth_m": None,
        "packer_depth_m": None,
        "adapter_depth_m": None,
        "pattern_stuck_depth_m": None,
        "choke_diam_mm": None,
    }

    for label, field_name in label_map.items():
        row_idx = row_idx_by_label.get(label)
        if row_idx is None:
            continue
        record[field_name] = df_raw.iat[row_idx, col_idx]

    records.append(record)

df = pd.DataFrame(records)

# Числовые поля → float
numeric_cols = [
    "prod_casing_diam_mm",
    "prod_casing_depth_m",
    "current_bottomhole_m",
    "tubing_diam_mm",
    "tubing_shoe_depth_m",
    "packer_depth_m",
    "adapter_depth_m",
    "pattern_stuck_depth_m",
    "choke_diam_mm",
]

for col in numeric_cols:
    df[col] = df[col].apply(to_num)

print("\n--- DF конструкций:")
print(df.head())


# ---------------------------------------------------------
# 6. ВСТАВКА КОНСТРУКЦИЙ В БД (С АНТИ-ДУБЛИКАЦИЕЙ)
# ---------------------------------------------------------

insert_construction_sql = text("""
    INSERT INTO well_construction (
        well_no,
        horizon,
        prod_casing_diam_mm,
        prod_casing_depth_m,
        current_bottomhole_m,
        tubing_diam_mm,
        tubing_shoe_depth_m,
        packer_depth_m,
        adapter_depth_m,
        pattern_stuck_depth_m,
        choke_diam_mm,
        data_as_of
    ) VALUES (
        :well_no,
        :horizon,
        :prod_casing_diam_mm,
        :prod_casing_depth_m,
        :current_bottomhole_m,
        :tubing_diam_mm,
        :tubing_shoe_depth_m,
        :packer_depth_m,
        :adapter_depth_m,
        :pattern_stuck_depth_m,
        :choke_diam_mm,
        :data_as_of
    )
    RETURNING id
""")

well_id_by_no = {}

with engine.begin() as conn:
    for _, row in df.iterrows():
        well_no = row["well_no"]

        # анти-дубликат
        existing = conn.execute(
            text("""
                SELECT id FROM well_construction 
                WHERE well_no = :well_no AND data_as_of = :data_as_of
            """),
            {"well_no": well_no, "data_as_of": DATA_AS_OF},
        ).scalar()

        if existing:
            well_id_by_no[well_no] = existing
            continue

        # вставка
        res = conn.execute(
            insert_construction_sql,
            {
                "well_no": well_no,
                "horizon": row["horizon"],
                "prod_casing_diam_mm": row["prod_casing_diam_mm"],
                "prod_casing_depth_m": row["prod_casing_depth_m"],
                "current_bottomhole_m": row["current_bottomhole_m"],
                "tubing_diam_mm": row["tubing_diam_mm"],
                "tubing_shoe_depth_m": row["tubing_shoe_depth_m"],
                "packer_depth_m": row["packer_depth_m"],
                "adapter_depth_m": row["adapter_depth_m"],
                "pattern_stuck_depth_m": row["pattern_stuck_depth_m"],
                "choke_diam_mm": row["choke_diam_mm"],
                "data_as_of": DATA_AS_OF,
            }
        )

        well_id_by_no[well_no] = res.scalar()

print("\n--- Конструкции записаны.")


# ---------------------------------------------------------
# 7. ПАРСИНГ ИНТЕРВАЛОВ (ВТОРОЙ ПРОХОД)
# ---------------------------------------------------------

perf_label = "Интервалы перфорации, м / Perforation intervals, m"

matches = first_col[header_row_idx + 1 :]
perf_idxs = matches[matches == perf_label].index

if perf_idxs.empty:
    raise RuntimeError("❌ Не найдена строка с интервалами перфорации!")

perf_row_idx = int(perf_idxs[0])

insert_interval_sql = text("""
    INSERT INTO well_perforation_interval (
        well_construction_id,
        interval_index,
        top_depth_m,
        bottom_depth_m
    ) VALUES (
        :well_construction_id,
        :interval_index,
        :top_depth_m,
        :bottom_depth_m
    )
""")

with engine.begin() as conn:

    for col_idx, well_value in well_numbers.items():
        if pd.isna(well_value):
            continue

        well_no = str(well_value).strip()
        constr_id = well_id_by_no.get(well_no)

        if not constr_id:
            continue

        cell_raw = df_raw.iat[perf_row_idx, col_idx]
        intervals = parse_perf_intervals(cell_raw)

        # очищаем старые интервалы
        conn.execute(
            text("DELETE FROM well_perforation_interval WHERE well_construction_id = :cid"),
            {"cid": constr_id},
        )

        # записываем новые
        for i, (top, bottom) in enumerate(intervals, start=1):
            conn.execute(
                insert_interval_sql,
                {
                    "well_construction_id": constr_id,
                    "interval_index": i,
                    "top_depth_m": top,
                    "bottom_depth_m": bottom,
                }
            )

print("\n=== ГОТОВО ===")
print("Все конструкции и интервалы успешно загружены.")