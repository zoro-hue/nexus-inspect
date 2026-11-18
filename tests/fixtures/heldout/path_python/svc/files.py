import os

BASE_DIR = "/srv/reports"


def read_report(request):
    with open(os.path.join(BASE_DIR, request.args["name"])) as fh:
        return fh.read()


def read_report_safely(request):
    name = os.path.basename(request.args["name"])
    with open(os.path.join(BASE_DIR, name)) as fh:
        return fh.read()
