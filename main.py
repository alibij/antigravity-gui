import http.server
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# ---------------------------------------------------------
# Load .env configuration
# ---------------------------------------------------------
def load_env(filepath=".env"):
    env_path = Path(__file__).parent / filepath
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

load_env()

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

AG_HOME_PATH_STR = os.environ.get("AGY_HOME", str(Path.home() / ".gemini" / "antigravity-cli"))
AG_HOME = Path(AG_HOME_PATH_STR)
BRAIN_DIR = AG_HOME / "brain"
HISTORY_FILE = AG_HOME / "history.jsonl"
TITLES_FILE = AG_HOME / "custom_titles.json"

cli_bin_env = os.environ.get("CLI_BIN", "agy")
CLI_BIN = cli_bin_env if shutil.which(cli_bin_env) else "antigravity"

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def get_cli_version():
    try:
        proc = subprocess.run([CLI_BIN, "--version"], capture_output=True, text=True, timeout=5)
        return proc.stdout.strip()
    except Exception:
        return "Unknown"

def get_usage_data():
    usage = {
        "gemini_weekly": "0", "gemini_weekly_reset": "",
        "gemini_5h": "0", "gemini_5h_reset": "",
        "claude_weekly": "0", "claude_weekly_reset": "",
        "claude_5h": "0", "claude_5h_reset": ""
    }
    
    # Try Fast RPC first
    ls_address = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
    csrf_token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    
    if ls_address and csrf_token:
        try:
            req = urllib.request.Request(
                f"http://{ls_address}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
                data=b"{}",
                headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf_token}
            )
            with urllib.request.urlopen(req, timeout=1) as response:
                res_data = json.loads(response.read().decode())
                groups = res_data.get("response", {}).get("groups", [])
                for group in groups:
                    for b in group.get("buckets", []):
                        bid = b.get("bucketId", "")
                        frac = b.get("remainingFraction", 0)
                        pct = str(int(frac * 100))
                        reset = b.get("resetTime", "")
                        
                        if bid == "gemini-weekly":
                            usage["gemini_weekly"] = pct
                            usage["gemini_weekly_reset"] = reset
                        elif bid == "gemini-5h":
                            usage["gemini_5h"] = pct
                            usage["gemini_5h_reset"] = reset
                        elif bid == "3p-weekly":
                            usage["claude_weekly"] = pct
                            usage["claude_weekly_reset"] = reset
                        elif bid == "3p-5h":
                            usage["claude_5h"] = pct
                            usage["claude_5h_reset"] = reset
                return usage
        except Exception as e:
            print("Fast RPC failed:", e)
            pass

    # Fallback to CLI
    try:
        proc = subprocess.run([CLI_BIN, "-p", "/usage"], capture_output=True, text=True, timeout=10)
        lines = proc.stdout.strip().split("\n")
        
        import re
        for line in lines:
            line = line.strip().replace("\r", "")
            if not line or "Limit Remaining" not in line:
                continue
            
            group = line.lower()
            period = line.lower()
            
            pct_match = re.search(r'(\d+)%', line)
            percent = pct_match.group(1) if pct_match else "0"
            
            date_match = re.search(r'(\d{4}-\d{2}-\d{2}.*)', line)
            reset_time = date_match.group(1).strip() if date_match else ""
            
            if "gemini" in group and "weekly" in period:
                usage["gemini_weekly"] = percent
                usage["gemini_weekly_reset"] = reset_time
            elif "gemini" in group and "five hour" in period:
                usage["gemini_5h"] = percent
                usage["gemini_5h_reset"] = reset_time
            elif ("claude" in group or "gpt" in group) and "weekly" in period:
                usage["claude_weekly"] = percent
                usage["claude_weekly_reset"] = reset_time
            elif ("claude" in group or "gpt" in group) and "five hour" in period:
                usage["claude_5h"] = percent
                usage["claude_5h_reset"] = reset_time
                    
        return usage
    except Exception:
        return usage

def get_user_info():
    import glob
    log_dir = os.path.join(AG_HOME_PATH_STR, "log")
    email = "Unknown User"
    tier = "PRO"
    
    try:
        log_files = sorted(glob.glob(os.path.join(log_dir, "cli-*.log")), reverse=True)
        for log_file in log_files:
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
                if "authenticated successfully as" in content:
                    lines = content.split("\n")
                    for line in reversed(lines):
                        if "authenticated successfully as" in line:
                            email = line.split("authenticated successfully as ")[-1].strip()
                            break
            if email != "Unknown User":
                break
    except Exception:
        pass
    
    return {"email": email, "tier": tier}

