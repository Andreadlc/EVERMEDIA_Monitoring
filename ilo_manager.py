from flask import Flask, render_template, request, redirect, Response, url_for, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
import json
import os
import subprocess

app = Flask(__name__)
app.secret_key = 'une_cle_secrete_bien_longue'

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

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        for user in users_data:
            if user['username'] == username and user['password'] == password:
                login_user(User(username, user['role']))
                return redirect(url_for('index'))
        return render_template('login.html', error="Nom d'utilisateur ou mot de passe incorrect")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))



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


@app.route('/delete/<ip>')
@login_required
@admin_required
def delete_ilo(ip):
    global ilos
    ilos = [i for i in ilos if i['ip'] != ip]
    save_ilos()
    return redirect('/add')

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
