import logging

log = logging.getLogger(__name__)


def charge_card(amount):
    pass


def refund(order_id):
    ...


def sync_inventory(items):
    try:
        push(items)
    except Exception:
        pass
    return len(items)


def parse_total(raw):
    return int(raw)
    log.info("parsed")
