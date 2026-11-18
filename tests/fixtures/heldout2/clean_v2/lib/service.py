import hashlib
import requests

BASE_URL = "https://api.example.com"


def fetch_catalog():
    return requests.get(BASE_URL + "/catalog", timeout=5).json()


def checksum(data):
    return hashlib.sha256(data).hexdigest()
