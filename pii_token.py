import dataclasses

from enum import Enum, auto


class TokenType(Enum):
    EMAIL = auto()
    PHONE = auto()
    PHONE_RU_EXCEPTION = auto()
    REST = auto()


@dataclasses.dataclass
class Token:
    type: TokenType
    start: int
    end: int

    @property
    def length(self):
        return self.end - self.start
