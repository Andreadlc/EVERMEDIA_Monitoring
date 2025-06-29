# === Importation des modules nécessaires ===
from flask import Flask, flash, render_template, request, redirect, Response, url_for, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
import subprocess
import socket


# === Initialisation de l'application Flask ===

app = Flask(__name__)
app.secret_key = 'une_cle_secrete_bien_longue'


# === Fichiers de stockage (persistants) ===
ILO_FILE = 'ilos.json'
SITE_FILE = 'sites.json'
USERS_FILE = 'users.json'


# ----- Gestion iLO -----
if os.path.exists(ILO_FILE):
    with open(ILO_FILE, 'r') as f:
        ilos = json.load(f)
else:
    ilos = []

def save_ilos():
    with open(ILO_FILE, 'w') as f:
        json.dump(ilos, f)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # connexion "fake" pour connaître la bonne interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ----- Gestion utilisateurs -----
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

if os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'r') as f:
        users_data = json.load(f)
else:
    users_data = []

class User(UserMixin):
    def __init__(self, username, role):
        self.id = username
        self.role = role

    def get_role(self):
        return self.role

@login_manager.user_loader
def load_user(user_id):
    for user in users_data:
        if user['username'] == user_id:
            return User(user['username'], user['role'])
    return None

# ----- Routes sécurisées -----
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.get_role() != 'admin':
            return "Accès interdit", 403
        return f(*args, **kwargs)
    return decorated


# Page d'accueil (nécessite une connexion)

@app.route('/')
@login_required
def index():
    ip = get_local_ip()
    return render_template('index.html', grafana_ip=ip)


# Page de login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        for user in users_data:
            if user['username'] == username and check_password_hash(user['password'], password):
                login_user(User(username, user['role']))
                return redirect(url_for('index'))
        return render_template('login.html', error="Nom d'utilisateur ou mot de passe incorrect")
    return render_template('login.html')


# Déconnexion
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# Ajout d’un site (admin uniquement)
@app.route('/sites', methods=['GET', 'POST'])
@login_required
@admin_required
def add_site():

    if os.path.exists(SITE_FILE):
        with open(SITE_FILE, 'r') as f:
            sites = json.load(f)
    else:
        sites = []

    if request.method == 'POST':
        new_site = request.form['site'].strip()
        if new_site and new_site not in sites:
            sites.append(new_site)
            with open(SITE_FILE, 'w') as f:
                json.dump(sites, f)
        return redirect('/sites')

    return render_template('sites.html', sites=sites)

# Suppression d’un site

@app.route('/delete_site/<site>')
@login_required
@admin_required
def delete_site(site):
    if os.path.exists(SITE_FILE):
        with open(SITE_FILE, 'r') as f:
            sites = json.load(f)
        if site in sites:
            sites.remove(site)
            with open(SITE_FILE, 'w') as f:
                json.dump(sites, f)
    return redirect('/sites')

# Ajout d'un iLO

@app.route('/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_ilo():
    with open(SITE_FILE, 'r') as f:
        sites = json.load(f)

    if request.method == 'POST':
        ip = request.form['ip']
        site = request.form['site']
        username = request.form['username']
        password = request.form['password']

        new_ilo = {
            "ip": ip,
            "site": site,
            "username": username,
            "password": password
        }

        if not any(i['ip'] == ip for i in ilos):
            ilos.append(new_ilo)
            save_ilos()

        return redirect('/add')

    return render_template('add_ilo.html', sites=sites, ilos=ilos)

# Suppression d'un iLO par IP
@app.route('/delete/<ip>')
@login_required
@admin_required
def delete_ilo(ip):
    global ilos
    ilos = [i for i in ilos if i['ip'] != ip]
    save_ilos()
    return redirect('/add')

# Bascule (ON/OFF) du script multi_ilo_web.service
@app.route('/toggle_script', methods=['POST'])
@login_required
@admin_required
def toggle_script():
    import sys
    print("TOGGLE SCRIPT appelé", flush=True)
    result = subprocess.run(["systemctl", "is-active", "--quiet", "multi_ilo_web.service"])
    print("Status actuel:", result.returncode, flush=True)
    if result.returncode == 0:
        subprocess.run(["sudo", "systemctl", "stop", "multi_ilo_web.service"])
        print("Service arrêté", flush=True)
        return jsonify({"running": False})
    else:
        subprocess.run(["sudo", "systemctl", "start", "multi_ilo_web.service"])
        print("Service démarré", flush=True)
        return jsonify({"running": True})


@app.route('/script_status')
@login_required
def script_status():
    # Vérifie le statut actuel du service systemd
    result = subprocess.run(["systemctl", "is-active", "--quiet", "multi_ilo_web.service"])
    is_running = (result.returncode == 0)
    return jsonify({"running": is_running})

# Page de gestion des utilisateurs : ajout / suppression
@app.route('/manage_users', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_users():
    if request.method == 'POST':
        if 'add' in request.form:
            new_user = request.form['new_username']
            new_pass = request.form['new_password']
            new_role = request.form['new_role']
            if new_user and new_pass and new_role:
                if any(u['username'] == new_user for u in users_data):
                    flash("Utilisateur déjà existant", "error")
                else:
                    users_data.append({
                        "username": new_user,
                        "password": generate_password_hash(new_pass),
                        "role": new_role
                    })
                    with open(USERS_FILE, 'w') as f:
                        json.dump(users_data, f)
                    flash("Utilisateur ajouté avec succès", "success")

        elif 'delete' in request.form:
            to_delete = request.form['delete']
            if to_delete == current_user.id:
                flash("Impossible de supprimer votre propre compte.", "error")
            else:
                updated_users = [u for u in users_data if u['username'] != to_delete]
                with open(USERS_FILE, 'w') as f:
                    json.dump(updated_users, f)
                flash(f"Utilisateur {to_delete} supprimé", "success")
                users_data.clear()
                users_data.extend(updated_users)

    return render_template('manage_users.html', users=users_data)




if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
