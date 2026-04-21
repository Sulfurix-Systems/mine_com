"""Authentication routes and login_required decorator."""
from functools import wraps

from flask import (
    Blueprint, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

from config import PASSWORD, USERNAME

bp = Blueprint('auth', __name__)


def login_required(fn):
    """Decorator for API routes – returns 401 JSON when not authenticated."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Unauthorized'}), 401
        return fn(*args, **kwargs)
    return wrapper


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if (request.form.get('username') == USERNAME
                and request.form.get('password') == PASSWORD):
            session['logged_in'] = True
            flash('Успешный вход!', 'success')
            return redirect(url_for('servers.list_servers'))
        flash('Неверный логин или пароль', 'error')
    return render_template('login.html')


@bp.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Вы вышли из системы', 'success')
    return redirect(url_for('auth.login'))
