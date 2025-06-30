from flask import Flask, render_template, request, redirect, session, url_for
import subprocess, os, hashlib

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secret")
process = None

PASS_FILE = "password.txt"
DEFAULT_PASS = "Admin@123"

def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()

# Init password file
if not os.path.exists(PASS_FILE):
    with open(PASS_FILE, "w") as f:
        f.write(hash_pass(DEFAULT_PASS))

@app.route('/login', methods=['GET', 'POST'])
def login():
    msg = ''
    if request.method == 'POST':
        pw = request.form.get('password')
        with open(PASS_FILE) as f:
            saved = f.read().strip()
        if hash_pass(pw) == saved:
            session['logged_in'] = True
            session['first_login'] = (pw == DEFAULT_PASS)
            return redirect('/')
        msg = "Sai mật khẩu!"
    return render_template("login.html", msg=msg)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/change', methods=['GET', 'POST'])
def change_pass():
    if not session.get('logged_in'): return redirect('/login')
    msg = ''
    if request.method == 'POST':
        new = request.form.get('newpass')
        if len(new) < 6:
            msg = "Mật khẩu quá ngắn"
        else:
            with open(PASS_FILE, 'w') as f:
                f.write(hash_pass(new))
            session['first_login'] = False
            msg = "Đổi mật khẩu thành công!"
    return render_template("change.html", msg=msg)

@app.route('/', methods=['GET', 'POST'])
def index():
    global process
    if not session.get('logged_in'): return redirect('/login')
    if session.get('first_login'): return redirect('/change')

    msg = ''
    if request.method == 'POST':
        action = request.form.get('action')
        src = request.form.get('src')
        dst = request.form.get('dst')
        if action == 'start' and src and dst and process is None:
            cmd = ['ffmpeg', '-re', '-i', src, '-c:v', 'copy', '-c:a', 'aac', '-f', 'flv', dst]
            process = subprocess.Popen(cmd)
            msg = '✅ Đã bắt đầu livestream.'
        elif action == 'stop' and process:
            process.terminate(); process = None
            msg = '⛔️ Livestream đã dừng.'
    status = '🔴 ĐANG LIVESTREAM' if process else '🟢 IDLE'
    return render_template('index.html', status=status, msg=msg)

@app.route('/healthz')
def healthz():
    return 'OK', 200

if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=PORT)
