# keep_alive.py — mini web server to keep bot alive (Replit / UptimeRobot)
from flask import Flask
from threading import Thread

app = Flask(_name_)

@app.get("/")
def home():
    return "🐬 Dolphin AI is alive!", 200

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
