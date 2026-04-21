"""
Application entry point.

Run with:  python app.py
"""
from flask import Flask

from config import MAX_UPLOAD_SIZE, SECRET_KEY
from routes.auth import bp as auth_bp
from routes.backups import bp as backups_bp
from routes.files import bp as files_bp
from routes.servers import bp as servers_bp
from services.backup_service import start_autobackup_thread
from services.system_monitor import start_cpu_monitor


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE

    app.register_blueprint(auth_bp)
    app.register_blueprint(servers_bp)
    app.register_blueprint(backups_bp)
    app.register_blueprint(files_bp)

    return app


if __name__ == '__main__':
    start_cpu_monitor()
    start_autobackup_thread()
    application = create_app()
    application.run(host='0.0.0.0', port=8390, debug=False)
