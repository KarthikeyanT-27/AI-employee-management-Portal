"""Production entry point:  gunicorn wsgi:app -k gevent -w 1 --worker-connections 400 --timeout 120"""
from app import create_app

app = create_app()
