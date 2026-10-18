from enum import Enum


class InteractionState(str, Enum):
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    OPEN = "OPEN"

    def __str__(self) -> str:
        return str(self.value)
