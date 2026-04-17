"""
BioScan Healthspan — Gerador de Relatório PDF Médico
Saída: bytes do PDF pronto para download
"""

import io
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether
)


# ── CORES ─────────────────────────────────────────────────────────────────
COLOR_PRIMARY  = HexColor("#1a1a18")
COLOR_MUTED    = HexColor("#999999")
COLOR_LIGHT    = HexColor("#f5f4f0")
COLOR_BORDER   = HexColor("#e0e0dc")
COLOR_OK       = HexColor("#2d6a1f")
COLOR_WARN     = HexColor("#b07410")
COLOR_ALERT    = HexColor("#a82020")
COLOR_OK_BG    = HexColor("#e8f5e0")
COLOR_WARN_BG  = HexColor("#fef3e0")
COLOR_ALERT_BG = HexColor("#fde8e8")


# ── CLASSIFICADORES (espelha o dashboard) ────────────────────────────────

def fat_status(v, sex):
    if v is None:
        return None
    thr = (30, 35) if sex == "F" else (25, 30)
    if v < thr[0]:
        return "ok"
    if v < thr[1]:
        return "warn"
    return "alert"


def visc_status(v):
    if v is None:
        return None
    if v <= 9:
        return "ok"
    if v <= 14:
        return "warn"
    return "alert"


def bmi_status(v):
    if v is None:
        return None
    if v < 25:
        return "ok"
    if v < 30:
        return "warn"
    return "alert"


def meta_status(meta_age, real_age):
    if meta_age is None or real_age is None:
        return None
    diff = meta_age - real_age
    if diff <= 0:
        return "ok"
    if diff <= 5:
        return "warn"
    return "alert"


STATUS_LABELS = {"ok": "Normal", "warn": "Atenção", "alert": "Elevado"}


# ── ESTILOS ───────────────────────────────────────────────────────────────

def make_styles():
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold",
                                fontSize=16, textColor=COLOR_PRIMARY,
                                spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica",
                                   fontSize=9, textColor=COLOR_MUTED,
                                   spaceAfter=12),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold",
                             fontSize=11, textColor=COLOR_PRIMARY,
                             spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", fontName="Helvetica-Bold",
                             fontSize=9, textColor=COLOR_PRIMARY,
                             spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", fontName="Helvetica",
                               fontSize=9, textColor=COLOR_PRIMARY,
                               leading=12),
        "small": ParagraphStyle("small", fontName="Helvetica",
                                fontSize=8, textColor=COLOR_MUTED,
                                leading=10),
        "badge_ok": ParagraphStyle("badge_ok", fontName="Helvetica-Bold",
                                   fontSize=7, textColor=COLOR_OK,
                                   alignment=TA_CENTER),
        "badge_warn": ParagraphStyle("badge_warn", fontName="Helvetica-Bold",
                                     fontSize=7, textColor=COLOR_WARN,
                                     alignment=TA_CENTER),
        "badge_alert": ParagraphStyle("badge_alert", fontName="Helvetica-Bold",
                                      fontSize=7, textColor=COLOR_ALERT,
                                      alignment=TA_CENTER),
    }


# ── GRÁFICOS EVOLUTIVOS (matplotlib) ─────────────────────────────────────

