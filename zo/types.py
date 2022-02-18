from typing import Literal, Any
from dataclasses import dataclass
from anchorpy import Program
from solana.publickey import PublicKey

Side = Literal["bid", "ask"]
OrderType = Literal[
    "limit", "ioc", "postonly", "reduceonlyioc", "reduceonlylimit", "fok"
]


@dataclass(frozen=True)
class CollateralInfo:
    mint: PublicKey
    oracle_symbol: str
    decimals: int
    weight: int
    liq_fee: int
    is_borrowable: bool
    optimal_util: int
    optimal_rate: int
    max_rate: int
    og_fee: int
    is_swappable: bool
    serum_open_orders: PublicKey
    max_deposit: int
    dust_threshold: int
    vault: PublicKey


@dataclass(frozen=True)
class MarketInfo:
    symbol: str
    oracle_symbol: str
    perp_type: Any
    base_decimals: int
    base_lot_size: int
    quote_decimals: int
    quote_lot_size: int
    strike: int
    base_imf: int
    liq_fee: int
    dex_market: PublicKey


def order_type_from_str(t: OrderType, /, *, program: Program):
    typ = program.type["OrderType"]
    match t:
        case "limit":
            return typ.Limit()
        case "ioc":
            return typ.ImmediateOrCancel()
        case "postonly":
            return typ.PostOnly()
        case "reduceonlyioc":
            return typ.ReduceOnlyIoc()
        case "reduceonlylimit":
            return typ.ReduceOnlyLimit()
        case "fok":
            return typ.FillOrKill()
        case _:
            raise TypeError(f"unsupported order type {t}")
