from flask import Flask, render_template, request, redirect, session
import subprocess, os, hashlib, psutil, uuid, json, threading, time

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secret")
PASS_FILE = "password.txt"
DEFAULT_PASS = "Admin@123"
STREAMS_FILE = "streams.json"
STREAMS = {}
PROCESSES = {}

def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()

if not os.path.exists(PASS_FILE):
    with open(PASS_FILE, "w") as f:
        f.write(hash_pass(DEFAULT_PASS))

if os.path.exists(STREAMS_FILE):
    with open(STREAMS_FILE) as f:
        STREAMS = json.load(f)

def save_streams():
    with open(STREAMS_FILE, "w") as f:
        json.dump(STREAMS, f)

def schedule_stop_by_time(sid, delay_minutes):
    def worker():
        time.sleep(delay_minutes * 60)
        if sid in PROCESSES:
            PROCESSES[sid].terminate()
            STREAMS[sid]["status"] = "stopped"
            save_streams()
    threading.Thread(target=worker, daemon=True).start()

def monitor_file_end(sid, proc):
    proc.wait()
    if sid in STREAMS and STREAMS[sid].get("file_end_stop") == "on":
        STREAMS[sid]["status"] = "stopped"
        save_streams()

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
    if not session.get('logged_in'): return redirect('/login')
    if session.get('first_login'): return redirect('/change')

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'start':
            src = request.form.get('src')
            dst = request.form.get('dst')
            vbit = request.form.get('vbit')
            abit = request.form.get('abit')
            delay = request.form.get('delaymin')
            loop = request.form.get('loop')
            file_stop = request.form.get('file_end_stop')
            if src and dst:
                sid = str(uuid.uuid4())[:8]
                cmd = ['ffmpeg']
                if src.startswith('http') and '.m3u8' in src:
                    cmd += ['-re']
                elif src.startswith('file://') or os.path.exists(src):
                    if loop == 'on':
                        cmd += ['-stream_loop', '-1']
                    cmd += ['-re']
                cmd += ['-i', src]
                cmd += ['-c:v', 'libx264']
                cmd += ['-c:a', 'aac']
                if vbit: cmd += ['-b:v', vbit]
                if abit: cmd += ['-b:a', abit]
                cmd += ['-f', 'flv', dst]
                proc = subprocess.Popen(cmd)
                STREAMS[sid] = {
                    'src': src, 'dst': dst, 'vbit': vbit, 'abit': abit,
                    'delaymin': delay, 'loop': loop, 'file_end_stop': file_stop,
                    'status': 'running'
                }
                PROCESSES[sid] = proc
                if delay:
                    try:
                        mins = int(delay)
                        schedule_stop_by_time(sid, mins)
                    except: pass
                elif file_stop == "on" and loop != "on":
                    threading.Thread(target=monitor_file_end, args=(sid, proc), daemon=True).start()
                save_streams()
        elif action == 'stop':
            sid = request.form.get('stream_id')
            if sid in PROCESSES:
                PROCESSES[sid].terminate()
                STREAMS[sid]['status'] = 'stopped'
                save_streams()
        elif action == 'delete':
            sid = request.form.get('stream_id')
            if sid in PROCESSES:
                PROCESSES[sid].terminate()
            STREAMS.pop(sid, None)
            PROCESSES.pop(sid, None)
            save_streams()

    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    return render_template('index.html', streams=STREAMS, cpu=cpu, mem=mem)

@app.route('/healthz')
def healthz():
    return 'OK', 200

if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=PORT)
