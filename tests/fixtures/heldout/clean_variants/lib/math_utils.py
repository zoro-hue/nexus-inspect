import json
import subprocess


def total(values):
    return sum(values)


def describe(payload):
    return json.dumps({"total": total(payload)})


def git_head(repo_path):
    result = subprocess.run(["git", "-C", repo_path, "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return result.stdout.strip()
