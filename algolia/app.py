import os
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)

ALGOLIA_API_URL = "https://hn.algolia.com/api/v1"
PORT = 3333

CORS(app, origins=[f"http://localhost:{PORT}", "https://chat.openai.com"])


@app.route('/.well-known/ai-plugin.json')
def serve_manifest():
    return send_from_directory(os.path.dirname(__file__), 'static/ai-plugin.json')


@app.route('/openapi.yaml')
def serve_openapi_yaml():
    return send_from_directory(os.path.dirname(__file__), 'static/openapi.yaml')


def proxy_request(endpoint):
    url = ALGOLIA_API_URL + endpoint
    response = requests.get(url, params=request.args)

    if response.status_code != 200:
        return jsonify({"error": "Failed to fetch data from Algolia API"}), response.status_code

    return jsonify(response.json())


@app.route("/search")
def search():
    return proxy_request("/search")


@app.route("/search_by_date")
def search_by_date():
    return proxy_request("/search_by_date")


@app.route("/items/<int:item_id>")
def get_item(item_id):
    return proxy_request(f"/items/{item_id}")


@app.route("/users/<username>")
def get_user(username):
    return proxy_request(f"/users/{username}")


if __name__ == "__main__":
    app.run(port=PORT)
