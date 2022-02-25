from typing import *
import asyncio
import os
import json
from anchorpy import Idl, Program, Provider, Context, Wallet
from anchorpy.error import AccountDoesNotExistError
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.rpc.commitment import Processed, Finalized
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.sysvar import SYSVAR_RENT_PUBKEY
from solana.system_program import SYS_PROGRAM_ID
from spl.token.instructions import get_associated_token_address
from spl.token.constants import TOKEN_PROGRAM_ID

from . import util, types, config
from .config import configs, Config
from .types import Side, OrderType, CollateralInfo, MarketInfo, PositionInfo
from .dex import Market, Order

T = TypeVar("T")


class ZoIndexer(Generic[T]):
    def __init__(self, d: dict[str, T], m: Callable[[str | int | PublicKey], str]):
        self.d = d
        self.m = m

    def __repr__(self):
        return self.d.__repr__()

    def __iter__(self):
        return self.d.items().__iter__()

    def __len__(self):
        return len(self.d)

    def __getitem__(self, i: str | int | PublicKey) -> T:
        return self.d[self.m(i)]


class Zo:
    __program: Program
    __config: Config

    __markets: dict[str, MarketInfo]
    __collaterals: dict[str, CollateralInfo]
    __balance: dict[str, float]
    __position: dict[str, float]

    __dex_markets: dict[str, Market]
    __orders: dict[str, list[Order]]

    __markets_map: dict[str | int, str]
    __collaterals_map: dict[str | int, str]

    _zo_state: Any
    _zo_state_signer: PublicKey
    _zo_cache: Any
    _zo_margin: Any
    _zo_margin_key: None | PublicKey
    _zo_control: Any

    def __init__(
        self,
        *,
        _program,
        _config,
        _state,
        _state_signer,
        _margin,
        _margin_key,
    ):
        self.__program = _program
        self.__config = _config
        self._zo_state = _state
        self._zo_state_signer = _state_signer
        self._zo_margin = _margin
        self._zo_margin_key = _margin_key

    @classmethod
    async def new(
        cls,
        *,
        payer: Keypair | None = None,
        cluster: Literal["devnet", "mainnet"],
        url: str | None = None,
        load_margin: bool = True,
        create_margin: bool = True,
        tx_opts: TxOpts = TxOpts(
            max_retries=None,
            preflight_commitment=Finalized,
            skip_confirmation=False,
            skip_preflight=False,
        ),
    ):
        if cluster not in configs.keys():
            raise TypeError(f"`cluster` must be one of: {configs.keys()}")

        config = configs[cluster]

        if url is None:
            url = config.CLUSTER_URL

        idl_path = os.path.join(os.path.dirname(__file__), "idl.json")
        with open(idl_path) as f:
            raw_idl = json.load(f)

        idl = Idl.from_json(raw_idl)
        conn = AsyncClient(url)
        wallet = Wallet(payer) if payer is not None else Wallet.local()
        provider = Provider(conn, wallet, opts=tx_opts)
        program = Program(idl, config.ZO_PROGRAM_ID, provider=provider)

        state = await program.account["State"].fetch(config.ZO_STATE_ID)
        state_signer, state_signer_nonce = util.state_signer_pda(
            state=config.ZO_STATE_ID, program_id=config.ZO_PROGRAM_ID
        )

        if state.signer_nonce != state_signer_nonce:
            raise ValueError(
                f"Invalid state key ({config.ZO_STATE_ID}) for program id ({config.ZO_PROGRAM_ID})"
            )

        margin = None
        margin_key = None

        if load_margin:
            margin_key, nonce = util.margin_pda(
                owner=wallet.public_key,
                state=config.ZO_STATE_ID,
                program_id=config.ZO_PROGRAM_ID,
            )
            try:
                margin = await program.account["Margin"].fetch(margin_key)
            except AccountDoesNotExistError as e:
                if not create_margin:
                    raise e

                await util.create_margin(
                    program=program,
                    state=config.ZO_STATE_ID,
                    key=margin_key,
                    nonce=nonce,
                )
                margin = await program.account["Margin"].fetch(margin_key)

        zo = cls(
            _config=config,
            _program=program,
            _state=state,
            _state_signer=state_signer,
            _margin=margin,
            _margin_key=margin_key,
        )
        await zo.refresh()
        return zo

    @property
    def program(self) -> Program:
        return self.__program

    @property
    def provider(self) -> Provider:
        return self.program.provider

    @property
    def connection(self) -> AsyncClient:
        return self.provider.connection

    @property
    def wallet(self) -> Wallet:
        return self.provider.wallet

    def _collaterals_map(self, k: str | int | PublicKey) -> str:
        if isinstance(k, PublicKey):
            for i, c in enumerate(self._zo_state.collaterals):
                if c.mint == k:
                    return self.__collaterals_map[i]
            raise ValueError("")
        else:
            return self.__collaterals_map[k]

    def _markets_map(self, k: str | int | PublicKey) -> str:
        if isinstance(k, PublicKey):
            for i, m in enumerate(self._zo_state.perp_markets):
                if m.dex_market == k:
                    return self.__markets_map[i]
            raise ValueError("")
        else:
            return self.__markets_map[k]

    @property
    def collaterals(self):
        return ZoIndexer(self.__collaterals, lambda k: self._collaterals_map(k))

    @property
    def markets(self):
        return ZoIndexer(self.__markets, lambda k: self._markets_map(k))

    @property
    def balance(self):
        return ZoIndexer(self.__balance, lambda k: self._collaterals_map(k))

    @property
    def position(self):
        return ZoIndexer(self.__position, lambda k: self._markets_map(k))

    @property
    def orders(self):
        return ZoIndexer(self.__orders, lambda k: self._markets_map(k))

    def _get_open_orders_info(self, key: int | str, /):
        if isinstance(key, str):
            for k, v in self.__markets_map.items():
                if v == key and isinstance(k, int):
                    key = k
                    break
            else:
                ValueError("")
        o = self._zo_control.open_orders_agg[key]
        return o if o.key != PublicKey(0) else None

    def __reload_collaterals(self):
        map = {}
        collaterals = {}

        for i, c in enumerate(self._zo_state.collaterals):
            if c.mint == PublicKey(0):
                break

            symbol = util.decode_symbol(c.oracle_symbol)
            map[symbol] = symbol
            map[i] = symbol

            collaterals[symbol] = CollateralInfo(
                mint=c.mint,
                oracle_symbol=symbol,
                decimals=c.decimals,
                weight=c.weight,
                liq_fee=c.liq_fee,
                is_borrowable=c.is_borrowable,
                optimal_util=c.optimal_util,
                optimal_rate=c.optimal_rate,
                max_rate=c.max_rate,
                og_fee=c.og_fee,
                is_swappable=c.is_swappable,
                serum_open_orders=c.serum_open_orders,
                max_deposit=c.max_deposit,
                dust_threshold=c.dust_threshold,
                vault=self._zo_state.vaults[i],
            )

        self.__collaterals_map = map
        self.__collaterals = collaterals

    def __reload_markets(self):
        map = {}
        markets = {}

        for i, m in enumerate(self._zo_state.perp_markets):
            if m.dex_market == PublicKey(0):
                break

            symbol = util.decode_symbol(m.symbol)
            map[symbol] = symbol
            map[i] = symbol

            markets[symbol] = MarketInfo(
                address=m.dex_market,
                symbol=symbol,
                oracle_symbol=util.decode_symbol(m.oracle_symbol),
                perp_type=types.perp_type_to_str(m.perp_type, program=self.program),
                base_decimals=m.asset_decimals,
                base_lot_size=m.asset_lot_size,
                quote_decimals=6,
                quote_lot_size=m.quote_lot_size,
                strike=m.strike,
                base_imf=m.base_imf,
                liq_fee=m.liq_fee,
            )

        self.__markets_map = map
        self.__markets = markets

    def __reload_balances(self):
        if self._zo_margin is None:
            return

        balances = {}
        for i, c in enumerate(self._zo_margin.collateral):
            if i not in self.__collaterals_map:
                break

            decimals = self.collaterals[i].decimals
            c = util.decode_wrapped_i80f48(c)
            m = self._zo_cache.borrow_cache[i]
            m = m.supply_multiplier if c >= 0 else m.borrow_multiplier
            m = util.decode_wrapped_i80f48(m)

            balances[self.__collaterals_map[i]] = util.small_to_big_amount(
                c * m, decimals=decimals
            )

        self.__balance = balances

    def __reload_positions(self):
        if self._zo_margin is None:
            return

        positions = {}
        for s, m in self.markets:
            if (oo := self._get_open_orders_info(s)) is not None:
                positions[s] = PositionInfo(
                    size=util.small_to_big_amount(
                        oo.pos_size, decimals=m.base_decimals
                    ),
                    value=util.small_to_big_amount(
                        oo.native_pc_total, decimals=m.quote_decimals
                    ),
                    realized_pnl=util.small_to_big_amount(
                        oo.realized_pnl, decimals=m.base_decimals
                    ),
                    funding_index=util.small_to_big_amount(
                        oo.funding_index, decimals=m.quote_decimals
                    ),
                    pos="long" if oo.pos_size >= 0 else "short",
                )
            else:
                positions[s] = PositionInfo(
                    size=0, value=0, realized_pnl=0, funding_index=1, pos="long"
                )

        self.__position = positions
        pass

    async def __reload_dex_markets(self):
        ks = [
            m.dex_market
            for m in self._zo_state.perp_markets
            if m.dex_market != PublicKey(0)
        ]
        res: Any = await self.connection.get_multiple_accounts(
            ks, encoding="base64", commitment=Processed
        )
        res = res["result"]["value"]
        self.__dex_markets = {
            self.__markets_map[i]: Market.from_base64(res[i]["data"][0])
            for i in range(len(self.__markets))
        }

    async def __reload_orders(self):
        if self._zo_margin is None:
            return

        ks = []
        for i in range(len(self.__markets)):
            mkt = self.__dex_markets[self.__markets_map[i]]
            ks.extend((mkt.bids, mkt.asks))

        res: Any = await self.connection.get_multiple_accounts(
            ks, encoding="base64", commitment=Processed
        )
        res = res["result"]["value"]
        orders = {}

        for i in range(len(self.__markets)):
            mkt = self.__dex_markets[self.__markets_map[i]]
            ob = mkt._decode_orderbook_from_base64(
                res[2 * i]["data"][0], res[2 * i + 1]["data"][0]
            )
            os = []
            for slab in [ob.bids, ob.asks]:
                for o in slab:
                    if o.control == self._zo_margin.control:
                        os.append(o)
            orders[self.__markets_map[i]] = os

        self.__orders = orders

    async def __refresh_margin(self):
        if self._zo_margin_key is not None:
            self._zo_margin, self._zo_control = await asyncio.gather(
                self.program.account["Margin"].fetch(self._zo_margin_key),
                self.program.account["Control"].fetch(self._zo_margin.control),
            )

    async def refresh(self):
        self._zo_state, self._zo_cache, _ = await asyncio.gather(
            self.program.account["State"].fetch(self.__config.ZO_STATE_ID),
            self.program.account["Cache"].fetch(self._zo_state.cache),
            self.__refresh_margin(),
        )

        self.__reload_collaterals()
        self.__reload_markets()
        self.__reload_balances()
        self.__reload_positions()
        await self.__reload_dex_markets()
        await self.__reload_orders()

    async def deposit(
        self,
        amount: float,
        /,
        *,
        mint: PublicKey,
        repay_only=False,
        token_account: None | PublicKey = None,
    ):
        """Deposit collateral into the margin account.

        :param size: The amount to deposit, in big units (e.g.: 1.5 SOL, 0.5 BTC).
        :param mint: The mint of the collateral.
        :repay_only: If true, will only deposit up to the amount borrowed.
        :param token_account: The token account to deposit from, defaulting to
            the associated token account.
        """
        if token_account is None:
            token_account = get_associated_token_address(self.wallet.public_key, mint)

        decimals = self.collaterals[mint].decimals
        amount = util.big_to_small_amount(amount, decimals=decimals)

        return await self.program.rpc["deposit"](
            repay_only,
            amount,
            ctx=Context(
                accounts={
                    "state": self.__config.ZO_STATE_ID,
                    "state_signer": self._zo_state_signer,
                    "cache": self._zo_state.cache,
                    "authority": self.wallet.public_key,
                    "margin": self._zo_margin_key,
                    "token_account": token_account,
                    "vault": self.collaterals[mint].vault,
                    "token_program": TOKEN_PROGRAM_ID,
                }
            ),
        )

    async def withdraw(
        self,
        amount: float,
        /,
        *,
        mint: PublicKey,
        allow_borrow=False,
        token_account: None | PublicKey = None,
    ) -> str:
        if token_account is None:
            token_account = get_associated_token_address(self.wallet.public_key, mint)

        decimals = self.collaterals[mint].decimals
        amount = util.big_to_small_amount(amount, decimals=decimals)

        return await self.program.rpc["withdraw"](
            allow_borrow,
            amount,
            ctx=Context(
                accounts={
                    "state": self.__config.ZO_STATE_ID,
                    "state_signer": self._zo_state_signer,
                    "cache": self._zo_state.cache,
                    "authority": self.wallet.public_key,
                    "margin": self._zo_margin_key,
                    "control": self._zo_margin.control,
                    "token_account": token_account,
                    "vault": self.collaterals[mint].vault,
                    "token_program": TOKEN_PROGRAM_ID,
                }
            ),
        )

    async def place_order(
        self,
        size: float,
        price: float,
        side: Side,
        *,
        symbol: str,
        order_type: OrderType,
        limit: int = 10,
        client_id: int = 0,
    ):
        mkt = self.__dex_markets[symbol]
        info = self.markets[symbol]
        is_long = side == "bid"
        price = util.price_to_lots(
            price,
            base_decimals=info.base_decimals,
            quote_decimals=info.quote_decimals,
            base_lot_size=info.base_lot_size,
            quote_lot_size=info.quote_lot_size,
        )
        order_type_: Any = types.order_type_from_str(order_type, program=self.program)
        taker_fee = config.taker_fee(info.perp_type, program=self.program)
        fee_multiplier = 1 + taker_fee if is_long else 1 - taker_fee
        base_qty = util.size_to_lots(
            size, decimals=info.base_decimals, lot_size=info.base_lot_size
        )
        quote_qty = round(price * fee_multiplier * base_qty * info.quote_lot_size)

        pre_ixs = []
        oo_key = None
        oo_info = self._get_open_orders_info(symbol)

        if oo_info is not None:
            oo_key = oo_info.key
        else:
            oo_key, _ = util.open_orders_pda(
                control=self._zo_margin.control,
                dex_market=info.address,
                program_id=self.program.program_id,
            )
            pre_ixs = [
                self.program.instruction["create_perp_open_orders"](
                    ctx=Context(
                        accounts={
                            "state": self.__config.ZO_STATE_ID,
                            "state_signer": self._zo_state_signer,
                            "authority": self.wallet.public_key,
                            "payer": self.wallet.public_key,
                            "margin": self._zo_margin_key,
                            "control": self._zo_margin.control,
                            "open_orders": oo_key,
                            "dex_market": info.address,
                            "dex_program": self.__config.ZO_DEX_ID,
                            "rent": SYSVAR_RENT_PUBKEY,
                            "system_program": SYS_PROGRAM_ID,
                        },
                        pre_instructions=pre_ixs,
                    )
                )
            ]

        return await self.program.rpc["place_perp_order"](
            is_long,
            price,
            base_qty,
            quote_qty,
            order_type_,
            limit,
            client_id,
            ctx=Context(
                accounts={
                    "state": self.__config.ZO_STATE_ID,
                    "state_signer": self._zo_state_signer,
                    "cache": self._zo_state.cache,
                    "authority": self.wallet.public_key,
                    "margin": self._zo_margin_key,
                    "control": self._zo_margin.control,
                    "open_orders": oo_key,
                    "dex_market": info.address,
                    "req_q": mkt.req_q,
                    "event_q": mkt.event_q,
                    "market_bids": mkt.bids,
                    "market_asks": mkt.asks,
                    "dex_program": self.__config.ZO_DEX_ID,
                    "rent": SYSVAR_RENT_PUBKEY,
                }
            ),
        )

    async def __cancel_order(
        self,
        *,
        symbol: str,
        side: None | Side = None,
        order_id: None | int = None,
        client_id: None | int = None,
    ):
        mkt = self.__dex_markets[symbol]
        oo = self._get_open_orders_info(symbol)

        if oo is None:
            raise ValueError("open orders account is uninitialized")

        return await self.program.rpc["cancel_perp_order"](
            order_id,
            side == "bid",
            client_id,
            ctx=Context(
                accounts={
                    "state": self.__config.ZO_STATE_ID,
                    "cache": self._zo_state.cache,
                    "authority": self.wallet.public_key,
                    "margin": self._zo_margin_key,
                    "control": self._zo_margin.control,
                    "open_orders": oo.key,
                    "dex_market": mkt.own_address,
                    "market_bids": mkt.bids,
                    "market_asks": mkt.asks,
                    "event_q": mkt.event_q,
                    "dex_program": self.__config.ZO_DEX_ID,
                }
            ),
        )

    async def cancel_order_by_order_id(self, order_id: int, side: Side, *, symbol: str):
        return await self.__cancel_order(symbol=symbol, order_id=order_id, side=side)

    async def cancel_order_by_client_id(self, client_id: int, *, symbol: str):
        return await self.__cancel_order(symbol=symbol, client_id=client_id)
