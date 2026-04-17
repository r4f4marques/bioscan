"""
BioScan Healthspan — Models
SQLAlchemy models compatíveis com Flask + SQLite (ou Postgres em produção)
"""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ── USERS ─────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=True)
    role          = db.Column(db.String(20), nullable=False, default="doctor")

    name          = db.Column(db.String(120))
    birth_date    = db.Column(db.Date, nullable=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active     = db.Column(db.Boolean, default=True)

    patient_id    = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=True)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw)

    def check_birth_date(self, date_str):
        if not self.birth_date:
            return False
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                from datetime import datetime as dt
                entered = dt.strptime(date_str.strip(), fmt).date()
                return entered == self.birth_date
            except ValueError:
                continue
        return False

    def to_dict(self):
        return {
            "id":         self.id,
            "email":      self.email,
            "role":       self.role,
            "name":       self.name,
            "patient_id": self.patient_id,
        }


# ── PATIENTS ──────────────────────────────────────────────────────────────

class Patient(db.Model):
    __tablename__ = "patients"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    cpf         = db.Column(db.String(14), unique=True, nullable=True, index=True)  # "123.456.789-00"
    phone       = db.Column(db.String(20), nullable=True)                           # "(11) 91234-5678"
    birth_date  = db.Column(db.Date, nullable=True)
    sex         = db.Column(db.String(1), nullable=True)
    height_cm   = db.Column(db.Float, nullable=True)
    notes       = db.Column(db.Text, nullable=True)
    tags        = db.Column(db.String(255), nullable=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    measurements = db.relationship("Measurement", back_populates="patient",
                                   order_by="Measurement.measured_at",
                                   cascade="all, delete-orphan")

    @property
    def age(self):
        if not self.birth_date:
            return None
        today = datetime.now(timezone.utc).date()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
        )

    @property
    def latest(self):
        return self.measurements[-1] if self.measurements else None

    def to_dict(self, include_measurements=False):
        # Busca o email do User vinculado a este paciente (para edição)
        linked_email = None
        linked_user = User.query.filter_by(patient_id=self.id, role="patient").first()
        if linked_user:
            linked_email = linked_user.email

        d = {
            "id":         self.id,
            "name":       self.name,
            "cpf":        self.cpf,
            "phone":      self.phone,
            "email":      linked_email,
            "age":        self.age,
            "sex":        self.sex,
            "height_cm":  self.height_cm,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "tags":       self.tags.split(",") if self.tags else [],
            "notes":      self.notes,
            "created_at": self.created_at.isoformat(),
        }
        if include_measurements:
            d["measurements"] = [m.to_dict() for m in self.measurements]
        elif self.latest:
            d["latest"] = self.latest.to_dict()
        return d


# ── MEASUREMENTS ──────────────────────────────────────────────────────────

