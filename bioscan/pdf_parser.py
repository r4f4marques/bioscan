"""
BioScan Healthspan — Parser universal de PDFs de bioimpedância via LLM Vision

Suporta:
- Tanita (via Bioeasy Analysis)
- InBody (modelos 270, 370, 570, 770)

Fluxo: PDF → imagem PNG → Groq Llama 4 Scout (visão) → JSON estruturado
       A LLM detecta o fabricante pelo conteúdo visual antes de extrair.
"""

import base64
import io
import json
import os
from datetime import datetime, timezone

from pdf2image import convert_from_bytes


VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DPI = 150
MAX_IMAGE_SIDE = 2000        # max px no lado maior (limita tamanho)
JPEG_QUALITY = 85
MAX_IMAGE_BYTES = 4_000_000  # limite da Groq para imagens inline


# ── PROMPT UNIFICADO ──────────────────────────────────────────────────────

EXTRACTION_PROMPT = """Você é um extrator especializado em relatórios de bioimpedância.
Identifique primeiro o fabricante da balança (Tanita/Bioeasy ou InBody) pelo layout e logo do PDF.
Em seguida, extraia TODOS os valores numéricos do relatório em JSON válido.

Retorne APENAS um JSON neste formato, sem texto adicional:

{
  "manufacturer": "tanita" ou "inbody",
  "measured_at": "YYYY-MM-DD",
  "patient_name": "string ou null",
  "weight": number ou null,
  "bmi": number ou null,
  "fat_pct": number ou null,
  "visceral": number ou null,
  "muscle_kg": number ou null,
  "muscle_quality": number ou null,
  "bone_kg": number ou null,
  "bmr": number ou null,
  "meta_age": number ou null,
  "water_pct": number ou null,
  "water_kg": number ou null,
  "physique_rating": number ou null,
  "heart_rate": number ou null,
  "smi": number ou null,
  "protein_kg": number ou null,
  "mineral_kg": number ou null,
  "ffm_kg": number ou null,
  "waist_hip_ratio": number ou null,
  "obesity_degree": number ou null,
  "recommended_kcal": number ou null,
  "inbody_score": number ou null,
  "seg_musc": {
    "right_arm": number ou null,
    "left_arm": number ou null,
    "right_leg": number ou null,
    "left_leg": number ou null,
    "trunk": number ou null
  },
  "seg_fat": {
    "right_arm": number ou null,
    "left_arm": number ou null,
    "right_leg": number ou null,
    "left_leg": number ou null,
    "trunk": number ou null
  }
}

MAPEAMENTO TANITA (layout Bioeasy):
- "PESO" → weight (kg)
- "IMC" → bmi (kg/m²)
- "NÍVEL DE GORDURA" → fat_pct (%)
- "MASSA ADIPOSA" → se aparecer, pode informar mas fat_pct é prioridade
- "MASSA MUSCULAR" → muscle_kg (se em kg) OU muscle_quality (se em %, 0-100)
- "MASSA ÓSSEA" → bone_kg
- "TAXA METABÓLICA BASAL" → bmr (kcal)
- "GORDURA VISCERAL" → visceral
- "ÁGUA CORPORAL" → water_pct (%) E/OU water_kg (kg)
- "IDADE METABÓLICA" → meta_age
- "FFM" ou "MASSA NÃO ADIPOSA" → ffm_kg
- Para Tanita, manufacturer="tanita".

MAPEAMENTO INBODY (layout InBody 270/370/570/770):
- "Peso" → weight (kg)
- "IMC" → bmi (kg/m²)
- "PGC" (Percentual de Gordura Corporal) → fat_pct (%)
- "Massa Muscular Esquelética" → muscle_kg (kg, mesmo sendo esquelética converta para muscle_kg)
- "Massa de Gordura" → NÃO é fat_pct (é gordura em kg), ignore ou pule
- "Nível de Gordura Visceral" → visceral
- "Água Corporal Total" (L) → water_kg (e estime water_pct = water_kg/weight*100 se possível)
- "Proteína" (kg) → protein_kg
- "Minerais" (kg) → mineral_kg
- "Massa Livre de Gordura" → ffm_kg
- "SMI" (kg/m²) → smi
- "Taxa Metabólica Basal" → bmr (kcal)
- "Pontuação InBody" (X/100) → inbody_score (apenas o número X)
- "Relação Cintura-Quadril" → waist_hip_ratio
- "Grau de Obesidade" (%) → obesity_degree
- "Ingestão calórica recomendada" (kcal) → recommended_kcal
- Para InBody, manufacturer="inbody".

SEGMENTOS (ambos fabricantes):
- Tanita: "Braço Esquerdo/Direito" e "Perna Esquerda/Direita" em kg (músculo) e % (gordura)
- InBody: "Análise da Massa Magra Segmentar" mostra kg E % por segmento
  - USE SOMENTE OS VALORES EM KG (a primeira linha de cada segmento, ex: "3,94kg" para braço esquerdo)
  - IGNORE os percentuais (ex: "112,2%") — eles são proporção do ideal, não a medida absoluta
  - ATENÇÃO CRÍTICA: o valor do TRONCO aparece NO CENTRO da silhueta do InBody (entre os braços e as pernas). É OBRIGATÓRIO extrair esse valor. Exemplo: se vir "29,8kg" entre os braços e as pernas, esse é o seg_musc.trunk. NUNCA deixe trunk como null se a imagem mostrar um valor central.
  - São 5 valores distintos de massa em kg por gráfico segmental: 2 braços (topo), 1 tronco (centro), 2 pernas (base). Sempre extraia os 5.
- "Análise da Gordura Segmentar" → seg_fat (em kg para InBody, em % para Tanita)
  - No InBody, o tronco aparece também no centro (ex: "6,5kg" para gordura do tronco)
- Lateralidade: mantenha right_arm/left_arm conforme o lado ANATÔMICO do paciente
  (no InBody, o rótulo "Direito" está à direita da figura — que é o lado ANATÔMICO direito)

REGRAS CRÍTICAS:
- Use pontos como separador decimal (não vírgula).
- Se um valor não estiver presente no PDF, use null (NÃO invente).
- measured_at no formato YYYY-MM-DD a partir da data do exame.
- Para o nome do paciente, extraia o campo "Nome" ou "NOME" ou o texto entre parênteses após ID.
- weight NUNCA deve ser null se aparece no PDF."""


