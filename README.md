# 01.xyz Python SDK

```python
from zo import Zo
from anchorpy import Wallet

# Create the client. By default, this loads the local payer
# and initializes a margin account for the payer if there
# isn't already one.
zo = await Zo.new(cluster='devnet')

# View market and collateral info.
print(zo.collaterals["BTC"])
print(zo.markets["BTC-PERP"])

# Deposit and withdraw collateral.
await zo.deposit(1, mint=zo.collateral_info["SOL"].mint)
await zo.withdraw(1, mint=zo.collateral_info["SOL"].mint)

# Place and cancel orders.
await zo.place_order(1., 100., 'bid', symbol="SOL-PERP", order_type="limit", client_id=1)
await zo.cancel_order_by_client_id(1, symbol="SOL-PERP")

# Refresh loaded accounts to see updates, such as change in collateral after deposits.
await zo.refresh()

# View own balance, positions and orders.
print(zo.balance["BTC"])
print(zo.position["BTC-PERP"])
print(zo.orders["BTC-PERP"])
```
