import logging

log = logging.getLogger(__name__)


def total(prices):
    if not prices:
        return 0
    return sum(prices)


def load_settings(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        log.exception("could not read settings from %s", path)
        raise
