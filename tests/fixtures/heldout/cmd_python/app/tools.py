import subprocess
import os


def convert_upload(name):
    subprocess.run(f"convert uploads/{name} out.png", shell=True, check=True)


def convert_safely(name):
    subprocess.run(["convert", f"uploads/{name}", "out.png"], check=True)


def list_root():
    os.system("ls -la /srv/app")
