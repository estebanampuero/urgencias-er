"""
Servidor de producción WSGI usando waitress.
Multi-thread, sin advertencia de dev server.

Uso:
    python serve.py
o:
    waitress-serve --listen=0.0.0.0:5050 --threads=8 app:app
"""
import os
from waitress import serve
from app import app, get_ip_local
from database import init_db


def main():
    init_db()
    port = int(os.environ.get("PORT", "5050"))
    threads = int(os.environ.get("THREADS", "8"))
    ip = get_ip_local()
    print("=" * 60)
    print("  Sistema de Entrega de Turno · Urgencias · waitress")
    print("=" * 60)
    print(f"  Local:    http://127.0.0.1:{port}")
    print(f"  Red LAN:  http://{ip}:{port}")
    print(f"  Threads:  {threads}")
    print("=" * 60)
    print("  Comparte la URL de Red LAN con el equipo del turno.")
    print("  Ctrl+C para detener.")
    print("=" * 60)
    serve(app, host="0.0.0.0", port=port, threads=threads, ident="urgencias")


if __name__ == "__main__":
    main()
