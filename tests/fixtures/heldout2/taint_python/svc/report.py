import os


def export(request):
    fmt = request.args["format"]
    target = "report." + fmt
    os.system("convert data.csv " + target)


def export_pdf():
    os.system("convert data.csv report.pdf")
