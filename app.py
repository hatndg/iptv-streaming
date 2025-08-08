from flask import Flask, render_template, request, redirect, session
import subprocess, os, hashlib, psutil, uuid, json, threading, time

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "a_very_secret_key_for_flask") # Thay đổi key bí mật
PASS_FILE = "password.txt"
DEFAULT_PASS = "Admin@123"
STREAMS_FILE = "streams.json"
STREAMS = {}
PROCESSES = {}

def hash_pass(p):
    return hashlib.sha256(p.encode()).hexdigest()

# --- Khởi tạo --- 
if not os.path.exists(PASS_FILE):
    with open(PASS_FILE, "w") as f:
        f.write(hash_pass(DEFAULT_PASS))

if os.path.exists(STREAMS_FILE):
    try:
        with open(STREAMS_FILE) as f:
            STREAMS = json.load(f)
    except json.JSONDecodeError:
        STREAMS = {} # Nếu file json bị lỗi, khởi tạo rỗng

def save_streams():
    with open(STREAMS_FILE, "w") as f:
        json.dump(STREAMS, f, indent=2) # Thêm indent để file json dễ đọc hơn

def schedule_delay_stop(sid, delay_minutes):
    def worker():
        try:
            time.sleep(delay_minutes * 60)
            proc = PROCESSES.get(sid)
            if proc and proc.poll() is None: # Kiểm tra xem process có còn chạy không
                proc.terminate()
                print(f"Delayed stop for stream {sid} executed.")
                if sid in STREAMS:
                    STREAMS[sid]["status"] = "stopped"
                    save_streams()
        except Exception as e:
            print(f"Error in delay stop worker for {sid}: {e}")

    threading.Thread(target=worker, daemon=True).start()

# --- Các route xác thực ---
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
    if not session.get('logged_in'):
        return redirect('/login')
    msg = ''
    if request.method == 'POST':
        new = request.form.get('newpass')
        if len(new) < 6:
            msg = "Mật khẩu phải có ít nhất 6 ký tự"
        else:
            with open(PASS_FILE, 'w') as f:
                f.write(hash_pass(new))
            session['first_login'] = False
            msg = "Đổi mật khẩu thành công!"
    return render_template("change.html", msg=msg)

# --- Route chính ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if not session.get('logged_in'):
        return redirect('/login')
    if session.get('first_login'):
        return redirect('/change')

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'start':
            src = request.form.get('src')
            dst = request.form.get('dst')
            vbit = request.form.get('vbit')
            abit = request.form.get('abit')
            delay = request.form.get('delaymin')
            stream_copy_mode = request.form.get('stream_copy') == 'on'

            if src and dst:
                sid = str(uuid.uuid4())[:8]
                cmd = ['ffmpeg']

                # Thêm tùy chọn tối ưu cho RTSP
                if src.startswith('rtsp://'):
                    cmd += ['-rtsp_transport', 'tcp']

                cmd += ['-re', '-i', src]

                stream_info = {'src': src, 'dst': dst, 'delaymin': delay, 'status': 'running'}

                if stream_copy_mode:
                    # **LOGIC MỚI: Chỉ copy stream, không transcode**
                    cmd += ['-c:v', 'copy', '-c:a', 'copy']
                    stream_info.update({'vbit': 'copy', 'abit': 'copy'})
                else:
                    # Logic cũ: Transcode nếu có bitrate
                    if vbit:
                        cmd += ['-c:v', 'libx264', '-b:v', vbit]
                    else:
                        cmd += ['-c:v', 'copy']
                    
                    cmd += ['-c:a', 'aac'] # Luôn transcode audio sang aac cho tương thích
                    if abit:
                        cmd += ['-b:a', abit]
                    
                    stream_info.update({'vbit': vbit or 'copy', 'abit': abit or 'default'})

                cmd += ['-f', 'flv', dst]
                
                print(f"Executing command: {' '.join(cmd)}") # In câu lệnh để debug
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                STREAMS[sid] = stream_info
                PROCESSES[sid] = proc

                if delay:
                    try:
                        mins = int(delay)
                        if mins > 0:
                            schedule_delay_stop(sid, mins)
                    except (ValueError, TypeError):
                        pass
                save_streams()

        elif action == 'stop':
            sid = request.form.get('stream_id')
            proc = PROCESSES.get(sid)
            if proc and proc.poll() is None:
                proc.terminate()
            if sid in STREAMS:
                STREAMS[sid]['status'] = 'stopped'
                save_streams()

        elif action == 'delete':
            sid = request.form.get('stream_id')
            proc = PROCESSES.get(sid)
            if proc and proc.poll() is None:
                proc.terminate()
            STREAMS.pop(sid, None)
            PROCESSES.pop(sid, None)
            save_streams()
        
        return redirect('/')

    # Cập nhật trạng thái các stream đang chạy
    for sid, proc in PROCESSES.items():
        if proc.poll() is not None and STREAMS.get(sid, {}).get('status') == 'running':
            STREAMS[sid]['status'] = 'stopped'
            save_streams()

    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    return render_template('index.html', streams=STREAMS, cpu=cpu, mem=mem)

@app.route('/healthz')
def healthz():
    return 'OK', 200

if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=PORT, debug=True)
