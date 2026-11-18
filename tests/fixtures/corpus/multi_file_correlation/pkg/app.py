from .models import Order


def handle(request):
    return Order(request)