def evolution_chart_png(measurements, attr, title, color, unit=""):
    """Retorna bytes PNG de um gráfico de evolução."""
    from matplotlib.dates import DateFormatter, AutoDateLocator

    dates = [m.measured_at for m in measurements]
    values = [getattr(m, attr) for m in measurements]

    # Filtra pontos None
    pairs = [(d, v) for d, v in zip(dates, values) if v is not None]
    if len(pairs) < 2:
        return None

    fig = Figure(figsize=(4.8, 2.4), dpi=120)
    ax = fig.add_subplot(111)

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    ax.plot(xs, ys, marker="o", linestyle="-", linewidth=1.5,
            markersize=4, color=color)
    ax.fill_between(xs, ys, min(ys) - abs(min(ys)) * 0.05 if min(ys) > 0 else min(ys) - 1,
                    alpha=0.12, color=color)

    ax.set_title(title, fontsize=10, loc="left", pad=8,
                 fontweight="bold", color="#1a1a18")
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cccccc")
    ax.spines["bottom"].set_color("#cccccc")
    ax.tick_params(axis="both", labelsize=7, colors="#666666")

    if unit:
        ax.set_ylabel(unit, fontsize=7, color="#666666")

    # Sempre formato dd/mm no eixo X, independente do intervalo
    ax.xaxis.set_major_formatter(DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(AutoDateLocator(minticks=2, maxticks=6))

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white",
                pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ── GRÁFICO SEGMENTAL (massa muscular + gordura lado a lado) ─────────────

def segmental_chart_png(m):
    """Gráfico de barras com valores segmentais."""
    fig = Figure(figsize=(7.2, 2.5), dpi=120)

    segs = ["Braço D", "Braço E", "Tronco", "Perna D", "Perna E"]
    musc = [m.seg_musc_right_arm, m.seg_musc_left_arm, m.seg_musc_trunk,
            m.seg_musc_right_leg, m.seg_musc_left_leg]
    fat = [m.seg_fat_right_arm, m.seg_fat_left_arm, m.seg_fat_trunk,
           m.seg_fat_right_leg, m.seg_fat_left_leg]

    # Placeholder para valores None
    musc = [v if v is not None else 0 for v in musc]
    fat = [v if v is not None else 0 for v in fat]

    ax1 = fig.add_subplot(121)
    ax1.bar(segs, musc, color="#639922", alpha=0.85)
    ax1.set_title("Massa muscular (kg)", fontsize=10, loc="left",
                  fontweight="bold", color="#1a1a18")
    ax1.grid(True, axis="y", linestyle=":", linewidth=0.5, alpha=0.5)
    for sp in ["top", "right"]:
        ax1.spines[sp].set_visible(False)
    ax1.tick_params(axis="both", labelsize=7, colors="#666666")
    for i, v in enumerate(musc):
        if v > 0:
            ax1.text(i, v + max(musc) * 0.02, f"{v:.1f}", ha="center",
                     fontsize=7, color="#333333")

    ax2 = fig.add_subplot(122)
    fat_colors = ["#97c459" if v < 25 else "#ef9f27" if v < 32 else "#e24b4a"
                  for v in fat]
    ax2.bar(segs, fat, color=fat_colors, alpha=0.85)
    ax2.set_title("Gordura corporal (%)", fontsize=10, loc="left",
                  fontweight="bold", color="#1a1a18")
    ax2.grid(True, axis="y", linestyle=":", linewidth=0.5, alpha=0.5)
    for sp in ["top", "right"]:
        ax2.spines[sp].set_visible(False)
    ax2.tick_params(axis="both", labelsize=7, colors="#666666")
    for i, v in enumerate(fat):
        if v > 0:
            ax2.text(i, v + max(fat) * 0.02, f"{v:.1f}", ha="center",
                     fontsize=7, color="#333333")

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ── BARRAS DE REFERÊNCIA (estilo InBody) ─────────────────────────────────

def reference_bands(patient):
    """Retorna faixas de referência baseadas em idade e sexo."""
    sex = getattr(patient, "sex", None)
    age = getattr(patient, "age", None) or 40

    # % Gordura (ACSM)
    if sex == "F":
        if age < 40:   fat_min, fat_max = 21, 33
        elif age < 60: fat_min, fat_max = 23, 34
        else:          fat_min, fat_max = 24, 36
    else:
        if age < 40:   fat_min, fat_max = 8, 19
        elif age < 60: fat_min, fat_max = 11, 22
        else:          fat_min, fat_max = 13, 25

    return {
        "fat_min": fat_min, "fat_max": fat_max,
        "bmi_min": 18.5,    "bmi_max": 24.9,
    }


def reference_bars_png(m, patient):
    """
    Gera gráfico 'InBody-style' com barras horizontais das métricas principais.
    Zonas fixas: abaixo (0-30%), normal (30-60%), acima (60-100%).
    """
    ref = reference_bands(patient)
    h_m = getattr(patient, "height_cm", None)
    h_m = h_m / 100 if h_m else None

    # Define as barras: (label, valor, faixa_min, faixa_max, unidade)
    bars = []

    # Peso via IMC × altura²
    if h_m and m.weight:
        w_min = ref["bmi_min"] * h_m * h_m
        w_max = ref["bmi_max"] * h_m * h_m
        bars.append(("Peso", m.weight, w_min, w_max, "kg"))

    # Massa muscular: 32-42% (F), 38-48% (M)
    sex = getattr(patient, "sex", None)
    if m.weight and m.muscle_kg:
        if sex == "F":
            mm_min, mm_max = m.weight * 0.32, m.weight * 0.42
        else:
            mm_min, mm_max = m.weight * 0.38, m.weight * 0.48
        bars.append(("Massa muscular", m.muscle_kg, mm_min, mm_max, "kg"))

    # IMC
    if m.bmi:
        bars.append(("IMC", m.bmi, ref["bmi_min"], ref["bmi_max"], "kg/m²"))

    # % Gordura corporal
    if m.fat_pct is not None:
        bars.append(("% Gordura corporal", m.fat_pct,
                     ref["fat_min"], ref["fat_max"], "%"))

    if not bars:
        return None

    # Zonas fixas
    Z_LOW = 30   # abaixo: 0-30%
    Z_HIGH = 60  # normal: 30-60%, acima: 60-100%

    def marker_pos(value, range_min, range_max):
        """Posição do marcador (0-100) dentro das zonas alinhadas."""
        if value < range_min:
            t = max(0, value) / range_min if range_min > 0 else 0
            return t * Z_LOW
        elif value <= range_max:
            t = (value - range_min) / (range_max - range_min)
            return Z_LOW + t * (Z_HIGH - Z_LOW)
        else:
            t = min(1, (value - range_max) / range_max)
            return Z_HIGH + t * (100 - Z_HIGH)

    # Cria figura
    n = len(bars)
    fig = Figure(figsize=(7.5, 0.7 + n * 0.55), dpi=120)
    ax = fig.add_subplot(111)

    bar_h = 0.45
    y_positions = list(range(n, 0, -1))

    # Cores das zonas (mesmas do frontend)
    c_below  = "#a8c9e8"
    c_normal = "#a8d89b"
    c_above  = "#e8a89b"

    for (label, value, rmin, rmax, unit), y in zip(bars, y_positions):
        # Desenha as 3 zonas
        ax.barh(y, Z_LOW, left=0, height=bar_h, color=c_below, edgecolor="none")
        ax.barh(y, Z_HIGH - Z_LOW, left=Z_LOW, height=bar_h, color=c_normal, edgecolor="none")
        ax.barh(y, 100 - Z_HIGH, left=Z_HIGH, height=bar_h, color=c_above, edgecolor="none")

        # Marcador
        mpos = marker_pos(value, rmin, rmax)
        ax.plot([mpos, mpos], [y - bar_h/2 - 0.08, y + bar_h/2 + 0.08],
                color="#1a1a18", linewidth=2.5, solid_capstyle="butt", zorder=5)
        ax.plot([mpos], [y], marker="D", markersize=7,
                color="#1a1a18", markeredgecolor="white", markeredgewidth=1.2, zorder=6)

        # Classificação (texto colorido à direita)
        if value < rmin:
            status, scolor = "Abaixo", "#1a6fa8"
        elif value <= rmax:
            status, scolor = "Normal", "#2d6a1f"
        else:
            status, scolor = "Acima", "#a82020"

        value_str = f"{value:.1f} {unit}"
        ax.text(107, y, value_str, ha="left", va="center",
                fontsize=9, fontweight="bold", color="#1a1a18")
        ax.text(137, y, f"[{status}]", ha="left", va="center",
                fontsize=8, color=scolor, fontweight="bold")

    # Labels das barras (à esquerda)
    ax.set_yticks(y_positions)
    ax.set_yticklabels([b[0] for b in bars], fontsize=9)

    # Labels das zonas (embaixo)
    ax.text(Z_LOW / 2, 0.3, "Abaixo", ha="center", fontsize=7, color="#666")
    ax.text((Z_LOW + Z_HIGH) / 2, 0.3, "Normal", ha="center", fontsize=7, color="#666")
    ax.text((Z_HIGH + 100) / 2, 0.3, "Acima", ha="center", fontsize=7, color="#666")

    ax.set_xlim(-2, 170)
    ax.set_ylim(0, n + 0.8)
    ax.set_xticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(left=False)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ── TABELA DE MÉTRICAS COM VALORES DE REFERÊNCIA ─────────────────────────

def metrics_table(m, p, styles):
    """Tabela principal com métricas, valor, referência e classificação."""

    def badge(status):
        if status is None:
            return Paragraph("—", styles["small"])
        label = STATUS_LABELS[status]
        return Paragraph(f"<b>{label}</b>", styles[f"badge_{status}"])

    sex = p.sex or "M"

    # Cria o texto de IMC formatado com superscript via Paragraph
    def bmi_val(v):
        if v is None:
            return "—"
        return Paragraph(f"{v:.2f} kg/m<super>2</super>", styles["body"])

    def bmi_ref_p():
        return Paragraph("18,5 – 24,9 kg/m<super>2</super>", styles["body"])

    # Valores de referência baseados em diretrizes clínicas
    fat_ref = "10-22% (H) / 20-32% (M)" if sex == "M" else "10-22% (H) / 20-32% (M)"
    visc_ref = "≤ 9 (saudável)"
    musc_ref = "dependente de altura/sexo"
    water_ref = "50-65%"
    meta_ref = f"≤ {p.age or '—'} anos (idade real)"

    rows = [
        ["Parâmetro", "Valor", "Referência", "Classificação"],
        ["Peso",            f"{m.weight:.1f} kg" if m.weight else "—",
         "—",               badge(None)],
        ["IMC",             bmi_val(m.bmi),
         bmi_ref_p(),       badge(bmi_status(m.bmi))],
        ["Gordura corporal", f"{m.fat_pct:.1f}%" if m.fat_pct else "—",
         fat_ref,           badge(fat_status(m.fat_pct, sex))],
        ["Gordura visceral", f"{m.visceral}" if m.visceral else "—",
         visc_ref,          badge(visc_status(m.visceral))],
        ["Massa muscular",  f"{m.muscle_kg:.2f} kg" if m.muscle_kg else "—",
         musc_ref,          badge(None)],
        ["Qualidade muscular", f"{m.muscle_quality}" if m.muscle_quality else "—",
         "≥ 60",            badge(None)],
        ["Massa óssea",     f"{m.bone_kg:.2f} kg" if m.bone_kg else "—",
         "—",               badge(None)],
        ["Água corporal",   f"{m.water_pct:.1f}%" if m.water_pct else "—",
         water_ref,         badge(None)],
        ["Metabolismo basal", f"{m.bmr:.0f} kcal" if m.bmr else "—",
         "—",               badge(None)],
        ["Idade metabólica", f"{m.meta_age:.0f} anos" if m.meta_age else "—",
         meta_ref,          badge(meta_status(m.meta_age, p.age))],
    ]

    t = Table(rows, colWidths=[4.5 * cm, 3 * cm, 5.5 * cm, 3.2 * cm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_PRIMARY),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("FONT", (0, 1), (0, -1), "Helvetica", 8),
        ("FONT", (1, 1), (-1, -1), "Helvetica", 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, COLOR_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


# ── TABELA DE HISTÓRICO ──────────────────────────────────────────────────

def history_table(measurements, styles):
    rows = [["Data", "Peso (kg)", "Gordura (%)", "Músculo (kg)",
             "Visceral", "Id. met.", "BMR"]]

    # Se há mais de uma medição no mesmo dia COM horário real, inclui hora para distinguir
    dates = [m.measured_at.date() for m in measurements]
    has_same_day = len(dates) != len(set(dates))
    has_real_time = any(m.measured_at.time() != datetime.min.time() for m in measurements)
    date_fmt = "%d/%m/%Y %H:%M" if (has_same_day and has_real_time) else "%d/%m/%Y"

    for m in reversed(measurements):
        rows.append([
            m.measured_at.strftime(date_fmt),
            f"{m.weight:.1f}" if m.weight else "—",
            f"{m.fat_pct:.1f}" if m.fat_pct else "—",
            f"{m.muscle_kg:.2f}" if m.muscle_kg else "—",
            f"{m.visceral}" if m.visceral else "—",
            f"{m.meta_age:.0f}" if m.meta_age else "—",
            f"{m.bmr:.0f}" if m.bmr else "—",
        ])

    data_col_w = 3.4 * cm if (has_same_day and has_real_time) else 2.6 * cm
    t = Table(rows, colWidths=[data_col_w, 2.1 * cm, 2.3 * cm, 2.4 * cm,
                               2 * cm, 2 * cm, 2 * cm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_LIGHT),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.25, COLOR_BORDER),
    ]))
    return t


