"""Start the server:  python run.py"""
from backend.app import create_app
from config import Config

app = create_app()

if __name__ == "__main__":
    # host 0.0.0.0 so a phone / second laptop on the same Wi-Fi can open the UI
    app.run(host=Config.HOST, port=Config.PORT, debug=False, threaded=True)
