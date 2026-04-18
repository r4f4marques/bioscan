"""
BioScan Healthspan — API Routes
"""

import os
import re
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, g, send_file
import jwt

from .models import db, User, Patient, Measurement, AuditLog
from .tanita_parser import parse_tanita_file, parse_tanita_csv
from .pdf_report import generate_pdf

bioscan_bp = Blueprint("bioscan", __name__)

JWT_EXPIRES = timedelta(hours=12)


def _jwt_secret():
    return os.environ.get("JWT_SECRET", "dev-only-change-in-prod")


# ── VALIDADORES BRASIL ────────────────────────────────────────────────────

def validate_cpf(cpf: str) -> bool:
    """Valida CPF brasileiro. Aceita com ou sem formatação."""
    if not cpf:
        return False
    cpf = re.sub(r"\D", "", cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in range(9, 11):
        value = sum(int(cpf[num]) * (i + 1 - num) for num in range(i))
        digit = (value * 10) % 11
        if digit == 10:
            digit = 0
        if digit != int(cpf[i]):
            return False
    return True


def format_cpf(cpf: str) -> str:
    """Formata CPF: 12345678900 → 123.456.789-00"""
    cpf = re.sub(r"\D", "", cpf or "")
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def format_phone(phone: str) -> str:
    """Formata celular: 11912345678 → (11) 91234-5678"""
    p = re.sub(r"\D", "", phone or "")
    if len(p) == 11:
        return f"({p[:2]}) {p[2:7]}-{p[7:]}"
    if len(p) == 10:
        return f"({p[:2]}) {p[2:6]}-{p[6:]}"
    return phone


# ── HEALTH / DEBUG ────────────────────────────────────────────────────────

@bioscan_bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "bioscan-healthspan"})


# ── JWT AUTH ──────────────────────────────────────────────────────────────