class Measurement(db.Model):
    __tablename__ = "measurements"

    id          = db.Column(db.Integer, primary_key=True)
    patient_id  = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    measured_at = db.Column(db.DateTime, nullable=False, index=True)
    source      = db.Column(db.String(40), default="tanita_csv")

    weight          = db.Column(db.Float)
    bmi             = db.Column(db.Float)
    fat_pct         = db.Column(db.Float)
    visceral        = db.Column(db.Float)
    muscle_kg       = db.Column(db.Float)
    muscle_quality  = db.Column(db.Float)
    bone_kg         = db.Column(db.Float)
    bmr             = db.Column(db.Float)
    meta_age        = db.Column(db.Float)
    water_pct       = db.Column(db.Float)
    water_kg        = db.Column(db.Float)          # InBody: Água Corporal Total em L
    physique_rating = db.Column(db.Integer)
    heart_rate      = db.Column(db.Integer)

    # Campos adicionais InBody
    smi              = db.Column(db.Float)         # Skeletal Muscle Index (kg/m²)
    protein_kg       = db.Column(db.Float)         # Proteína (kg)
    mineral_kg       = db.Column(db.Float)         # Minerais (kg)
    ffm_kg           = db.Column(db.Float)         # Massa Livre de Gordura (kg)
    waist_hip_ratio  = db.Column(db.Float)         # Relação Cintura-Quadril
    obesity_degree   = db.Column(db.Float)         # Grau de obesidade (%)
    recommended_kcal = db.Column(db.Float)         # Ingestão calórica recomendada
    inbody_score     = db.Column(db.Float)         # Pontuação InBody (0-100)

    seg_musc_right_arm  = db.Column(db.Float)
    seg_musc_left_arm   = db.Column(db.Float)
    seg_musc_right_leg  = db.Column(db.Float)
    seg_musc_left_leg   = db.Column(db.Float)
    seg_musc_trunk      = db.Column(db.Float)

    seg_qual_right_arm  = db.Column(db.Float)
    seg_qual_left_arm   = db.Column(db.Float)
    seg_qual_right_leg  = db.Column(db.Float)
    seg_qual_left_leg   = db.Column(db.Float)
    seg_qual_trunk      = db.Column(db.Float)

    seg_fat_right_arm   = db.Column(db.Float)
    seg_fat_left_arm    = db.Column(db.Float)
    seg_fat_right_leg   = db.Column(db.Float)
    seg_fat_left_leg    = db.Column(db.Float)
    seg_fat_trunk       = db.Column(db.Float)

    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    patient = db.relationship("Patient", back_populates="measurements")

    def _seg_fat_as_percent(self, side: str):
        """
        Retorna a gordura segmental SEMPRE em %, independente do fabricante.
        - Tanita: já vem em % → retorna direto
        - InBody: vem em kg → converte % = gordura / (gordura + musc) × 100
        """
        fat_raw  = getattr(self, f"seg_fat_{side}")
        musc_raw = getattr(self, f"seg_musc_{side}")

        if fat_raw is None:
            return None

        # InBody armazena seg_fat em kg, precisa converter
        if self.source and self.source.startswith("inbody"):
            if musc_raw is None or (fat_raw + musc_raw) <= 0:
                return None
            return round(fat_raw / (fat_raw + musc_raw) * 100, 1)

        # Tanita e demais: já em %
        return fat_raw

    def to_dict(self):
        return {
            "id":           self.id,
            "measured_at":  self.measured_at.isoformat(),
            "source":       self.source,
            "weight":       self.weight,
            "bmi":          self.bmi,
            "fat_pct":      self.fat_pct,
            "visceral":     self.visceral,
            "muscle_kg":    self.muscle_kg,
            "muscle_quality": self.muscle_quality,
            "bone_kg":      self.bone_kg,
            "bmr":          self.bmr,
            "meta_age":     self.meta_age,
            "water_pct":    self.water_pct,
            "water_kg":     self.water_kg,
            "physique_rating": self.physique_rating,
            "heart_rate":   self.heart_rate,
            "smi":              self.smi,
            "protein_kg":       self.protein_kg,
            "mineral_kg":       self.mineral_kg,
            "ffm_kg":           self.ffm_kg,
            "waist_hip_ratio":  self.waist_hip_ratio,
            "obesity_degree":   self.obesity_degree,
            "recommended_kcal": self.recommended_kcal,
            "inbody_score":     self.inbody_score,
            "seg_musc": {
                "right_arm": self.seg_musc_right_arm,
                "left_arm":  self.seg_musc_left_arm,
                "right_leg": self.seg_musc_right_leg,
                "left_leg":  self.seg_musc_left_leg,
                "trunk":     self.seg_musc_trunk,
            },
            "seg_qual": {
                "right_arm": self.seg_qual_right_arm,
                "left_arm":  self.seg_qual_left_arm,
                "right_leg": self.seg_qual_right_leg,
                "left_leg":  self.seg_qual_left_leg,
                "trunk":     self.seg_qual_trunk,
            },
            # seg_fat SEMPRE em % — conversão dinâmica para InBody (vem em kg)
            "seg_fat": {
                "right_arm": self._seg_fat_as_percent("right_arm"),
                "left_arm":  self._seg_fat_as_percent("left_arm"),
                "right_leg": self._seg_fat_as_percent("right_leg"),
                "left_leg":  self._seg_fat_as_percent("left_leg"),
                "trunk":     self._seg_fat_as_percent("trunk"),
            },
            # seg_fat bruto (valores originais como salvos no banco, para auditoria)
            "seg_fat_raw": {
                "right_arm": self.seg_fat_right_arm,
                "left_arm":  self.seg_fat_left_arm,
                "right_leg": self.seg_fat_right_leg,
                "left_leg":  self.seg_fat_left_leg,
                "trunk":     self.seg_fat_trunk,
            },
        }
