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
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), nullable=False, default="doctor")
    # role: "doctor" | "patient"

    name          = db.Column(db.String(120))
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active     = db.Column(db.Boolean, default=True)

    # Se role == "patient", aponta para o Patient vinculado
    patient_id    = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=True)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def to_dict(self):
        return {"id": self.id, "email": self.email, "role": self.role, "name": self.name}


# ── PATIENTS ──────────────────────────────────────────────────────────────

class Patient(db.Model):
    __tablename__ = "patients"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    birth_date  = db.Column(db.Date, nullable=True)
    sex         = db.Column(db.String(1), nullable=True)   # "M" | "F"
    height_cm   = db.Column(db.Float, nullable=True)
    notes       = db.Column(db.Text, nullable=True)
    tags        = db.Column(db.String(255), nullable=True)  # comma-separated
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
        d = {
            "id":         self.id,
            "name":       self.name,
            "age":        self.age,
            "sex":        self.sex,
            "height_cm":  self.height_cm,
            "tags":       self.tags.split(",") if self.tags else [],
            "created_at": self.created_at.isoformat(),
        }
        if include_measurements:
            d["measurements"] = [m.to_dict() for m in self.measurements]
        elif self.latest:
            d["latest"] = self.latest.to_dict()
        return d


# ── MEASUREMENTS ──────────────────────────────────────────────────────────

class Measurement(db.Model):
    """
    Uma linha do CSV Tanita = uma Measurement.
    Colunas espelham exatamente o TANITA_MAP do parser.
    """
    __tablename__ = "measurements"

    id          = db.Column(db.Integer, primary_key=True)
    patient_id  = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    measured_at = db.Column(db.DateTime, nullable=False, index=True)
    source      = db.Column(db.String(40), default="tanita_csv")
    # source: "tanita_csv" | "manual" | "api"

    # ── Composição geral ──────────────────────────────────────────────────
    weight      = db.Column(db.Float)   # kg
    bmi         = db.Column(db.Float)
    fat_pct     = db.Column(db.Float)   # %
    visceral    = db.Column(db.Float)   # índice (1–59)
    muscle_kg   = db.Column(db.Float)   # kg
    muscle_quality = db.Column(db.Float)  # índice Tanita (0–100)
    bone_kg     = db.Column(db.Float)   # kg
    bmr         = db.Column(db.Float)   # kcal/dia
    meta_age    = db.Column(db.Float)   # anos
    water_pct   = db.Column(db.Float)   # % água corporal total
    physique_rating = db.Column(db.Integer)
    heart_rate  = db.Column(db.Integer) # bpm

    # ── Segmental — músculo (kg) ──────────────────────────────────────────
    seg_musc_right_arm   = db.Column(db.Float)
    seg_musc_left_arm    = db.Column(db.Float)
    seg_musc_right_leg   = db.Column(db.Float)
    seg_musc_left_leg    = db.Column(db.Float)
    seg_musc_trunk       = db.Column(db.Float)

    # ── Segmental — qualidade muscular ───────────────────────────────────
    seg_qual_right_arm   = db.Column(db.Float)
    seg_qual_left_arm    = db.Column(db.Float)
    seg_qual_right_leg   = db.Column(db.Float)
    seg_qual_left_leg    = db.Column(db.Float)
    seg_qual_trunk       = db.Column(db.Float)   # pode ser NULL (Tanita emite "-")

    # ── Segmental — gordura (%) ───────────────────────────────────────────
    seg_fat_right_arm    = db.Column(db.Float)
    seg_fat_left_arm     = db.Column(db.Float)
    seg_fat_right_leg    = db.Column(db.Float)
    seg_fat_left_leg     = db.Column(db.Float)
    seg_fat_trunk        = db.Column(db.Float)

    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    patient = db.relationship("Patient", back_populates="measurements")

    def to_dict(self):
        return {
            "id":           self.id,
            "measured_at":  self.measured_at.isoformat(),
            "source":       self.source,
            # geral
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
            "physique_rating": self.physique_rating,
            "heart_rate":   self.heart_rate,
            # segmental músculo
            "seg_musc": {
                "right_arm": self.seg_musc_right_arm,
                "left_arm":  self.seg_musc_left_arm,
                "right_leg": self.seg_musc_right_leg,
                "left_leg":  self.seg_musc_left_leg,
                "trunk":     self.seg_musc_trunk,
            },
            # segmental qualidade
            "seg_qual": {
                "right_arm": self.seg_qual_right_arm,
                "left_arm":  self.seg_qual_left_arm,
                "right_leg": self.seg_qual_right_leg,
                "left_leg":  self.seg_qual_left_leg,
                "trunk":     self.seg_qual_trunk,
            },
            # segmental gordura
            "seg_fat": {
                "right_arm": self.seg_fat_right_arm,
                "left_arm":  self.seg_fat_left_arm,
                "right_leg": self.seg_fat_right_leg,
                "left_leg":  self.seg_fat_left_leg,
                "trunk":     self.seg_fat_trunk,
            },
        }