def create_token(user: User) -> str:
    payload = {
        "sub":  str(user.id),
        "role": user.role,
        "exp":  datetime.now(timezone.utc) + JWT_EXPIRES,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def require_auth(fn):
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

        user = db.session.get(User, int(payload["sub"]))
        if not user or not user.is_active:
            return jsonify({"error": "Usuário não encontrado"}), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def require_role(*roles):
    def decorator(fn):
        @wraps(fn)
        @require_auth
        def wrapper(*args, **kwargs):
            if g.user.role not in roles:
                return jsonify({"error": "Permissão negada"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ── AUDIT LOG HELPER ──────────────────────────────────────────────────────

def log_action(action: str, entity_type: str = None, entity_id: int = None,
               patient_id: int = None, details: dict = None):
    """
    Registra uma ação de escrita no log de auditoria.
    Nunca levanta exceção — falha silenciosamente se algo der errado
    (logar auditoria não pode quebrar o fluxo principal).
    """
    import json
    try:
        user = getattr(g, "user", None)

        log = AuditLog(
            user_id     = user.id if user else None,
            user_email  = user.email if user else None,
            user_name   = user.name if user else None,
            action      = action,
            entity_type = entity_type,
            entity_id   = entity_id,
            patient_id  = patient_id,
            details     = json.dumps(details, default=str) if details else None,
            ip_address  = request.remote_addr if request else None,
        )
        db.session.add(log)
        # NÃO fazer commit aqui — deixar junto com a transação da ação.
        # Isso garante que se a ação falhar, o log também não é persistido.
    except Exception as e:
        print(f"[BioScan] Audit log falhou ({action}): {e}")


# ── AUTH ENDPOINTS ────────────────────────────────────────────────────────

@bioscan_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").lower().strip()
    user = User.query.filter_by(email=email).first()

    if not user or not user.is_active:
        return jsonify({"error": "Credenciais inválidas"}), 401

    if user.role == "patient":
        birth_date = data.get("birth_date", "")
        if not user.check_birth_date(birth_date):
            return jsonify({"error": "Credenciais inválidas"}), 401
    else:
        password = data.get("password", "")
        if not user.check_password(password):
            return jsonify({"error": "Credenciais inválidas"}), 401

    return jsonify({
        "token": create_token(user),
        "user":  user.to_dict(),
    })


@bioscan_bp.post("/auth/create-doctor")
def create_doctor():
    admin_key = request.headers.get("X-Admin-Key", "")
    expected  = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        return jsonify({"error": "Não autorizado"}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("email") or not data.get("password"):
        return jsonify({"error": "email e password obrigatórios"}), 400

    if User.query.filter_by(email=data["email"].lower()).first():
        return jsonify({"error": "E-mail já cadastrado"}), 409

    user = User(email=data["email"].lower(), role="doctor", name=data.get("name", ""))
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


@bioscan_bp.get("/patients/by-cpf/<cpf>")
@require_role("doctor")
def get_patient_by_cpf(cpf):
    """Busca paciente por CPF (com ou sem formatação)."""
    cpf_formatted = format_cpf(cpf)
    p = Patient.query.filter_by(cpf=cpf_formatted).first()
    if not p:
        return jsonify({"error": "Paciente não encontrado"}), 404
    return jsonify(p.to_dict(include_measurements=True))


@bioscan_bp.post("/patients")
@require_role("doctor")
def create_patient():
    """
    Cria paciente + conta de usuário.
    Body obrigatórios: name, email, birth_date, cpf
    Opcionais: sex, height_cm, phone, tags, notes
    """
    data = request.get_json(silent=True) or {}

    if not data.get("name"):
        return jsonify({"error": "Nome obrigatório"}), 400
    if not data.get("email"):
        return jsonify({"error": "E-mail obrigatório"}), 400
    if not data.get("birth_date"):
        return jsonify({"error": "Data de nascimento obrigatória"}), 400
    if not data.get("cpf"):
        return jsonify({"error": "CPF obrigatório"}), 400

    # Valida CPF
    if not validate_cpf(data["cpf"]):
        return jsonify({"error": "CPF inválido"}), 400
    cpf_formatted = format_cpf(data["cpf"])

    # Verifica duplicatas
    if User.query.filter_by(email=data["email"].lower()).first():
        return jsonify({"error": "E-mail já cadastrado"}), 409
    if Patient.query.filter_by(cpf=cpf_formatted).first():
        return jsonify({"error": "CPF já cadastrado"}), 409

    birth = datetime.strptime(data["birth_date"], "%Y-%m-%d").date()

    p = Patient(
        name       = data["name"],
        cpf        = cpf_formatted,
        phone      = format_phone(data.get("phone")) if data.get("phone") else None,
        birth_date = birth,
        sex        = data.get("sex"),
        height_cm  = data.get("height_cm"),
        notes      = data.get("notes"),
        tags       = ",".join(data.get("tags", [])),
        created_by = g.user.id,
    )
    db.session.add(p)
    db.session.flush()

    user = User(
        email      = data["email"].lower(),
        role       = "patient",
        name       = data["name"],
        birth_date = birth,
        patient_id = p.id,
    )
    db.session.add(user)

    log_action(
        "patient.create",
        entity_type="patient",
        entity_id=p.id,
        patient_id=p.id,
        details={"name": p.name, "cpf": p.cpf, "email": user.email},
    )

    db.session.commit()

    return jsonify({
        "patient": p.to_dict(),
        "user":    user.to_dict(),
        "login_info": {
            "email":      user.email,
            "birth_date": data["birth_date"],
            "note":       "Paciente faz login com email + data de nascimento",
        }
    }), 201


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

    # Snapshot dos campos antes da alteração (para log)
    changed_fields = {}

    def track(field, new_value, old_value):
        """Registra campo alterado se o valor mudou."""
        if new_value != old_value:
            changed_fields[field] = {"from": old_value, "to": new_value}

    for field in ("name", "sex", "notes"):
        if field in data:
            track(field, data[field], getattr(p, field))
            setattr(p, field, data[field])
    if "height_cm" in data:
        track("height_cm", data["height_cm"], p.height_cm)
        p.height_cm = data["height_cm"]
    if "tags" in data:
        new_tags = ",".join(data["tags"])
        track("tags", new_tags, p.tags)
        p.tags = new_tags
    if "birth_date" in data and data["birth_date"]:
        new_birth = datetime.strptime(data["birth_date"], "%Y-%m-%d").date()
        track("birth_date", str(new_birth), str(p.birth_date))
        p.birth_date = new_birth
        # Mantém o User do paciente sincronizado para ele continuar logando
        linked_user = User.query.filter_by(patient_id=p.id, role="patient").first()
        if linked_user:
            linked_user.birth_date = new_birth
    if "phone" in data:
        new_phone = format_phone(data["phone"]) if data["phone"] else None
        track("phone", new_phone, p.phone)
        p.phone = new_phone
    if "cpf" in data and data["cpf"]:
        if not validate_cpf(data["cpf"]):
            return jsonify({"error": "CPF inválido"}), 400
        cpf_formatted = format_cpf(data["cpf"])
        # Verifica se o CPF novo já pertence a outro paciente
        existing = Patient.query.filter(
            Patient.cpf == cpf_formatted,
            Patient.id != p.id
        ).first()
        if existing:
            return jsonify({"error": "CPF já cadastrado para outro paciente"}), 409
        track("cpf", cpf_formatted, p.cpf)
        p.cpf = cpf_formatted

    # Sincroniza name no User vinculado também
    if "name" in data:
        linked_user = User.query.filter_by(patient_id=p.id, role="patient").first()
        if linked_user:
            linked_user.name = data["name"]

    if changed_fields:
        log_action(
            "patient.update",
            entity_type="patient",
            entity_id=p.id,
            patient_id=p.id,
            details={"changes": changed_fields},
        )

    db.session.commit()
    return jsonify(p.to_dict())


@bioscan_bp.delete("/patients/<int:pid>")
@require_role("doctor")
def delete_patient(pid):
    p = db.get_or_404(Patient, pid)

    # Snapshot para log antes de deletar
    snapshot = {
        "name": p.name,
        "cpf": p.cpf,
        "measurements_count": Measurement.query.filter_by(patient_id=p.id).count(),
    }

    # Remove User vinculado ao paciente (para não ficar órfão)
    linked_user = User.query.filter_by(patient_id=p.id, role="patient").first()
    if linked_user:
        db.session.delete(linked_user)

    log_action(
        "patient.delete",
        entity_type="patient",
        entity_id=p.id,
        patient_id=p.id,
        details=snapshot,
    )

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

    return jsonify([m.to_dict() for m in q.order_by(Measurement.measured_at).all()])


@bioscan_bp.post("/patients/<int:pid>/measurements")
@require_role("doctor")
def add_measurement(pid):
    db.get_or_404(Patient, pid)
    data = request.get_json(silent=True) or {}
    m = Measurement(patient_id=pid, source="manual",
                    measured_at=datetime.now(timezone.utc))
    _fill_measurement(m, data)
    db.session.add(m)
    db.session.flush()

    log_action(
        "measurement.create",
        entity_type="measurement",
        entity_id=m.id,
        patient_id=pid,
        details={"source": "manual", "measured_at": m.measured_at.isoformat()},
    )

    db.session.commit()
    return jsonify(m.to_dict()), 201


@bioscan_bp.delete("/patients/<int:pid>/measurements/<int:mid>")
@require_role("doctor")
def delete_measurement(pid, mid):
    m = Measurement.query.filter_by(id=mid, patient_id=pid).first_or_404()

    log_action(
        "measurement.delete",
        entity_type="measurement",
        entity_id=m.id,
        patient_id=pid,
        details={
            "source": m.source,
            "measured_at": m.measured_at.isoformat(),
            "weight": m.weight,
            "fat_pct": m.fat_pct,
        },
    )

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
            patient_id=pid, measured_at=measured_at).first()
        if exists:
            skipped += 1
            continue
        m = Measurement(patient_id=pid, source="tanita_csv",
                        measured_at=measured_at)
        _fill_measurement(m, row)
        db.session.add(m)
        inserted.append(m)

    db.session.flush()

    if inserted:
        log_action(
            "measurement.import_csv",
            entity_type="measurement",
            patient_id=pid,
            details={
                "inserted": len(inserted),
                "skipped": skipped,
                "measurement_ids": [m.id for m in inserted],
            },
        )

    db.session.commit()
    return jsonify({
        "inserted": len(inserted),
        "skipped":  skipped,
        "measurements": [m.to_dict() for m in inserted],
    }), 201


# ── PDF IMPORT (via LLM Vision) ──────────────────────────────────────────

@bioscan_bp.post("/patients/<int:pid>/import-file")
@bioscan_bp.post("/patients/<int:pid>/import-pdf")   # alias legado
@require_role("doctor")
def import_file(pid):
    """
    Importa medição de arquivo Tanita/InBody via Groq Vision.
    Aceita PDF (multi-página, lê a 1ª) ou imagem (JPEG/PNG/HEIC/WebP).
    Detecta o fabricante automaticamente.

    Aceita o arquivo em qualquer um destes campos do form: 'file', 'pdf', 'image'.
    """
    from .pdf_parser import parse_bioimpedance_file

    db.get_or_404(Patient, pid)

    # Aceita diferentes nomes de campo para compatibilidade
    file = request.files.get("file") or request.files.get("pdf") or request.files.get("image")
    if not file:
        return jsonify({
            "error": "Nenhum arquivo enviado (campos aceitos: 'file', 'pdf', 'image')"
        }), 400

    file_bytes = file.read()
    if not file_bytes:
        return jsonify({"error": "Arquivo vazio"}), 400

    # Log do nome original para auditoria/debug
    original_filename = getattr(file, "filename", "desconhecido")

    try:
        row = parse_bioimpedance_file(file_bytes)
    except RuntimeError as e:
        return jsonify({"error": f"Configuração: {str(e)}"}), 500
    except ValueError as e:
        return jsonify({"error": f"Erro na extração: {str(e)}"}), 422
    except Exception as e:
        return jsonify({"error": f"Erro ao processar arquivo: {str(e)}"}), 500

    measured_at = row.pop("measured_at")
    detected_name = row.pop("_patient_name_detected", None)
    manufacturer = row.pop("_manufacturer", "unknown")

    # Evita duplicatas pela data de medição
    exists = Measurement.query.filter_by(
        patient_id=pid, measured_at=measured_at).first()
    if exists:
        return jsonify({
            "error": f"Já existe medição para {measured_at.strftime('%d/%m/%Y')} neste paciente",
            "measurement_id": exists.id,
            "detected_name": detected_name,
            "manufacturer": manufacturer,
        }), 409

    # Detecta se foi imagem ou PDF (pelos magic bytes do bytes bruto)
    is_pdf = file_bytes[:4] == b"%PDF"
    file_suffix = "pdf" if is_pdf else "img"

    # source reflete fabricante + tipo de arquivo
    if manufacturer in ("tanita", "inbody"):
        source = f"{manufacturer}_{file_suffix}"
    else:
        source = file_suffix

    m = Measurement(patient_id=pid, source=source, measured_at=measured_at)
    _fill_measurement(m, row)
    db.session.add(m)
    db.session.flush()

    log_action(
        "measurement.import_file",
        entity_type="measurement",
        entity_id=m.id,
        patient_id=pid,
        details={
            "manufacturer": manufacturer,
            "source": source,
            "file_type": "pdf" if is_pdf else "image",
            "original_filename": original_filename,
            "measured_at": m.measured_at.isoformat(),
            "detected_name": detected_name,
        },
    )

    db.session.commit()

    return jsonify({
        "inserted": 1,
        "measurement": m.to_dict(),
        "detected_name": detected_name,
        "manufacturer": manufacturer,
        "file_type": "pdf" if is_pdf else "image",
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

    user_prompt = f"""Paciente: {p.name}, {p.age or 'idade não informada'} anos, sexo {p.sex or 'não informado'}, altura {p.height_cm or 'não informada'} cm.

MEDIÇÃO MAIS RECENTE ({last.measured_at.strftime('%d/%m/%Y')}):
- Peso: {last.weight} kg | IMC: {last.bmi}
- Gordura corporal: {last.fat_pct}% | Gordura visceral: {last.visceral}
- Massa muscular: {last.muscle_kg} kg | Qualidade muscular: {last.muscle_quality}
- Massa óssea: {last.bone_kg} kg
- Água corporal: {last.water_pct}%
- Metabolismo basal: {last.bmr} kcal
- Idade metabólica: {last.meta_age} anos (idade real: {p.age or 'não informada'})
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


# ── PDF REPORT ───────────────────────────────────────────────────────────

@bioscan_bp.get("/patients/<int:pid>/report")
@require_role("doctor")
def patient_report_pdf(pid):
    """
    Gera relatório PDF completo do paciente e retorna para download.
    Inclui última medição, alertas clínicos, gráficos segmentais,
    evolução temporal e histórico completo.
    """
    import io
    p = db.get_or_404(Patient, pid)
    measurements = p.measurements

    if not measurements:
        return jsonify({"error": "Paciente sem medições"}), 404

    # Gera flags clínicos da última medição
    flags = _risk_flags(measurements[-1], p)

    try:
        pdf_bytes = generate_pdf(p, measurements, flags)
    except Exception as e:
        return jsonify({"error": f"Erro ao gerar PDF: {str(e)}"}), 500

    # Nome de arquivo sanitizado com data
    safe_name = re.sub(r"[^\w\s-]", "", p.name).strip().replace(" ", "_")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"BioScan_{safe_name}_{date_str}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ── AUDIT LOG ENDPOINTS ───────────────────────────────────────────────────

@bioscan_bp.get("/audit-logs")
@require_role("doctor")
def list_audit_logs():
    """
    Lista os logs de auditoria mais recentes.
    Query params opcionais:
    - patient_id: filtra por paciente
    - action: filtra por tipo de ação (ex: 'patient.delete')
    - limit: máximo de registros (default 100, max 500)
    """
    query = AuditLog.query.order_by(AuditLog.timestamp.desc())

    patient_id = request.args.get("patient_id", type=int)
    if patient_id:
        query = query.filter_by(patient_id=patient_id)

    action = request.args.get("action")
    if action:
        query = query.filter_by(action=action)

    limit = min(request.args.get("limit", 100, type=int), 500)
    logs = query.limit(limit).all()

    return jsonify([log.to_dict() for log in logs])


@bioscan_bp.get("/patients/<int:pid>/audit-logs")
@require_role("doctor")
def patient_audit_logs(pid):
    """Lista todos os logs de auditoria relacionados a um paciente específico."""
    db.get_or_404(Patient, pid)

    logs = AuditLog.query.filter_by(patient_id=pid)\
        .order_by(AuditLog.timestamp.desc())\
        .limit(200).all()

    return jsonify([log.to_dict() for log in logs])


# ── HELPERS ───────────────────────────────────────────────────────────────

MEASUREMENT_FIELDS = [
    "weight", "bmi", "fat_pct", "visceral", "muscle_kg", "muscle_quality",
    "bone_kg", "bmr", "meta_age", "water_pct", "water_kg",
    "physique_rating", "heart_rate",
    "smi", "protein_kg", "mineral_kg", "ffm_kg",
    "waist_hip_ratio", "obesity_degree", "recommended_kcal", "inbody_score",
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