def remove_from_history(cid):
    if not HISTORY_FILE.exists():
        return
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            for line in lines:
                if f'"{cid}"' not in line:
                    f.write(line)
    except Exception:
        pass

def load_custom_titles():
    if TITLES_FILE.exists():
        try:
            with open(TITLES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_custom_title(cid, new_title):
    titles = load_custom_titles()
    titles[cid] = new_title
    TITLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TITLES_FILE, "w", encoding="utf-8") as f:
        json.dump(titles, f, ensure_ascii=False, indent=2)

def delayed_cleanup(temp_id):
    import threading, time
    def _cleanup():
        time.sleep(5)
        temp_dir = BRAIN_DIR / temp_id
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        remove_from_history(temp_id)
    threading.Thread(target=_cleanup, daemon=True).start()

def get_first_prompt(cid):
    logs_dir = BRAIN_DIR / cid / ".system_generated" / "logs"
    for filename in ["transcript_full.jsonl", "transcript.jsonl"]:
        p = logs_dir / filename
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        data = json.loads(line)
                        if data.get("type") == "USER_INPUT" or data.get("source") == "USER_EXPLICIT":
                            c = data.get("content")
                            raw_text = ""
                            if isinstance(c, str):
                                raw_text = c.strip()
                            elif isinstance(c, dict) and "text" in c:
                                raw_text = c["text"].strip()
                                
                            if raw_text:
                                lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                                if lines:
                                    first_line = lines[0]
                                    words = first_line.split()
                                    if len(words) > 6:
                                        first_line = " ".join(words[:6]) + "..."
                                    if len(first_line) > 35:
                                        first_line = first_line[:32] + "..."
                                    return first_line
                            return "چت جدید"
            except Exception:
                pass
    return None

def get_antigravity_sessions():
    custom_titles = load_custom_titles()
    sessions = {}

    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        cid = record.get("conversationId")
                        if not cid or cid.startswith("temp-"):
                            continue
                        
                        ts = record.get("timestamp", 0) / 1000.0
                        if cid not in sessions:
                            sessions[cid] = {
                                "id": cid,
                                "title": record.get("display") or "",
                                "timestamp": ts,
                            }
                        else:
                            sessions[cid]["timestamp"] = max(sessions[cid]["timestamp"], ts)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    if BRAIN_DIR.exists():
        for item in BRAIN_DIR.iterdir():
            if item.is_dir():
                cid = item.name
                mtime = item.stat().st_mtime
                if cid not in sessions:
                    sessions[cid] = {
                        "id": cid,
                        "title": "",
                        "timestamp": mtime,
                    }
                else:
                    sessions[cid]["timestamp"] = max(sessions[cid]["timestamp"], mtime)

    result = []
    for cid, info in sessions.items():
        if cid in custom_titles:
            info["title"] = custom_titles[cid]
        
        if not info["title"] or info["title"] in ["بدون عنوان", "گفت‌وگوی ذخیره‌شده"]:
            first_prompt = get_first_prompt(cid)
            info["title"] = first_prompt.strip() if first_prompt else f"گفتگوی ناشناس ({cid[:6]})"

        result.append(info)

    result.sort(key=lambda x: x["timestamp"], reverse=True)
    return result

def load_conversation_messages(cid):
    messages = []
    logs_dir = BRAIN_DIR / cid / ".system_generated" / "logs"
    log_file = logs_dir / "transcript_full.jsonl"
    if not log_file.exists():
        log_file = logs_dir / "transcript.jsonl"

    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        src = record.get("source", "")
                        stype = record.get("type", "")
                        
                        if stype not in ["USER_INPUT", "PLANNER_RESPONSE"] and src != "USER_EXPLICIT":
                            continue
                            
                        content = record.get("content")
                        created_at = record.get("created_at", "")

                        text = ""
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, dict):
                            text = content.get("text", "") or json.dumps(content, ensure_ascii=False)
                        elif isinstance(content, list):
                            texts = []
                            for item in content:
                                if isinstance(item, str):
                                    texts.append(item)
                                elif isinstance(item, dict) and "text" in item:
                                    texts.append(item["text"])
                            text = "\n".join(texts)

                        if not text:
                            continue

                        role = "user" if (stype == "USER_INPUT" or src == "USER_EXPLICIT") else "assistant"
                        messages.append({"role": role, "text": text, "created_at": created_at})
                    except Exception:
                        continue
        except Exception:
            pass

    return messages

