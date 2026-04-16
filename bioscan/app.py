"""
BioScan Healthspan — App Factory
Integração com TriDash (Flask + Railway)

USO STANDALONE:
    python -m bioscan.app

INTEGRAÇÃO NO TRIDASH:
    No seu app.py principal do TriDash:
        from bioscan.app import init_bioscan
        init_bioscan(app)
"""

import os
from flask import Flask
from .models import db
from .routes import bioscan_bp


def init_bioscan(app: Flask):
    """
    Registra o BioScan em um app Flask existente (TriDash).
    Chame depois de configurar o app, antes do primeiro request.
    """
    if not app.extensions.get("sqlalchemy"):
        db.init_app(app)

    app.register_blueprint(bioscan_bp, url_prefix="/bioscan")

    with app.app_context():
        db.create_all()

    return app


def create_app() -> Flask:
    """App factory para uso standalone / testes."""
    app = Flask(__name__)

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

    with app.app_context():
        db.create_all()
        _seed_demo()

    return app


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
