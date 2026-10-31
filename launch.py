"""One-window launcher: starts the server, opens the browser, then runs the scanner
for your newest active session.

    .venv\\Scripts\\python launch.py        (Windows)
    python launch.py                       (any OS, with the venv active)

Press Ctrl+C to stop everything.
"""
import json
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://localhost:5000"


def get_json(path, timeout=3):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return json.load(r)


def server_up():
    try:
        return get_json("/api/health").get("status") == "ok"
    except Exception:
        return False


def ready_session():
    try:
        return next((s for s in get_json("/api/sessions")
                     if s["status"] == "active" and s["has_blueprint"]), None)
    except Exception:
        return None


def main():
    (ROOT / "logs").mkdir(exist_ok=True)
    log_file = open(ROOT / "logs" / "server.log", "a", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT,
                              stdout=log_file, stderr=subprocess.STDOUT)
    try:
        print("Starting server...")
        for _ in range(40):
            if server_up():
                break
            if server.poll() is not None:
                sys.exit("Server failed to start. Details are in logs\\server.log")
            time.sleep(0.5)
        else:
            sys.exit("Server did not respond. Details are in logs\\server.log")

        print(f"Server running. Opening {URL}")
        webbrowser.open(URL)
        try:
            phone = get_json("/api/network").get("url")
            if phone:
                print(f"On your phone (same Wi-Fi): {phone}   (or scan the QR code on the page)")
        except Exception:
            pass
        print("In the browser: create a session and upload the floor plan.")
        print("Ctrl+C here stops everything.\n")

        waiting_msg = False
        while True:
            sess = ready_session()
            if sess is None:
                if not waiting_msg:
                    print("Waiting for an active session with a floor plan...")
                    waiting_msg = True
                time.sleep(2)
                continue
            waiting_msg = False
            print(f"Scanner starting for session '{sess['name']}'.")
            print("Tap a spot on the plan, wait for 3 lines here, then walk to the next spot.\n")
            subprocess.run([sys.executable, "-m", "scanner.scanner", "--mode", "checkpoint",
                            "--session", str(sess["id"])], cwd=ROOT)
            print("\nScanner stopped. Waiting for the next active session (Ctrl+C to quit)...")
            time.sleep(4)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        log_file.close()


if __name__ == "__main__":
    main()
