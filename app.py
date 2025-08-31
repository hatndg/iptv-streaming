from flask import Flask, render_template, request, redirect, session
import subprocess
import os
import hashlib
import psutil
import uuid
import json
import threading
import time

# --- Cấu hình và Khởi tạo ---
app = Flask(__name__)
# Luôn sử dụng biến môi trường cho secret key trên server, có giá trị mặc định để dễ test local
app.secret_key = os.environ.get("SECRET_KEY", "a_very_secret_key_for_flask") 

# Tên các file cấu hình
PASS_FILE = "password.txt"
STREAMS_FILE = "streams.json"

# Mật khẩu mặc định nếu file chưa tồn tại
DEFAULT_PASS = "Admin@123"

# Các biến global để lưu trạng thái
STREAMS = {}    # Lưu thông tin cấu hình và trạng thái các stream (persisted to file)
PROCESSES = {}  # Lưu các đối tượng process đang chạy (in-memory only)

# --- Các hàm tiện ích ---
def hash_pass(p):
    """Băm mật khẩu sử dụng SHA256."""
    return hashlib.sha256(p.encode()).hexdigest()

def save_streams():
    """Lưu thông tin các stream vào file JSON."""
    with open(STREAMS_FILE, "w") as f:
        json.dump(STREAMS, f, indent=4) # indent=4 cho dễ đọc

def build_ffmpeg_cmd(stream_info):
    """Xây dựng mảng câu lệnh ffmpeg từ thông tin stream."""
    src = stream_info['src']
    dst = stream_info['dst']
    vbit = stream_info.get('vbit')
    abit = stream_info.get('abit')
    
    cmd = ['ffmpeg']

    # Tối ưu cho RTSP để tăng độ ổn định
    if src.startswith('rtsp://'):
        cmd += ['-rtsp_transport', 'tcp']
    
    cmd += ['-re', '-i', src]

    # Kiểm tra xem có phải copy mode không
    if vbit == 'copy' and abit == 'copy':
        cmd += ['-c:v', 'copy', '-c:a', 'copy']
    else:
        # Logic transcode (tương tự như cũ)
        if vbit and vbit != 'copy':
            cmd += ['-c:v', 'libx264', '-b:v', vbit]
        else:
            cmd += ['-c:v', 'copy']
        
        cmd += ['-c:a', 'aac'] # Luôn encode audio sang aac để tương thích tốt hơn
        if abit and abit != 'default':
            cmd += ['-b:a', abit]

    cmd += ['-f', 'flv', dst]
    return cmd

# --- Logic khởi tạo ứng dụng ---
def initialize_app():
    """Khởi tạo file mật khẩu và tải danh sách stream khi ứng dụng bắt đầu."""
    # 1. Tạo file mật khẩu nếu chưa có
    if not os.path.exists(PASS_FILE):
        with open(PASS_FILE, "w") as f:
            f.write(hash_pass(DEFAULT_PASS))

    # 2. Tải danh sách stream từ file
    if os.path.exists(STREAMS_FILE):
        try:
            with open(STREAMS_FILE, "r") as f:
                # Gán trực tiếp cho biến global
                global STREAMS
                STREAMS = json.load(f)
        except json.JSONDecodeError:
            print(f"Cảnh báo: File {STREAMS_FILE} bị lỗi hoặc trống. Bắt đầu với danh sách rỗng.")
            STREAMS = {}

# --- Các hàm chạy nền (Background Workers) ---

def schedule_delay_stop(sid, delay_minutes):
    """Lên lịch dừng một stream sau một khoảng thời gian."""
    def worker():
        try:
            time.sleep(delay_minutes * 60)
            proc = PROCESSES.get(sid)
            # Chỉ dừng nếu stream vẫn còn tồn tại và đang chạy
            if proc and proc.poll() is None:
                proc.terminate()
                print(f"Delayed stop for stream {sid} executed.")
                if sid in STREAMS:
                    STREAMS[sid]["status"] = "stopped"
                    save_streams()
        except Exception as e:
            print(f"Lỗi trong worker delay stop cho {sid}: {e}")

    threading.Thread(target=worker, daemon=True).start()

def stream_worker(sid):
    """
    Worker chính cho mỗi stream, có khả năng tự khởi động lại.
    Worker này sẽ tự kết thúc khi stream bị xóa hoặc stop thủ công.
    """
    while True:
        # **Kiểm tra điều kiện thoát**: Nếu stream không còn trong danh sách
        # hoặc trạng thái không phải là 'running' -> thoát vòng lặp.
        # Đây là cách để dừng worker từ bên ngoài (qua nút Stop/Delete).
        if sid not in STREAMS or STREAMS[sid].get("status") != "running":
            print(f"[{sid}] Worker is stopping because stream was removed or stopped.")
            break

        # Xây dựng câu lệnh từ thông tin đã lưu
        cmd = build_ffmpeg_cmd(STREAMS[sid])
        print(f"[{sid}] Starting ffmpeg process: {' '.join(cmd)}")
        
        # Chạy process và lưu vào biến global để có thể tương tác (vd: stop)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PROCESSES[sid] = proc
        proc.wait()  # Chờ cho đến khi process kết thúc

        # Sau khi process kết thúc, kiểm tra lại trạng thái
        if STREAMS.get(sid, {}).get("status") == "running":
            print(f"[{sid}] FFMPEG process stopped unexpectedly. Restarting in 5 seconds...")
            time.sleep(5)
        else:
            # Nếu trạng thái đã là 'stopped' hoặc stream bị xóa, thì thoát hẳn
            print(f"[{sid}] FFMPEG process stopped as intended.")
            break
            
