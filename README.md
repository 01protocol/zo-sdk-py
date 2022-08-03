# 01.xyz Python SDK

<p align="center">
<b><a href="https://01protocol.github.io/zo-sdk-py/">Documentation</a></b>
|
<b><a href="https://pypi.org/project/zo-sdk/">PyPi</a></b>
</p>

Python SDK to interface with the 01 Solana program.

## Installation

```
$ pip install zo-sdk
```

## General Usage

```python
from zo import Zo

# Create the client. By default, this loads the local payer
# and initializes a margin account for the payer if there
# isn't already one.
zo = await Zo.new(cluster='devnet')

# View market and collateral info.
print(zo.collaterals["BTC"])
print(zo.markets["BTC-PERP"])

# Deposit and withdraw collateral.
await zo.deposit(1, "SOL")
await zo.withdraw(1, "SOL")

# Place and cancel orders.
await zo.place_order(1., 100., 'bid',
    symbol="SOL-PERP", order_type="limit", client_id=1)
await zo.cancel_order_by_client_id(1, symbol="SOL-PERP")

# Refresh loaded accounts to see updates,
# such as change in collateral after deposits.
await zo.refresh()

# View own balance, positions and orders.
print(zo.balance["BTC"])
print(zo.position["BTC-PERP"])
print(zo.orders["BTC-PERP"])

# Dispatch multiple instructions in a single transaction,
# using the `_ix` variant.
await zo.send(
    zo.cancel_order_by_client_id_ix(1, symbol="SOL-PERP"),
    zo.place_order_ix(1., 100., 'bid',
        symbol="SOL-PERP", order_type="limit", client_id=1),
)
```
