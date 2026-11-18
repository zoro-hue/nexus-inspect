from ..plugins import formatter


def load(name):
    return formatter.render(name)