# --- Các route xác thực ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect('/')
    msg = ''
    if request.method == 'POST':
        pw = request.form.get('password')
        with open(PASS_FILE) as f:
            saved = f.read().strip()
        if hash_pass(pw) == saved:
            session['logged_in'] = True
            # Kiểm tra xem có phải lần đầu đăng nhập với pass mặc định không
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
        if not new or len(new) < 6:
            msg = "Mật khẩu mới phải có ít nhất 6 ký tự!"
        else:
            with open(PASS_FILE, 'w') as f:
                f.write(hash_pass(new))
            session['first_login'] = False # Sau khi đổi pass thì không còn là first_login nữa
            msg = "Đổi mật khẩu thành công! Bạn sẽ được chuyển hướng về trang chủ."
            return render_template("change.html", msg=msg, success=True)
    return render_template("change.html", msg=msg)


# --- Route chính của ứng dụng ---
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
                
                stream_info = {
                    'src': src, 
                    'dst': dst, 
                    'delaymin': delay,
                    'status': 'running' # Trạng thái ban đầu là running
                }

                if stream_copy_mode:
                    stream_info.update({'vbit': 'copy', 'abit': 'copy'})
                else:
                    stream_info.update({'vbit': vbit or 'copy', 'abit': abit or 'default'})
                
                STREAMS[sid] = stream_info
                save_streams()

                # **LOGIC MỚI**: Khởi chạy stream trong một luồng riêng
                threading.Thread(target=stream_worker, args=(sid,), daemon=True).start()

                if delay:
                    try:
                        mins = int(delay)
                        if mins > 0:
                            schedule_delay_stop(sid, mins)
                    except (ValueError, TypeError):
                        pass

        elif action == 'stop':
            sid = request.form.get('stream_id')
            if sid in STREAMS:
                # **LOGIC MỚI**: Thay đổi trạng thái để worker tự thoát
                STREAMS[sid]['status'] = 'stopped'
                proc = PROCESSES.get(sid)
                if proc and proc.poll() is None:
                    proc.terminate() # Gửi tín hiệu dừng process
                save_streams()

        elif action == 'delete':
            sid = request.form.get('stream_id')
            if sid in STREAMS:
                # **LOGIC MỚI**: Xóa stream khỏi STREAMS để worker tự thoát
                STREAMS.pop(sid, None)
                proc = PROCESSES.get(sid)
                if proc and proc.poll() is None:
                    proc.terminate()
                PROCESSES.pop(sid, None)
                save_streams()
        
        return redirect('/')

    # Cập nhật trạng thái các stream trên giao diện
    # Dựa vào việc process có đang chạy hay không
    for sid, stream_data in STREAMS.items():
        proc = PROCESSES.get(sid)
        is_running = proc and proc.poll() is None
        
        # Nếu process đã chết nhưng status vẫn là 'running', cập nhật lại
        if not is_running and stream_data.get('status') == 'running':
            # Worker sẽ tự khởi động lại, không cần cập nhật file ở đây
            # Giao diện sẽ hiển thị "stopped" tạm thời
            pass

    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    return render_template('index.html', streams=STREAMS, processes=PROCESSES, cpu=cpu, mem=mem)


@app.route('/healthz')
def healthz():
    """Endpoint cho health check của các nền tảng hosting."""
    return 'OK', 200

def restart_running_streams():
    """
    Quét qua các stream đã lưu và khởi động lại những stream có status là 'running'.
    Hàm này được gọi một lần duy nhất khi ứng dụng khởi động.
    """
    print("Checking for streams to restart...")
    # Tạo một bản copy của keys để tránh lỗi khi duyệt dict
    for sid, stream_info in list(STREAMS.items()):
        if stream_info.get("status") == "running":
            print(f"Restarting stream {sid} ({stream_info['src']})")
            # Khởi chạy worker cho stream này trong một luồng riêng
            threading.Thread(target=stream_worker, args=(sid,), daemon=True).start()


if __name__ == '__main__':
    initialize_app()
    restart_running_streams()
    # Lấy port từ biến môi trường, mặc định là 10000 cho Render
    PORT = int(os.environ.get("PORT", 10000))
    # Chạy trên 0.0.0.0 để có thể truy cập từ bên ngoài container/máy ảo
    app.run(host='0.0.0.0', port=PORT, debug=False) # Chuyển debug=False khi deploy
