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
                # Cập nhật trạng thái sau khi dừng
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

# --- Worker cho việc quản lý và tự động restart stream ---
def stream_worker(sid, cmd):
    """Worker chạy ffmpeg trong một thread riêng, tự động restart nếu process chết."""
    while True:
        # Kiểm tra trước khi bắt đầu: nếu stream đã bị xóa hoặc dừng thủ công thì thoát loop
        if sid not in STREAMS or STREAMS[sid].get("status") != "running":
            print(f"[{sid}] Exiting worker thread. Reason: Stream deleted or manually stopped.")
            break

        print(f"[{sid}] Starting ffmpeg process: {' '.join(cmd)}")
        # Bắt đầu process
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PROCESSES[sid] = proc
        
        # Chờ process kết thúc
        proc.wait()  

        # Sau khi process kết thúc, kiểm tra lại trạng thái
        # Nếu người dùng đã nhấn stop/delete, PROCESSES[sid] có thể đã bị xóa
        PROCESSES.pop(sid, None) 

        # Kiểm tra xem có nên restart không
        if sid in STREAMS and STREAMS[sid].get("status") == "running":
            print(f"[{sid}] Ffmpeg process stopped unexpectedly. Restarting in 5 seconds...")
            time.sleep(5)
        else:
            # Nếu status không phải 'running' (đã bị stop/delete), thoát loop
            print(f"[{sid}] Exiting worker thread as stream is no longer marked as running.")
            break

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

                if src.startswith('rtsp://'):
                    cmd += ['-rtsp_transport', 'tcp']
                
                cmd += ['-re', '-i', src]

                # Đặt trạng thái là 'running' TRƯỚC KHI bắt đầu worker
                stream_info = {'src': src, 'dst': dst, 'delaymin': delay, 'status': 'running'}

                if stream_copy_mode:
                    cmd += ['-c:v', 'copy', '-c:a', 'copy']
                    stream_info.update({'vbit': 'copy', 'abit': 'copy'})
                else:
                    if vbit:
                        cmd += ['-c:v', 'libx264', '-b:v', vbit]
                    else:
                        cmd += ['-c:v', 'copy']
                    
                    cmd += ['-c:a', 'aac']
                    if abit:
                        cmd += ['-b:a', abit]
                    
                    stream_info.update({'vbit': vbit or 'copy', 'abit': abit or 'default'})

                cmd += ['-f', 'flv', dst]
                
                # Lưu thông tin stream và trạng thái 'running'
                STREAMS[sid] = stream_info
                save_streams()

                # Bắt đầu worker trong một luồng nền để quản lý tiến trình ffmpeg
                # Worker này sẽ tự động khởi động lại nếu tiến trình gặp lỗi
                worker_thread = threading.Thread(target=stream_worker, args=(sid, cmd), daemon=True)
                worker_thread.start()

                if delay:
                    try:
                        mins = int(delay)
                        if mins > 0:
                            schedule_delay_stop(sid, mins)
                    except (ValueError, TypeError):
                        pass

        elif action == 'stop':
            sid = request.form.get('stream_id')
            # Đặt trạng thái thành 'stopped' ĐỂ BÁO HIỆU cho worker dừng lại
            if sid in STREAMS:
                STREAMS[sid]['status'] = 'stopped'
                save_streams()
            # Dừng tiến trình hiện tại
            proc = PROCESSES.get(sid)
            if proc and proc.poll() is None:
                proc.terminate()


        elif action == 'delete':
            sid = request.form.get('stream_id')
            # Xóa khỏi STREAMS để báo hiệu cho worker dừng lại
            STREAMS.pop(sid, None)
            save_streams()
            # Dừng tiến trình hiện tại và xóa khỏi PROCESSES
            proc = PROCESSES.get(sid)
            if proc and proc.poll() is None:
                proc.terminate()
            PROCESSES.pop(sid, None)

        return redirect('/')

    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    return render_template('index.html', streams=STREAMS, cpu=cpu, mem=mem)

@app.route('/healthz')
def healthz():
    return 'OK', 200

# --- Logic khởi động ứng dụng ---

def build_cmd_for_stream(sid):
    """Xây dựng lại command list của ffmpeg từ thông tin đã lưu trong STREAMS."""
    stream = STREAMS.get(sid)
    if not stream:
        return None

    src = stream['src']
    dst = stream['dst']
    vbit = stream.get('vbit')
    abit = stream.get('abit')
    
    cmd = ['ffmpeg']
    if src.startswith('rtsp://'):
        cmd += ['-rtsp_transport', 'tcp']
    
    cmd += ['-re', '-i', src]

    if vbit == 'copy' and abit == 'copy':
        cmd += ['-c:v', 'copy', '-c:a', 'copy']
    else:
        if vbit and vbit != 'copy':
            cmd += ['-c:v', 'libx264', '-b:v', vbit]
        else:
            cmd += ['-c:v', 'copy']
        
        cmd += ['-c:a', 'aac']
        if abit and abit not in ['copy', 'default']:
            cmd += ['-b:a', abit]

    cmd += ['-f', 'flv', dst]
    return cmd

def start_existing_streams():
    """Khởi động lại các stream có trạng thái 'running' khi ứng dụng bắt đầu."""
    print("Checking for existing streams to restart...")
    # Tạo bản sao của list items để tránh lỗi khi thay đổi dict trong lúc duyệt
    for sid, stream_info in list(STREAMS.items()):
        if stream_info.get("status") == "running":
            print(f"Restarting stream {sid}...")
            cmd = build_cmd_for_stream(sid)
            if cmd:
                thread = threading.Thread(target=stream_worker, args=(sid, cmd), daemon=True)
                thread.start()
            else:
                print(f"Could not rebuild command for stream {sid}. Marking as stopped.")
                STREAMS[sid]["status"] = "stopped"
    save_streams()

if __name__ == '__main__':
    # Khởi động lại các stream đang chạy từ phiên làm việc trước
    start_existing_streams()
    
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=PORT, debug=False) # Tắt debug mode trên production