# ---------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------
active_processes = {}

class AppHandler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        
        # Serve index.html
        if url.path in ["/", "/index.html"]:
            index_path = Path(__file__).parent / "index.html"
            if not index_path.exists():
                self.send_error(404, "index.html not found")
                return
                
            with open(index_path, "r", encoding="utf-8") as f:
                html = f.read()
                
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            version = get_cli_version()
            html = html.replace("{{CLI_VERSION}}", version)
            self.wfile.write(html.encode("utf-8"))
            
        # Serve Fonts
        elif url.path.startswith("/fonts/"):
            file_name = os.path.basename(url.path)
            if ".." in file_name or "/" in file_name:
                self.send_error(400)
                return
            file_path = Path(__file__).parent / "fonts" / file_name
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                if file_name.endswith(".ttf"):
                    self.send_header("Content-Type", "font/ttf")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
                
        # API Endpoints
        elif url.path == "/api/user":
            self._send_json(get_user_info())
            
        elif url.path == "/api/settings":
            settings_path = BRAIN_DIR.parent / "ui_settings.json"
            settings = {
                "language": "fa",
                "model": "gemini-3.1-pro",
                "effort": "medium",
                "proxy_enabled": False,
                "proxy_address": "socks5://127.0.0.1:10808"
            }
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8") as f:
                        settings.update(json.load(f))
                except Exception:
                    pass
            self._send_json(settings)

        elif url.path == "/api/usage":
            self._send_json(get_usage_data())
            
        elif url.path == "/api/sessions":
            self._send_json(get_antigravity_sessions())
            
        elif url.path.startswith("/api/sessions/"):
            cid = url.path.split("/")[-1]
            titles = load_custom_titles()
            title = titles.get(cid) or get_first_prompt(cid) or f"Session {cid[:8]}"
            msgs = load_conversation_messages(cid)
            self._send_json({"id": cid, "title": title, "messages": msgs})
            
        else:
            self.send_error(404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        if url.path.endswith("/delete"):
            cid = url.path.split("/")[-2]
            target_dir = BRAIN_DIR / cid
            if target_dir.exists():
                shutil.rmtree(target_dir)
            remove_from_history(cid)
            titles = load_custom_titles()
            if cid in titles:
                del titles[cid]
                with open(TITLES_FILE, "w", encoding="utf-8") as f:
                    json.dump(titles, f, ensure_ascii=False, indent=2)
            self._send_json({"success": True})
            return

        if url.path.endswith("/suggest_title"):
            cid = url.path.split("/")[-2]
            msgs = load_conversation_messages(cid)
            prompt = "با توجه به پیام‌های زیر، یک عنوان بسیار کوتاه و جذاب (نهایتا ۳ تا ۴ کلمه) برای این چت پیشنهاد بده. فقط و فقط خود عنوان را بنویس و هیچ علامت، گیومه یا توضیح اضافه‌ای نده:\n\n"
            for m in msgs[-10:]:
                prompt += f"{m['role']}: {m['text'][:150]}\n"
            
            temp_id = "temp-" + str(uuid.uuid4())
            proc = subprocess.run([CLI_BIN, "--dangerously-skip-permissions", "--model", "gemini-3.8-flash-low", "--conversation", temp_id, "-p", prompt], capture_output=True, text=True)
            suggested = proc.stdout.strip().replace('"', '').replace('\"', '')
            
            delayed_cleanup(temp_id)
            self._send_json({"title": suggested})
            return

        if url.path == "/api/stop":
            session_id = body.get("session_id")
            if session_id in active_processes:
                try:
                    active_processes[session_id].terminate()
                except Exception:
                    pass
            self._send_json({"success": True})
            return

        if url.path.endswith("/rename"):
            cid = url.path.split("/")[-2]
            new_title = body.get("title", "").strip()
            if new_title:
                save_custom_title(cid, new_title)
                self._send_json({"success": True})
            else:
                self._send_json({"error": "عنوان نامعتبر است"}, 400)

        elif url.path == "/api/settings":
            settings_path = BRAIN_DIR.parent / "ui_settings.json"
            settings = {
                "language": "fa",
                "model": "gemini-3.1-pro",
                "effort": "medium",
                "proxy_enabled": False,
                "proxy_address": "socks5://127.0.0.1:10808"
            }
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8") as f:
                        settings.update(json.load(f))
                except Exception:
                    pass
            
            if "language" in body: settings["language"] = body["language"]
            if "model" in body: settings["model"] = body["model"]
            if "effort" in body: settings["effort"] = body["effort"]
            if "proxy_enabled" in body: settings["proxy_enabled"] = bool(body["proxy_enabled"])
            if "proxy_address" in body: settings["proxy_address"] = body["proxy_address"]
            
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            
            self._send_json({"success": True, "settings": settings})

        elif url.path == "/api/proxy_test":
            proxy = body.get("proxy", "").strip()
            if not proxy:
                self._send_json({"status": "red", "detail": "آدرس پروکسی وارد نشده است."})
                return
            
            proc1 = subprocess.run(["curl", "-s", "-x", proxy, "-m", "5", "http://gstatic.com/generate_204"], capture_output=True)
            if proc1.returncode != 0:
                self._send_json({"status": "red", "detail": "ارتباط با پروکسی برقرار نشد. (Time out / Refused)"})
                return
            
            proc2 = subprocess.run(["curl", "-s", "-I", "-x", proxy, "-m", "7", "https://server.codeium.com"], capture_output=True, text=True)
            out = proc2.stdout + proc2.stderr
            if "403" in out or "Forbidden" in out or "error code: 1020" in out.lower():
                self._send_json({"status": "yellow", "detail": "پروکسی وصل است اما توسط سرورهای هوش‌مصنوعی بلاک شده (Error 403)."})
            elif proc2.returncode == 0:
                self._send_json({"status": "green", "detail": "پروکسی و سرورهای آنتی‌گرویتی کاملاً در دسترس هستند."})
            else:
                self._send_json({"status": "yellow", "detail": "پروکسی وصل است اما ارتباط با سرورهای هوش‌مصنوعی تایم‌اوت شد."})

        elif url.path == "/api/log":
            err = body.get("error", "")
            print(f"\n\n=== JS ERROR LOG ===\n{err}\n====================\n\n")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        elif url.path == "/api/chat":
            prompt = body.get("prompt", "").strip()
            session_id = body.get("session_id")
            model = body.get("model")
            effort = body.get("effort")
            proxy = body.get("proxy")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()

            is_new_session = not session_id or not (BRAIN_DIR / session_id).exists()
            existing_dirs = set(p.name for p in BRAIN_DIR.iterdir() if p.is_dir()) if BRAIN_DIR.exists() else set()

            cmd = [CLI_BIN, "--dangerously-skip-permissions"]
            if not is_new_session:
                cmd += ["--conversation", session_id]
            if model and model != "inherit":
                cmd += ["--model", model]
            if effort:
                cmd += ["--effort", effort]
            cmd += ["-p", prompt]

            env = os.environ.copy()
            env["FORCE_COLOR"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            if proxy:
                env["HTTP_PROXY"] = proxy
                env["HTTPS_PROXY"] = proxy
                env["ALL_PROXY"] = proxy
            
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    env=env
                )

                real_session_id = session_id
                if is_new_session:
                    import time
                    for _ in range(60):
                        if BRAIN_DIR.exists():
                            new_dirs = set(p.name for p in BRAIN_DIR.iterdir() if p.is_dir()) - existing_dirs
                            if new_dirs:
                                real_session_id = next(iter(new_dirs))
                                break
                        time.sleep(0.05)

                if real_session_id:
                    active_processes[real_session_id] = proc
                    meta = json.dumps({"session_id": real_session_id}) + "\n"
                    self.wfile.write(meta.encode("utf-8"))
                    self.wfile.flush()

                # استریم مستقیم بایت‌های خروجی روی ویندوز
                while True:
                    chunk = proc.stdout.read(1024)
                    if not chunk and proc.poll() is not None:
                        break
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()

                proc.wait()
            except Exception as e:
                self.wfile.write(f"\nError: {str(e)}".encode("utf-8"))
                self.wfile.flush()

# ---------------------------------------------------------
# Server Boot
# ---------------------------------------------------------
if __name__ == "__main__":
    print(f"CLI Detected: {CLI_BIN}")
    print(f"Reading sessions from: {AG_HOME}")
    print(f"Open in browser: http://{HOST}:{PORT}")
    server = http.server.ThreadingHTTPServer((HOST, PORT), AppHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")