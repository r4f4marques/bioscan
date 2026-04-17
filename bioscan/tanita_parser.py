"""
BioScan Healthspan — Tanita CSV Parser
Suporta o formato exportado pelas balanças Tanita (BC-780, BC-545N, MC-780MA e similares).
Retorna lista de dicts prontos para instanciar Measurement.
"""

from datetime import datetime
import io
import csv


# Mapeamento exato: coluna CSV → campo interno
# Baseado no arquivo real: csv_report_2026-04-14_14-20-18.csv
TANITA_MAP = {
    "Date":                       "date",
    "Weight (kg)":                "weight",
    "BMI":                        "bmi",
    "Body Fat (%)":               "fat_pct",
    "Visc Fat":                   "visceral",
    "Muscle Mass (kg)":           "ffm_kg",       # Tanita reporta massa magra (FFM), não músculo esquelético
    "Muscle Quality":             "muscle_quality",
    "Bone Mass (kg)":             "bone_kg",
    "BMR (kcal)":                 "bmr",
    "Metab Age":                  "meta_age",
    "Body Water (%)":             "water_pct",
    "Physique Rating":            "physique_rating",
    "Heart rate":                 "heart_rate",
    # Segmental — músculo
    "Muscle mass - right arm":    "seg_musc_right_arm",
    "Muscle mass - left arm":     "seg_musc_left_arm",
    "Muscle mass - right leg":    "seg_musc_right_leg",
    "Muscle mass - left leg":     "seg_musc_left_leg",
    "Muscle mass - trunk":        "seg_musc_trunk",
    # Segmental — qualidade muscular
    "Muscle quality - right arm": "seg_qual_right_arm",
    "Muscle quality - left arm":  "seg_qual_left_arm",
    "Muscle quality - right leg": "seg_qual_right_leg",
    "Muscle quality - left leg":  "seg_qual_left_leg",
    "Muscle quality - trunk":     "seg_qual_trunk",
    # Segmental — gordura
    "Body fat (%) - right arm":   "seg_fat_right_arm",
    "Body fat (%) - left arm":    "seg_fat_left_arm",
    "Body fat (%) - right leg":   "seg_fat_right_leg",
    "Body fat (%) - left leg":    "seg_fat_left_leg",
    "Body fat (%) - trunk":       "seg_fat_trunk",
}

# Campos que devem ser tratados como int após conversão
INT_FIELDS = {"physique_rating", "heart_rate"}

# Valores que a Tanita emite para "sem dado"
NULL_VALUES = {"-", "N/A", "", "null", "NULL", "None"}


def _cast(value: str, field: str):
    """Converte string CSV para Python nativo."""
    v = value.strip()
    if v in NULL_VALUES:
        return None
    try:
        return int(v) if field in INT_FIELDS else float(v)
    except ValueError:
        return None


def _parse_date(value: str) -> datetime:
    """Tenta os formatos de data que a Tanita pode emitir."""
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Formato de data não reconhecido: '{value}'")


def parse_tanita_csv(content: str | bytes) -> list[dict]:
    """
    Lê o conteúdo de um CSV Tanita e retorna lista de dicts.
    Cada dict mapeia diretamente para os campos de Measurement.

    Lança:
        ValueError — se o arquivo não tiver as colunas mínimas esperadas.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")  # remove BOM se presente

    reader = csv.DictReader(io.StringIO(content))

    # Valida colunas mínimas
    required = {"Date", "Weight (kg)", "Body Fat (%)", "Visc Fat"}
    headers = set(reader.fieldnames or [])
    missing = required - headers
    if missing:
        raise ValueError(
            f"CSV inválido — colunas ausentes: {', '.join(sorted(missing))}. "
            f"Verifique se o arquivo foi exportado corretamente pela balança."
        )

    rows = []
    for i, row in enumerate(reader, start=2):  # start=2 porque linha 1 é header
        if not any(v.strip() for v in row.values()):
            continue  # linha em branco

        m = {}
        for csv_col, field in TANITA_MAP.items():
            raw = row.get(csv_col, "")
            if field == "date":
                try:
                    m["measured_at"] = _parse_date(raw)
                except ValueError as e:
                    raise ValueError(f"Linha {i}: {e}") from e
            else:
                m[field] = _cast(raw, field)

        rows.append(m)

    if not rows:
        raise ValueError("Nenhuma medição encontrada no arquivo.")

    return rows


def parse_tanita_file(file_storage) -> list[dict]:
    """
    Wrapper para uso direto com Flask: recebe um FileStorage e retorna medições.
    Uso:
        from bioscan.tanita_parser import parse_tanita_file
        measurements = parse_tanita_file(request.files['csv'])
    """
    content = file_storage.read()
    return parse_tanita_csv(content)
