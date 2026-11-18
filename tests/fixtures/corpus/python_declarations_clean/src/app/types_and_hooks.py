import typing as t
from abc import ABC, abstractmethod
from typing import Protocol, overload

if t.TYPE_CHECKING:
    from .models import Order


class Handler(Protocol):
    def handle(self, request: str) -> str: ...


class Base(ABC):
    @abstractmethod
    def load(self) -> str:
        pass

    def hook(self):
        raise NotImplementedError


class Converter:
    @overload
    def convert(self, value: int) -> int: ...

    @overload
    def convert(self, value: str) -> str: ...

    def convert(self, value):
        return value


def helper(order: "Order") -> int:
    return order.total
