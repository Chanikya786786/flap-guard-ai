"""Production WSGI entry point for Gunicorn / Render deployment."""

from web_app import create_app

app = create_app()