# ── TABELA DE ALERTAS CLÍNICOS ───────────────────────────────────────────

FIELD_LABELS = {
    "fat_pct":   "Gordura corporal",
    "visceral":  "Gordura visceral",
    "meta_age":  "Idade metabólica",
    "bmi":       "IMC",
    "weight":    "Peso",
    "muscle_kg": "Massa muscular",
    "water_pct": "Água corporal",
    "bone_kg":   "Massa óssea",
    "bmr":       "Metabolismo basal",
}


def flags_table(flags, styles):
    if not flags:
        return Paragraph("Nenhum alerta clínico identificado.", styles["body"])

    rows = [["Parâmetro", "Observação"]]
    bg_colors = []

    for f in flags:
        label = FIELD_LABELS.get(f["field"], f["field"].replace("_", " ").capitalize())
        rows.append([label, f["message"]])
        bg_colors.append(
            COLOR_WARN_BG if f["level"] == "warn" else COLOR_ALERT_BG
        )

    t = Table(rows, colWidths=[4.5 * cm, 11.7 * cm])
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_LIGHT),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, COLOR_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    for i, bg in enumerate(bg_colors, start=1):
        style.append(("BACKGROUND", (0, i), (-1, i), bg))

    t.setStyle(TableStyle(style))
    return t


