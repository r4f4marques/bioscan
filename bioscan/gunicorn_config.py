"""
BioScan Healthspan — Configuração Gunicorn para produção
Usado no Railway via Procfile: web: gunicorn -c gunicorn_config.py bioscan.wsgi:app
"""

import multiprocessing
import os

# ── BIND ──────────────────────────────────────────────────────────────────
# Railway injeta a porta via env PORT. Default 5000 para dev.
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ── WORKERS ───────────────────────────────────────────────────────────────
# Fórmula clássica do Gunicorn: (2 × CPUs) + 1
# No Railway hobby (1 vCPU), isso dá ~3 workers
workers = (multiprocessing.cpu_count() * 2) + 1

# Thread por worker — manter 1 por padrão (modelo de processo)
threads = 1

# Tipo de worker (sync é o padrão, suficiente para nossa carga)
worker_class = "sync"

# ── TIMEOUTS ──────────────────────────────────────────────────────────────
# Timeout por request (sincronizado com a escolha do usuário)
timeout = 30

# Graceful timeout: tempo que um worker tem para terminar requests pendentes
# quando recebe sinal de shutdown
graceful_timeout = 30

# Keepalive: tempo que a conexão TCP fica aberta após resposta
keepalive = 5

# ── PERFORMANCE ───────────────────────────────────────────────────────────
# Reiniciar workers após N requests (previne memory leaks em matplotlib etc.)
max_requests = 500

# Variação aleatória no max_requests para evitar que todos os workers
# reiniciem simultaneamente
max_requests_jitter = 50

# ── LOGGING ───────────────────────────────────────────────────────────────
# Railway captura stdout/stderr automaticamente
accesslog = "-"     # stdout
errorlog = "-"      # stderr
loglevel = "info"

# Formato de log mais compacto que o padrão
access_log_format = (
    '%(h)s "%(r)s" %(s)s %(b)s %(L)ss'
)

# ── PRELOAD ───────────────────────────────────────────────────────────────
# Carrega a aplicação ANTES de forkar workers (economiza memória)
# Importante para matplotlib + reportlab que são pesados
preload_app = True


def when_ready(server):
    """Hook executado quando o Gunicorn está pronto para receber requests."""
    server.log.info(f"[BioScan] Gunicorn pronto com {workers} workers")


def worker_int(worker):
    """Hook executado quando um worker recebe SIGINT (Ctrl+C)."""
    worker.log.info(f"[BioScan] Worker {worker.pid} recebeu SIGINT")
