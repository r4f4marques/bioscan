"""
BioScan Healthspan — App Factory
Integração com TriDash (Flask + Railway)
"""

import os
from flask import Flask, send_from_directory
from .models import db
from .routes import bioscan_bp


def init_bioscan(app: Flask):
    """Registra o BioScan em um app Flask existente (TriDash)."""
    if not app.extensions.get("sqlalchemy"):
        db.init_app(app)
    app.register_blueprint(bioscan_bp, url_prefix="/bioscan")
    with app.app_context():
        db.create_all()
        _migrate_schema()
    return app


def create_app() -> Flask:
    """App factory para uso standalone / testes."""
    app = Flask(__name__, static_folder="../static", static_url_path="/static")

    database_url = os.environ.get("DATABASE_URL", "sqlite:///bioscan.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config.update(
        SQLALCHEMY_DATABASE_URI        = database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS = False,
        SECRET_KEY                     = os.environ.get("SECRET_KEY", "dev-only-change-in-prod"),
        MAX_CONTENT_LENGTH             = 5 * 1024 * 1024,
    )

    db.init_app(app)
    app.register_blueprint(bioscan_bp, url_prefix="/bioscan")

    # ── Rotas do frontend ─────────────────────────────────────────────────
    @app.route("/")
    def index():
        return send_from_directory("../static", "login.html")

    @app.route("/dashboard")
    def dashboard():
        return send_from_directory("../static", "index.html")

    with app.app_context():
        db.create_all()
        _migrate_schema()
        _seed_demo()

    return app


def _migrate_schema():
    """
    Migração leve: adiciona colunas novas à tabela patients caso não existam.
    Usado para rodar de forma transparente quando o schema é atualizado.
    """
    from sqlalchemy import text, inspect

    inspector = inspect(db.engine)
    if "patients" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("patients")}
    alters = []

    if "cpf" not in existing_cols:
        alters.append("ADD COLUMN cpf VARCHAR(14)")
    if "phone" not in existing_cols:
        alters.append("ADD COLUMN phone VARCHAR(20)")

    if alters:
        # Executa cada alteração em transação própria
        with db.engine.begin() as conn:
            for alter in alters:
                try:
                    conn.execute(text(f"ALTER TABLE patients {alter}"))
                    print(f"[BioScan] Migração: patients {alter}")
                except Exception as e:
                    print(f"[BioScan] Migração falhou ({alter}): {e}")

        # Tenta criar índice único para CPF (ignora se já existe)
        if "cpf" in [a.split()[2] for a in alters]:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_patients_cpf ON patients(cpf)"
                    ))
                print("[BioScan] Migração: índice único patients.cpf criado")
            except Exception as e:
                print(f"[BioScan] Índice cpf falhou: {e}")


def _seed_demo():
    """Cria usuário demo se o banco estiver vazio."""
    from .models import User, Patient
    if User.query.count() > 0:
        return

    doctor = User(email="demo@bioscan.fit", role="doctor", name="Dr. Demo")
    doctor.set_password("demo")
    db.session.add(doctor)
    db.session.flush()

    patient = Patient(
        name="Paciente Demo", sex="M", height_cm=178, created_by=doctor.id,
        tags="Demo,Triatleta",
        notes="Paciente criado automaticamente para demonstração."
    )
    db.session.add(patient)
    db.session.commit()
    print("[BioScan] Demo seed criado: demo@bioscan.fit / demo")


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
