import requests

API_BASE = "https://api.example.com"


def fetch_remote(request):
    target = request.args["url"]
    return requests.get(target, timeout=5).text


def fetch_catalog():
    return requests.get(API_BASE + "/catalog", timeout=5).json()