# ── FUNÇÕES AUXILIARES ────────────────────────────────────────────────────

def pdf_to_base64_image(pdf_bytes: bytes) -> str:
    """
    Converte a primeira página de um PDF em JPEG base64.

    Estratégia:
    1. Renderiza a 150 DPI (qualidade suficiente para OCR via LLM)
    2. Redimensiona para max 2000px no lado maior (mantém legibilidade)
    3. Comprime como JPEG qualidade 85 (balanço ótimo tamanho/clareza)

    Retorna base64 string para envio à API Groq.
    """
    from PIL import Image

    images = convert_from_bytes(pdf_bytes, dpi=DPI, first_page=1, last_page=1)
    if not images:
        raise ValueError("Não foi possível converter o PDF em imagem.")

    img = images[0]

    # Redimensiona se lado maior exceder MAX_IMAGE_SIDE
    if max(img.size) > MAX_IMAGE_SIDE:
        ratio = MAX_IMAGE_SIDE / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # Salva como JPEG (RGB, sem alpha)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    jpeg_bytes = buf.getvalue()

    # Fallback raríssimo: se mesmo assim passou do limite, reduz qualidade
    if len(jpeg_bytes) > MAX_IMAGE_BYTES:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=65, optimize=True)
        jpeg_bytes = buf.getvalue()

    return base64.b64encode(jpeg_bytes).decode("utf-8")


def parse_bioimpedance_pdf(pdf_bytes: bytes) -> dict:
    """
    Extrai medição de PDF Tanita ou InBody via LLM com visão.
    Detecta fabricante automaticamente e retorna dict normalizado.
    """
    from groq import Groq  # lazy import

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY não configurada.")

    b64_image = pdf_to_base64_image(pdf_bytes)

    client = Groq(api_key=api_key)
    completion = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                    },
                ],
            }
        ],
        temperature=0.1,
        max_completion_tokens=2048,
        response_format={"type": "json_object"},
    )

    raw = completion.choices[0].message.content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Resposta do LLM não é JSON válido: {e}\nConteúdo: {raw[:500]}")

    return _normalize_extraction(data)


def _normalize_extraction(data: dict) -> dict:
    """Converte o JSON da LLM para o formato plano do Measurement."""
    # Data de medição
    raw_date = data.get("measured_at")
    if raw_date:
        try:
            measured_at = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            measured_at = datetime.now(timezone.utc)
    else:
        measured_at = datetime.now(timezone.utc)

    # Campos principais
    result = {
        "measured_at":      measured_at,
        "weight":           _num(data.get("weight")),
        "bmi":              _num(data.get("bmi")),
        "fat_pct":          _num(data.get("fat_pct")),
        "visceral":         _num(data.get("visceral")),
        "muscle_kg":        _num(data.get("muscle_kg")),
        "muscle_quality":   _num(data.get("muscle_quality")),
        "bone_kg":          _num(data.get("bone_kg")),
        "bmr":              _num(data.get("bmr")),
        "meta_age":         _num(data.get("meta_age")),
        "water_pct":        _num(data.get("water_pct")),
        "water_kg":         _num(data.get("water_kg")),
        "physique_rating":  _int(data.get("physique_rating")),
        "heart_rate":       _int(data.get("heart_rate")),
        # InBody específicos
        "smi":              _num(data.get("smi")),
        "protein_kg":       _num(data.get("protein_kg")),
        "mineral_kg":       _num(data.get("mineral_kg")),
        "ffm_kg":           _num(data.get("ffm_kg")),
        "waist_hip_ratio":  _num(data.get("waist_hip_ratio")),
        "obesity_degree":   _num(data.get("obesity_degree")),
        "recommended_kcal": _num(data.get("recommended_kcal")),
        "inbody_score":     _num(data.get("inbody_score")),
    }

    # Se o water_pct não veio mas temos water_kg e weight, calcula
    if result["water_pct"] is None and result["water_kg"] and result["weight"]:
        result["water_pct"] = round(result["water_kg"] / result["weight"] * 100, 1)

    # Segmentos achatados — salvos no banco EXATAMENTE como vêm do PDF.
    # A conversão de unidades (kg → % para InBody) acontece no to_dict()
    # do Measurement, preservando os dados originais.
    seg_musc = data.get("seg_musc") or {}
    seg_fat = data.get("seg_fat") or {}
    manufacturer = (data.get("manufacturer") or "").lower()

    for side in ("right_arm", "left_arm", "right_leg", "left_leg", "trunk"):
        result[f"seg_musc_{side}"] = _num(seg_musc.get(side))
        result[f"seg_fat_{side}"]  = _num(seg_fat.get(side))

    # Metadata auxiliar
    result["_patient_name_detected"] = data.get("patient_name")
    result["_manufacturer"] = manufacturer or "unknown"

    return result


def _num(v):
    if v is None or v == "" or v == "null":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None
