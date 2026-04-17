"""
BioScan Healthspan — API Routes (Flask Blueprint)
Plugável no TriDash: registre com app.register_blueprint(bioscan_bp, url_prefix="/bioscan")
"""

import os
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, g
import jwt

from .models import db, User, Patient, Measurement
from .tanita_parser import parse_tanita_file, parse_tanita_csv

bioscan_bp = Blueprint("bioscan", __name__)

JWT_EXPIRES = timedelta(hours=12)


def _jwt_secret():
    """Lê o JWT_SECRET em tempo de execução — garante que a variável de ambiente está disponível."""
    return os.environ.get("JWT_SECRET", "dev-only-change-in-prod")


@bioscan_bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "bioscan-healthspan"})


# ── JWT AUTH ──────────────────────────────────────────────────────────────

def create_token(user: User) -> str:
    payload = {
        "sub":  user.id,
        "role": user.role,
        "exp":  datetime.now(timezone.utc) + JWT_EXPIRES,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def require_auth(fn):
    """Decorator: valida Bearer token e injeta g.user."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Token ausente"}), 401
        try:
            payload = jwt.decode(auth[7:], _jwt_secret(), algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expirado"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token inválido"}), 401

        user = db.session.get(User, payload["sub"])
        if not user or not user.is_active:
            return jsonify({"error": "Usuário não encontrado"}), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def require_role(*roles):
    """Decorator: exige que g.user tenha um dos roles listados."""
    def decorator(fn):
        @wraps(fn)
        @require_auth
        def wrapper(*args, **kwargs):
            if g.user.role not in roles:
                return jsonify({"error": "Permissão negada"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ── AUTH ENDPOINTS ────────────────────────────────────────────────────────

@bioscan_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    user = User.query.filter_by(email=data.get("email", "").lower()).first()

    if not user or not user.check_password(data.get("password", "")):
        return jsonify({"error": "Credenciais inválidas"}), 401

    return jsonify({
        "token": create_token(user),
        "user":  user.to_dict(),
    })


@bioscan_bp.post("/auth/register")
@require_role("doctor")
def register():
    data = request.get_json(silent=True) or {}
    if User.query.filter_by(email=data.get("email", "").lower()).first():
        return jsonify({"error": "E-mail já cadastrado"}), 409

    user = User(
        email=data["email"].lower(),
        role=data.get("role", "patient"),
        name=data.get("name"),
    )
    user.set_password(data["password"])
    db.session.add(user)
    db.session.commit()
    return jsonify(user.to_dict()), 201


# ── PATIENTS ──────────────────────────────────────────────────────────────

@bioscan_bp.get("/patients")
@require_role("doctor")
def list_patients():
    patients = Patient.query.order_by(Patient.name).all()
    return jsonify([p.to_dict() for p in patients])


@bioscan_bp.post("/patients")
@require_role("doctor")
def create_patient():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "Nome obrigatório"}), 400

    p = Patient(
        name       = data["name"],
        sex        = data.get("sex"),
        height_cm  = data.get("height_cm"),
        notes      = data.get("notes"),
        tags       = ",".join(data.get("tags", [])),
        created_by = g.user.id,
    )
    if data.get("birth_date"):
        p.birth_date = datetime.strptime(data["birth_date"], "%Y-%m-%d").date()

    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201


@bioscan_bp.get("/patients/<int:pid>")
@require_auth
def get_patient(pid):
    p = db.get_or_404(Patient, pid)
    if g.user.role == "patient" and g.user.patient_id != pid:
        return jsonify({"error": "Acesso negado"}), 403
    return jsonify(p.to_dict(include_measurements=True))


@bioscan_bp.patch("/patients/<int:pid>")
@require_role("doctor")
def update_patient(pid):
    p = db.get_or_404(Patient, pid)
    data = request.get_json(silent=True) or {}

    for field in ("name", "sex", "notes"):
        if field in data:
            setattr(p, field, data[field])
    if "height_cm" in data:
        p.height_cm = data["height_cm"]
    if "tags" in data:
        p.tags = ",".join(data["tags"])
    if "birth_date" in data:
        p.birth_date = datetime.strptime(data["birth_date"], "%Y-%m-%d").date()

    db.session.commit()
    return jsonify(p.to_dict())


@bioscan_bp.delete("/patients/<int:pid>")
@require_role("doctor")
def delete_patient(pid):
    p = db.get_or_404(Patient, pid)
    db.session.delete(p)
    db.session.commit()
    return jsonify({"deleted": pid})


# ── MEASUREMENTS ──────────────────────────────────────────────────────────

@bioscan_bp.get("/patients/<int:pid>/measurements")
@require_auth
def list_measurements(pid):
    if g.user.role == "patient" and g.user.patient_id != pid:
        return jsonify({"error": "Acesso negado"}), 403

    q = Measurement.query.filter_by(patient_id=pid)

    from_date = request.args.get("from")
    to_date   = request.args.get("to")
    if from_date:
        q = q.filter(Measurement.measured_at >= from_date)
    if to_date:
        q = q.filter(Measurement.measured_at <= to_date)

    measurements = q.order_by(Measurement.measured_at).all()
    return jsonify([m.to_dict() for m in measurements])


@bioscan_bp.post("/patients/<int:pid>/measurements")
@require_role("doctor")
def add_measurement(pid):
    db.get_or_404(Patient, pid)
    data = request.get_json(silent=True) or {}

    m = Measurement(patient_id=pid, source="manual",
                    measured_at=datetime.now(timezone.utc))
    _fill_measurement(m, data)

    db.session.add(m)
    db.session.commit()
    return jsonify(m.to_dict()), 201


@bioscan_bp.delete("/patients/<int:pid>/measurements/<int:mid>")
@require_role("doctor")
def delete_measurement(pid, mid):
    m = Measurement.query.filter_by(id=mid, patient_id=pid).first_or_404()
    db.session.delete(m)
    db.session.commit()
    return jsonify({"deleted": mid})


# ── CSV IMPORT ────────────────────────────────────────────────────────────

@bioscan_bp.post("/patients/<int:pid>/import-csv")
@require_role("doctor")
def import_csv(pid):
    db.get_or_404(Patient, pid)

    if "csv" not in request.files:
        return jsonify({"error": "Campo 'csv' não encontrado no form"}), 400

    try:
        rows = parse_tanita_file(request.files["csv"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    inserted = []
    skipped  = 0

    for row in rows:
        measured_at = row.pop("measured_at")
        exists = Measurement.query.filter_by(
            patient_id=pid, measured_at=measured_at
        ).first()
        if exists:
            skipped += 1
            continue

        m = Measurement(patient_id=pid, source="tanita_csv",
                        measured_at=measured_at)
        _fill_measurement(m, row)
        db.session.add(m)
        inserted.append(m)

    db.session.commit()

    return jsonify({
        "inserted": len(inserted),
        "skipped":  skipped,
        "measurements": [m.to_dict() for m in inserted],
    }), 201


@bioscan_bp.post("/import-csv-raw")
@require_role("doctor")
def import_csv_raw():
    pid = request.form.get("patient_id")

    if "csv" not in request.files:
        return jsonify({"error": "Campo 'csv' não encontrado"}), 400

    try:
        content = request.files["csv"].read()
        rows = parse_tanita_csv(content)
    except ValueError as e:
        return jsonify({"error": str(e)}), 422

    if not pid:
        p = Patient(name="Paciente importado", created_by=g.user.id)
        db.session.add(p)
        db.session.flush()
        pid = p.id
    else:
        db.get_or_404(Patient, int(pid))

    inserted = _bulk_insert(int(pid), rows)
    db.session.commit()

    return jsonify({
        "patient_id": int(pid),
        "inserted":   len(inserted),
        "measurements": [m.to_dict() for m in inserted],
    }), 201


# ── HEALTHSPAN SUMMARY ────────────────────────────────────────────────────

@bioscan_bp.get("/patients/<int:pid>/summary")
@require_auth
def healthspan_summary(pid):
    if g.user.role == "patient" and g.user.patient_id != pid:
        return jsonify({"error": "Acesso negado"}), 403

    p = db.get_or_404(Patient, pid)
    if not p.measurements:
        return jsonify({"error": "Sem medições"}), 404

    first = p.measurements[0]
    last  = p.measurements[-1]

    def delta(attr):
        a, b = getattr(first, attr), getattr(last, attr)
        if a is None or b is None:
            return None
        return round(b - a, 2)

    return jsonify({
        "patient":        p.to_dict(),
        "n_measurements": len(p.measurements),
        "period": {
            "from": first.measured_at.isoformat(),
            "to":   last.measured_at.isoformat(),
        },
        "latest":  last.to_dict(),
        "deltas": {
            "weight":    delta("weight"),
            "fat_pct":   delta("fat_pct"),
            "muscle_kg": delta("muscle_kg"),
            "visceral":  delta("visceral"),
            "meta_age":  delta("meta_age"),
            "bmr":       delta("bmr"),
            "water_pct": delta("water_pct"),
        },
        "risk_flags": _risk_flags(last, p),
    })


# ── AI INTERPRETATION ────────────────────────────────────────────────────

@bioscan_bp.post("/patients/<int:pid>/interpret")
@require_role("doctor")
def interpret(pid):
    from groq import Groq

    p = db.get_or_404(Patient, pid)
    if not p.measurements:
        return jsonify({"error": "Sem medições"}), 404

    last  = p.measurements[-1]
    first = p.measurements[0]

    system_prompt = """Você é um assistente médico especializado em composição corporal e healthspan.
Analise os dados de bioimpedância e forneça uma interpretação clínica clara, objetiva e educativa.
Use linguagem acessível para o médico compartilhar com o paciente.
Não faça diagnósticos definitivos. Foque em tendências e recomendações de estilo de vida.
Responda sempre em português brasileiro."""

    user_prompt = f"""Paciente: {p.name}, {p.age} anos, sexo {p.sex or 'não informado'}, altura {p.height_cm or 'não informada'} cm.

MEDIÇÃO MAIS RECENTE ({last.measured_at.strftime('%d/%m/%Y')}):
- Peso: {last.weight} kg | IMC: {last.bmi}
- Gordura corporal: {last.fat_pct}% | Gordura visceral: {last.visceral}
- Massa muscular: {last.muscle_kg} kg | Qualidade muscular: {last.muscle_quality}
- Massa óssea: {last.bone_kg} kg
- Água corporal: {last.water_pct}%
- Metabolismo basal: {last.bmr} kcal
- Idade metabólica: {last.meta_age} anos (idade real: {p.age})
- FC repouso: {last.heart_rate} bpm

EVOLUÇÃO DESDE {first.measured_at.strftime('%d/%m/%Y')}:
- Peso: {first.weight} → {last.weight} kg
- Gordura: {first.fat_pct} → {last.fat_pct}%
- Músculo: {first.muscle_kg} → {last.muscle_kg} kg
- Visceral: {first.visceral} → {last.visceral}
- Idade metabólica: {first.meta_age} → {last.meta_age} anos

Interprete com foco em healthspan: qualidade de vida a longo prazo, risco metabólico, qualidade muscular e recomendações práticas."""

    try:
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=800,
        )
        interpretation = resp.choices[0].message.content
    except Exception as e:
        return jsonify({"error": f"Erro na LLM: {str(e)}"}), 502

    return jsonify({
        "interpretation": interpretation,
        "model": "llama-3.3-70b-versatile",
        "patient_id": pid,
    })


# ── HELPERS ───────────────────────────────────────────────────────────────

MEASUREMENT_FIELDS = [
    "weight", "bmi", "fat_pct", "visceral", "muscle_kg", "muscle_quality",
    "bone_kg", "bmr", "meta_age", "water_pct", "physique_rating", "heart_rate",
    "seg_musc_right_arm", "seg_musc_left_arm", "seg_musc_right_leg",
    "seg_musc_left_leg", "seg_musc_trunk",
    "seg_qual_right_arm", "seg_qual_left_arm", "seg_qual_right_leg",
    "seg_qual_left_leg", "seg_qual_trunk",
    "seg_fat_right_arm", "seg_fat_left_arm", "seg_fat_right_leg",
    "seg_fat_left_leg", "seg_fat_trunk",
]


def _fill_measurement(m: Measurement, data: dict):
    for f in MEASUREMENT_FIELDS:
        if f in data and data[f] is not None:
            setattr(m, f, data[f])


def _bulk_insert(pid: int, rows: list[dict]) -> list[Measurement]:
    inserted = []
    for row in rows:
        measured_at = row.pop("measured_at")
        exists = Measurement.query.filter_by(
            patient_id=pid, measured_at=measured_at).first()
        if exists:
            continue
        m = Measurement(patient_id=pid, source="tanita_csv",
                        measured_at=measured_at)
        _fill_measurement(m, row)
        db.session.add(m)
        inserted.append(m)
    return inserted


def _risk_flags(m: Measurement, p: Patient) -> list[dict]:
    flags = []
    sex = p.sex or "M"

    if m.visceral and m.visceral > 14:
        flags.append({"field": "visceral", "level": "alert",
                      "message": f"Gordura visceral elevada ({m.visceral}) — risco cardiovascular aumentado"})
    elif m.visceral and m.visceral > 9:
        flags.append({"field": "visceral", "level": "warn",
                      "message": f"Gordura visceral limítrofe ({m.visceral}) — monitorar"})

    fat_threshold_alert = 35 if sex == "F" else 30
    fat_threshold_warn  = 30 if sex == "F" else 25
    if m.fat_pct and m.fat_pct > fat_threshold_alert:
        flags.append({"field": "fat_pct", "level": "alert",
                      "message": f"Gordura corporal elevada ({m.fat_pct}%)"})
    elif m.fat_pct and m.fat_pct > fat_threshold_warn:
        flags.append({"field": "fat_pct", "level": "warn",
                      "message": f"Gordura corporal acima do ideal ({m.fat_pct}%)"})

    if m.meta_age and p.age:
        diff = m.meta_age - p.age
        if diff > 8:
            flags.append({"field": "meta_age", "level": "alert",
                          "message": f"Idade metabólica {diff:.0f} anos acima da real"})
        elif diff > 3:
            flags.append({"field": "meta_age", "level": "warn",
                          "message": f"Idade metabólica {diff:.0f} anos acima da real"})

    if m.bmi and m.bmi >= 30:
        flags.append({"field": "bmi", "level": "alert",
                      "message": f"IMC {m.bmi:.1f} — obesidade grau I ou superior"})
    elif m.bmi and m.bmi >= 25:
        flags.append({"field": "bmi", "level": "warn",
                      "message": f"IMC {m.bmi:.1f} — sobrepeso"})

    return flags
