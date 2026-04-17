"""
BioScan Healthspan — Parser de PDF Tanita via LLM Vision (Groq)

Os PDFs da Bioeasy Analysis são imagens (sem camada de texto), portanto
precisamos usar OCR via modelo com visão. Usamos o Llama 4 Scout da Groq,
que é rápido, multimodal e suporta JSON mode nativo.
"""

import base64
import io
import json
import os
from datetime import datetime, timezone

from pdf2image import convert_from_bytes


# ── CONFIGURAÇÃO ──────────────────────────────────────────────────────────

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DPI = 200                 # resolução da conversão PDF → PNG
MAX_IMAGE_BYTES = 4_000_000  # limite da Groq para imagens inline


# ── PROMPT ESTRUTURADO ────────────────────────────────────────────────────

EXTRACTION_PROMPT = """Você é um extrator especializado em relatórios de bioimpedância Tanita/Bioeasy.
Analise a imagem do relatório e extraia TODOS os valores numéricos em JSON válido.

Retorne APENAS um JSON neste formato exato, sem texto adicional:

{
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
  "physique_rating": number ou null,
  "heart_rate": number ou null,
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

REGRAS CRÍTICAS:
- weight em kg (ex: 107.7)
- bmi em kg/m² (ex: 34.38)
- fat_pct em percentual (ex: 30.70)
- visceral é um número inteiro de avaliação (ex: 18 ou 18.5)
- muscle_kg é MASSA MUSCULAR TOTAL em kg (ex: 70.95 — NÃO confundir com muscle_quality)
- muscle_quality é um número entre 0-100 (ex: 65)
- bone_kg em kg (massa óssea, ex: 3.7)
- bmr (TAXA METABÓLICA BASAL) em kcal (ex: 2215)
- meta_age é IDADE METABÓLICA em anos (ex: 73)
- water_pct em percentual (ex: 58.70)
- heart_rate em bpm (pode ser 0 ou null se não medido)
- Nos segmentos: os valores aparecem em gráficos de barras. Extraia do rótulo numérico de cada barra.
- Use pontos como separador decimal (não vírgula).
- Se um valor não estiver visível ou claro, use null.
- NÃO invente valores. NÃO arredonde.
- Para measured_at: extraia a data principal do relatório no formato YYYY-MM-DD."""


# ── FUNÇÕES AUXILIARES ────────────────────────────────────────────────────

def pdf_to_base64_image(pdf_bytes: bytes, page: int = 0) -> str:
    """
    Converte um PDF em imagem PNG base64 (para envio à Groq Vision).
    Pega apenas a primeira página por padrão (relatórios Tanita têm 1 página).
    """
    images = convert_from_bytes(pdf_bytes, dpi=DPI, first_page=1, last_page=1)
    if not images:
        raise ValueError("Não foi possível converter o PDF em imagem.")

    img = images[page]

    # Converte para bytes e verifica tamanho
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()

    # Se muito grande, re-salva com DPI menor
    if len(png_bytes) > MAX_IMAGE_BYTES:
        images = convert_from_bytes(pdf_bytes, dpi=120, first_page=1, last_page=1)
        img = images[0]
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()

    return base64.b64encode(png_bytes).decode("utf-8")


def parse_tanita_pdf(pdf_bytes: bytes) -> dict:
    """
    Extrai os dados de um relatório Tanita PDF via LLM Vision.
    Retorna dict no formato dos campos do Measurement.
    """
    from groq import Groq  # lazy import

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY não configurada.")

    # Converte PDF em imagem base64
    b64_image = pdf_to_base64_image(pdf_bytes)

    # Monta requisição para Groq com visão
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
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_image}"
                        },
                    },
                ],
            }
        ],
        temperature=0.1,  # baixa para extração determinística
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
    """
    Converte o dict extraído para o formato de Measurement.
    Transforma 'seg_musc' e 'seg_fat' em campos planos (seg_musc_right_arm, etc.)
    e converte measured_at em datetime.
    """
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
        "measured_at": measured_at,
        "weight":          _num(data.get("weight")),
        "bmi":             _num(data.get("bmi")),
        "fat_pct":         _num(data.get("fat_pct")),
        "visceral":        _num(data.get("visceral")),
        "muscle_kg":       _num(data.get("muscle_kg")),
        "muscle_quality":  _num(data.get("muscle_quality")),
        "bone_kg":         _num(data.get("bone_kg")),
        "bmr":             _num(data.get("bmr")),
        "meta_age":        _num(data.get("meta_age")),
        "water_pct":       _num(data.get("water_pct")),
        "physique_rating": _int(data.get("physique_rating")),
        "heart_rate":      _int(data.get("heart_rate")),
    }

    # Segmentos (achatar estrutura aninhada)
    seg_musc = data.get("seg_musc") or {}
    seg_fat = data.get("seg_fat") or {}

    for side in ("right_arm", "left_arm", "right_leg", "left_leg", "trunk"):
        result[f"seg_musc_{side}"] = _num(seg_musc.get(side))
        result[f"seg_fat_{side}"]  = _num(seg_fat.get(side))

    # Metadata extra (não vai para Measurement mas pode ser útil)
    result["_patient_name_detected"] = data.get("patient_name")

    return result


def _num(v):
    """Converte valor para float, retorna None se inválido."""
    if v is None or v == "" or v == "null":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v):
    """Converte para int, retorna None se inválido."""
    n = _num(v)
    return int(n) if n is not None else None
