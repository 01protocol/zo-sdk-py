import base64
import enum
import struct
from typing import NamedTuple, NewType, Any
from solana.publickey import PublicKey


def decode(cls, fmt: str, b: bytes) -> list[Any]:
    if len(b) < 12 or b[:5] != b"serum" or b[-7:] != b"padding":
        raise ValueError("invalid buffer for dex struct")
    fs = list(struct.unpack(fmt, b[5:-7]))

    for i, f in enumerate(cls._fields):
        if cls.__annotations__[f] == AccountFlag:
            fs[i] = AccountFlag(fs[i])
        elif cls.__annotations__[f] == PublicKey:
            fs[i] = PublicKey(fs[i])
        elif cls.__annotations__[f] == i128:
            fs[i] = i128(int.from_bytes(fs[i], "little", signed=True))

    return fs


i128 = NewType("i128", int)


class AccountFlag(enum.IntFlag):
    INITIALIZED = 1 << 0
    MARKET = 1 << 1
    OPEN_ORDERS = 1 << 2
    REQUEST_QUEUE = 1 << 3
    EVENT_QUEUE = 1 << 4
    BIDS = 1 << 5
    ASKS = 1 << 6
    DISABLED = 1 << 7
    CLOSED = 1 << 8
    PERMISSIONED = 1 << 9


class Market(NamedTuple):
    account_flags: AccountFlag
    own_address: PublicKey
    quote_fees_accrued: int
    req_q: PublicKey
    event_q: PublicKey
    bids: PublicKey
    asks: PublicKey
    base_lot_size: int
    quote_lot_size: int
    fee_rate_bps: int
    referrer_rebates_accrued: int
    funding_index: i128
    last_updated: int
    strike: int
    perp_type: int
    base_decimals: int
    open_interest: int
    open_orders_authority: PublicKey
    prune_authority: PublicKey

    @classmethod
    def from_bytes(cls, b: bytes):
        r = cls._make(decode(cls, "<Q32sQ32s32s32s32s4Q16s5Q32s32s1032s", b)[:-1])
        if (
            r.account_flags
            != AccountFlag.INITIALIZED | AccountFlag.MARKET | AccountFlag.PERMISSIONED
        ):
            raise ValueError("invalid account_flags for market")
        return r

    @classmethod
    def from_base64(cls, b: str):
        return cls.from_bytes(base64.b64decode(b))
