"""
BioScan Healthspan — Entrypoint WSGI para Gunicorn
Uso: gunicorn -c gunicorn_config.py bioscan.wsgi:app
"""

from .app import create_app

app = create_app()
