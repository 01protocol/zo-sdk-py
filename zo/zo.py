from typing import *
import asyncio
import os
import json
from anchorpy import Idl, Program, Provider, Context, Wallet
from anchorpy.error import AccountDoesNotExistError
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.rpc.commitment import Finalized
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.sysvar import SYSVAR_RENT_PUBKEY
from solana.system_program import SYS_PROGRAM_ID
from spl.token.instructions import get_associated_token_address
from spl.token.constants import TOKEN_PROGRAM_ID

from . import util, types, config
from .config import configs, Config
from .types import Side, OrderType, CollateralInfo, MarketInfo
from .dex import Market


class Zo:
    __program: Program
    __config: Config

    _zo_state: Any
    _zo_state_signer: PublicKey
    _zo_cache: Any
    _zo_margin: Any
    _zo_margin_key: None | PublicKey
    _zo_control: Any

    _memo_load_dex_market: dict[int, Market] = {}

    def __init__(
        self,
        *,
        _program,
        _config,
        _state,
        _state_signer,
        _cache,
        _margin,
        _margin_key,
        _control,
    ):
        self.__program = _program
        self.__config = _config
        self._zo_state = _state
        self._zo_state_signer = _state_signer
        self._zo_cache = _cache
        self._zo_margin = _margin
        self._zo_margin_key = _margin_key
        self._zo_control = _control

    @classmethod
    async def new(
        cls,
        *,
        payer: Keypair,
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
        wallet = Wallet(payer)
        provider = Provider(conn, wallet, opts=tx_opts)
        program = Program(idl, config.ZO_PROGRAM_ID, provider=provider)

        state = await program.account["State"].fetch(config.ZO_STATE_ID)
        state_signer, state_signer_nonce = util.state_signer_pda(
            state=config.ZO_STATE_ID, program_id=config.ZO_PROGRAM_ID
        )
        cache = await program.account["Cache"].fetch(state.cache)

        if state.signer_nonce != state_signer_nonce:
            raise ValueError(
                f"Invalid state key ({config.ZO_STATE_ID}) for program id ({config.ZO_PROGRAM_ID})"
            )

        margin = None
        margin_key = None
        control = None

        if load_margin:
            margin_key, nonce = util.margin_pda(
                owner=wallet.public_key,
                state=config.ZO_STATE_ID,
                program_id=config.ZO_PROGRAM_ID,
            )
            try:
                margin = await program.account["Margin"].fetch(margin_key)
                control = await program.account["Control"].fetch(margin.control)
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
                control = await program.account["Control"].fetch(margin.control)

        return cls(
            _config=config,
            _program=program,
            _state=state,
            _state_signer=state_signer,
            _cache=cache,
            _margin=margin,
            _margin_key=margin_key,
            _control=control,
        )

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

    def _get_collateral_index(self, key: int | str | PublicKey, /) -> int:
        if isinstance(key, int):
            if key < self._zo_state.total_collaterals:
                return int(key)
        elif isinstance(key, str) and key != "":
            for i, c in enumerate(self._zo_state.collaterals):
                if key == util.decode_symbol(c.oracle_symbol):
                    return i
        elif isinstance(key, PublicKey) and key != PublicKey(0):
            for i, c in enumerate(self._zo_state.collaterals):
                if key == c.mint:
                    return i
        raise LookupError(f"invalid collateral key '{key}'")

    def _get_market_index(self, key: int | str, /) -> int:
        if isinstance(key, int):
            if key < self._zo_state.total_markets:
                return int(key)
        elif isinstance(key, str) and key != "":
            for i, m in enumerate(self._zo_state.perp_markets):
                if key == util.decode_symbol(m.symbol):
                    return i
        raise LookupError(f"invalid market key '{key}'")

    def _get_open_orders_info(self, key: int | str, /):
        i = self._get_market_index(key)
        o = self._zo_control.open_orders_agg[i]
        if o.key == PublicKey(0):
            return None
        else:
            return o

    async def _load_dex_market(self, key: int | str) -> Market:
        i = self._get_market_index(key)
        if i not in self._memo_load_dex_market:
            k = self.market_info[i].dex_market
            res: Any = await self.connection.get_account_info(k)
            mkt = Market.from_base64(res["result"]["value"]["data"][0])
            self._memo_load_dex_market[i] = mkt
        return self._memo_load_dex_market[i]

    @property
    def collateral_info(self):
        class Indexer:
            def __init__(self, inner: Zo):
                self.inner = inner

            def __len__(self):
                return self.inner._zo_state.total_collaterals

            def __getitem__(self, i: int | str | PublicKey) -> CollateralInfo:
                i = self.inner._get_collateral_index(i)
                c = self.inner._zo_state.collaterals[i]
                v = self.inner._zo_state.vaults[i]
                d = {
                    **c.__dict__,
                    "oracle_symbol": util.decode_symbol(c.oracle_symbol),
                    "vault": v,
                }
                del d["padding"]
                return CollateralInfo(**d)

        return Indexer(self)

    @property
    def collateral(self):
        class Indexer:
            def __init__(self, inner: Zo):
                self.inner = inner

            def __len__(self):
                return self.inner._zo_state.total_collaterals

            def __getitem__(self, i: int | str | PublicKey) -> float:
                i = self.inner._get_collateral_index(i)
                c = util.decode_wrapped_i80f48(self.inner._zo_margin.collateral[i])
                m = self.inner._zo_cache.borrow_cache[i]
                m = m.supply_multiplier if c >= 0 else m.borrow_multiplier
                c *= util.decode_wrapped_i80f48(m)
                return util.small_to_big_amount(
                    c, decimals=self.inner.collateral_info[i].decimals
                )

        return Indexer(self)

    @property
    def market_info(self):
        class Indexer:
            def __init__(self, inner: Zo):
                self.inner = inner

            def __len__(self):
                return self.inner._zo_state.total_markets

            def __getitem__(self, i: int | str) -> MarketInfo:
                i = self.inner._get_market_index(i)
                m = self.inner._zo_state.perp_markets[i]
                d: dict[str, Any] = {
                    **m.__dict__,
                    "symbol": util.decode_symbol(m.symbol),
                    "oracle_symbol": util.decode_symbol(m.oracle_symbol),
                    "base_decimals": m.asset_decimals,
                    "base_lot_size": m.asset_lot_size,
                    "quote_decimals": 6,
                }
                del d["padding"]
                del d["asset_decimals"]
                del d["asset_lot_size"]
                return MarketInfo(**d)

        return Indexer(self)

    async def refresh(self):
        self._memo_load_dex_market = {}

        self._zo_state, self._zo_cache = await asyncio.gather(
            self.program.account["State"].fetch(self.__config.ZO_STATE_ID),
            self.program.account["Cache"].fetch(self._zo_state.cache),
        )

        if self._zo_margin_key is not None:
            self._zo_margin, self._zo_control = await asyncio.gather(
                self.program.account["Margin"].fetch(self._zo_margin_key),
                self.program.account["Control"].fetch(self._zo_margin.control),
            )

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

        decimals = self.collateral_info[mint].decimals
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
                    "vault": self.collateral_info[mint].vault,
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

        decimals = self.collateral_info[mint].decimals
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
                    "vault": self.collateral_info[mint].vault,
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
        mkt = await self._load_dex_market(symbol)
        info = self.market_info[symbol]
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
                dex_market=info.dex_market,
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
                            "dex_market": info.dex_market,
                            "dex_program": self.__config.ZO_DEX_ID,
                            "rent": SYSVAR_RENT_PUBKEY,
                            "system_program": SYS_PROGRAM_ID,
                        },
                        pre_instructions=pre_ixs,
                    )
                )
            ]

        print(price, base_qty, quote_qty)

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
                    "dex_market": info.dex_market,
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
        mkt = await self._load_dex_market(symbol)
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
