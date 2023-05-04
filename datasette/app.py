import re
import os
import sys
import signal
import requests
import threading
import subprocess
from flask import Flask, send_from_directory, request, Response
from flask_cors import CORS

app = Flask(__name__)

PROXY_URL = ""
PORT = 8000

CORS(app, origins=[f"http://localhost:{PORT}", "https://chat.openai.com"])


@app.route("/.well-known/ai-plugin.json")
def serve_ai_plugin_json():
    return send_from_directory("static", "ai-plugin.json")


@app.route("/openapi.yaml")
def serve_openapi_yaml():
    return send_from_directory("static", "openapi.yaml")


@app.route("/yc.jpg")
def serve_yc_logo():
    return send_from_directory("static", "yc.jpg")


# Proxies to datasette
@app.route("/api")
def proxy():
    query_params = request.query_string.decode("utf-8")
    url = f"{PROXY_URL}?{query_params}"

    response = requests.get(
        url, headers=request.headers, cookies=request.cookies, allow_redirects=False
    )
    headers = response.headers.items()
    headers = [header for header in headers if header[0].lower() != "transfer-encoding"]
    return Response(response.content, response.status_code, headers=headers)


def print_output(process):
    for line in process.stdout:
        line = line.decode().strip()
        print(line)


def start_datasette(database_path):
    cmd = f"datasette serve -i {database_path} --setting sql_time_limit_ms 10000"
    process = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )

    port = None
    for line in process.stdout:
        line = line.decode().strip()
        print(line)
        match = re.search(r"Uvicorn running on http://127.0.0.1:(\d+)", line)
        if match:
            port = int(match.group(1))
            break

    if port is not None:
        global PROXY_URL
        database_name = os.path.splitext(os.path.basename(database_path))[0]
        PROXY_URL = f"http://127.0.0.1:{port}/{database_name}.json"
        output_thread = threading.Thread(target=print_output, args=(process,))
        output_thread.daemon = True
        output_thread.start()

    return process


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python app.py <path_to_sqlite_database>")
        sys.exit(1)

    database_path = sys.argv[1]
    datasette_process = start_datasette(database_path)

    signal.signal(
        signal.SIGINT,
        lambda _: (
            print("Terminating Datasette process..."),
            datasette_process.terminate(),
            sys.exit(0),
        ),
    )
    try:
        app.run(port=PORT)
    finally:
        datasette_process.terminate()