# ── HEADER E FOOTER ──────────────────────────────────────────────────────

def header_footer(canvas, doc):
    canvas.saveState()

    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(COLOR_PRIMARY)
    canvas.drawString(2 * cm, A4[1] - 1.3 * cm, "BioScan Healthspan")
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(COLOR_MUTED)
    canvas.drawString(2 * cm, A4[1] - 1.6 * cm, "Relatório de Composição Corporal")

    # Linha separadora topo
    canvas.setStrokeColor(COLOR_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, A4[1] - 1.9 * cm, A4[0] - 2 * cm, A4[1] - 1.9 * cm)

    # Rodapé
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(COLOR_MUTED)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)
    canvas.drawString(2 * cm, 1 * cm,
                      f"Gerado em {datetime.now(timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')}")
    canvas.drawRightString(A4[0] - 2 * cm, 1 * cm,
                           f"Página {doc.page}")
    canvas.drawCentredString(A4[0] / 2, 1 * cm, "bioscan.tridash.fit")

    canvas.restoreState()


# ── FUNÇÃO PRINCIPAL ─────────────────────────────────────────────────────

def generate_pdf(patient, measurements, risk_flags) -> bytes:
    """
    Gera o PDF completo e retorna os bytes.
    - patient: instância Patient (com .name, .age, .sex, .height_cm, etc.)
    - measurements: lista de Measurement ordenada cronologicamente
    - risk_flags: lista de dicts {field, level, message}
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2.3 * cm, bottomMargin=2 * cm,
        title=f"BioScan - {patient.name}",
    )

    styles = make_styles()
    story = []

    # ── CABEÇALHO ──
    story.append(Paragraph(patient.name, styles["title"]))
    sex_label = "Masculino" if patient.sex == "M" else "Feminino" if patient.sex == "F" else "—"
    subtitle_parts = [
        f"{patient.age} anos" if patient.age else "Idade não informada",
        sex_label,
        f"{patient.height_cm:.0f} cm" if patient.height_cm else "Altura não informada",
    ]
    if getattr(patient, "cpf", None):
        subtitle_parts.append(f"CPF {patient.cpf}")
    story.append(Paragraph(" · ".join(subtitle_parts), styles["subtitle"]))

    if not measurements:
        story.append(Paragraph("Nenhuma medição disponível.", styles["body"]))
        doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
        return buf.getvalue()

    last = measurements[-1]
    first = measurements[0]

    # Formato do período — inclui hora quando medições do mesmo dia têm horários reais (não 00:00)
    same_day = first.measured_at.date() == last.measured_at.date()
    has_real_time = (
        first.measured_at.time() != datetime.min.time() or
        last.measured_at.time() != datetime.min.time()
    )
    date_fmt = "%d/%m/%Y %H:%M" if (same_day and has_real_time) else "%d/%m/%Y"

    story.append(Paragraph("Resumo da última avaliação", styles["h2"]))

    # Se só há uma medição, subtítulo mais simples
    if len(measurements) == 1:
        subtitle = f"Medição de {last.measured_at.strftime(date_fmt)} · medição única no histórico"
    else:
        subtitle = (
            f"Medição de {last.measured_at.strftime(date_fmt)} · "
            f"{len(measurements)} medições no histórico · "
            f"período acompanhado: {first.measured_at.strftime(date_fmt)} → "
            f"{last.measured_at.strftime(date_fmt)}"
        )
    story.append(Paragraph(subtitle, styles["small"]))
    story.append(Spacer(1, 6))

    # ── TABELA DE MÉTRICAS ──
    story.append(metrics_table(last, patient, styles))

    # ── ALERTAS ──
    story.append(Paragraph("Alertas clínicos", styles["h2"]))
    story.append(flags_table(risk_flags, styles))

    # ── BARRAS DE REFERÊNCIA (estilo InBody) ──
    ref_png = reference_bars_png(last, patient)
    if ref_png:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Análise de composição corporal", styles["h2"]))
        story.append(Image(io.BytesIO(ref_png), width=17 * cm, height=1 * cm + len([
            x for x in [
                getattr(last, 'weight', None), getattr(last, 'muscle_kg', None),
                getattr(last, 'bmi', None), getattr(last, 'fat_pct', None)
            ] if x is not None
        ]) * 1.35 * cm))

    # ── ANÁLISE SEGMENTAL ──
    seg_png = segmental_chart_png(last)
    if seg_png:
        story.append(KeepTogether([
            Paragraph("Análise segmental", styles["h2"]),
            Spacer(1, 6),
            Image(io.BytesIO(seg_png), width=17 * cm, height=6 * cm),
        ]))
    else:
        story.append(Paragraph("Análise segmental", styles["h2"]))
        story.append(Paragraph("Dados segmentais não disponíveis.", styles["small"]))

    # ── EVOLUÇÃO TEMPORAL (só aparece com 2+ medições) ──
    if len(measurements) >= 2:
        story.append(PageBreak())
        story.append(Paragraph("Evolução temporal", styles["h2"]))

        # Deltas resumidos
        def delta(attr):
            a, b = getattr(first, attr), getattr(last, attr)
            if a is None or b is None:
                return None
            return b - a

        delta_rows = [["Parâmetro", "Inicial", "Atual", "Variação"]]
        deltas_def = [
            ("Peso (kg)", "weight", 1),
            ("Gordura (%)", "fat_pct", 1),
            ("Músculo (kg)", "muscle_kg", 2),
            ("Visceral", "visceral", 1),
            ("Idade metabólica", "meta_age", 0),
            ("BMR (kcal)", "bmr", 0),
        ]

        for label, attr, dec in deltas_def:
            a, b = getattr(first, attr), getattr(last, attr)
            d = delta(attr)
            if d is None:
                delta_rows.append([label, "—", "—", "—"])
            else:
                sign = "+" if d > 0 else ""
                delta_rows.append([
                    label,
                    f"{a:.{dec}f}",
                    f"{b:.{dec}f}",
                    f"{sign}{d:.{dec}f}",
                ])

        dt = Table(delta_rows, colWidths=[5 * cm, 3 * cm, 3 * cm, 3 * cm])
        dt.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_LIGHT),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.25, COLOR_BORDER),
        ]))
        story.append(dt)

        story.append(Spacer(1, 10))

        # Gráficos 2x3
        charts = [
            ("weight", "Peso (kg)", "#1a6fa8", "kg"),
            ("fat_pct", "Gordura corporal (%)", "#e67e22", "%"),
            ("muscle_kg", "Massa muscular (kg)", "#2d6a1f", "kg"),
            ("visceral", "Gordura visceral", "#c0392b", ""),
            ("meta_age", "Idade metabólica", "#8e44ad", "anos"),
            ("bmr", "Metabolismo basal (kcal)", "#16a085", "kcal"),
        ]

        chart_imgs = []
        for attr, title, color, unit in charts:
            png = evolution_chart_png(measurements, attr, title, color, unit)
            if png:
                chart_imgs.append(
                    Image(io.BytesIO(png), width=8.2 * cm, height=4.3 * cm)
                )

        # Organiza em tabela 2 colunas
        pairs = []
        for i in range(0, len(chart_imgs), 2):
            row = chart_imgs[i:i + 2]
            if len(row) == 1:
                row.append(Paragraph("", styles["small"]))
            pairs.append(row)

        if pairs:
            chart_table = Table(pairs, colWidths=[8.5 * cm, 8.5 * cm])
            chart_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(chart_table)

    # ── HISTÓRICO COMPLETO ──
    story.append(Spacer(1, 18))
    story.append(Paragraph("Histórico completo de medições", styles["h2"]))
    story.append(history_table(measurements, styles))

    # ── OBSERVAÇÕES ──
    if getattr(patient, "notes", None):
        story.append(Spacer(1, 14))
        story.append(Paragraph("Observações clínicas", styles["h2"]))
        story.append(Paragraph(patient.notes, styles["body"]))

    # Build
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    return buf.getvalue()
