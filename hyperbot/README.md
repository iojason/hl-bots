# Simple Hyperliquid Market Making Bot

A lean, fast, and scalable market making bot focused on profitable trading with rebates on Hyperliquid.

## Features

- **Simple & Fast**: Minimal codebase with no database dependencies
- **Profitable Focus**: Designed for small accounts with risk management
- **Rebate Optimization**: Uses maker orders to earn trading rebates
- **Real-time Trading**: Fast order placement and position management
- **Risk Management**: Built-in take profit and stop loss mechanisms
- **Console Output**: Clear logging and position summaries

## Quick Start

### 1. Install Dependencies

```bash
cd hyperbot
pip install -r requirements.txt
```

### 2. Set Environment Variables

Create a `.env` file in the `hyperbot` directory:

```bash
HL_ACCOUNT_ADDRESS=your_wallet_address_here
HL_SECRET_KEY=your_private_key_here
```

### 3. Configure Trading Parameters

Edit `config.json` to set your trading parameters:

```json
{
  "mode": "testnet",
  "wallet_address": "your_wallet_address",
  "coins": ["BTC", "ETH", "SOL", "DOGE"],
  "loop_ms": 500,
  "size_notional_usd": 25,
  "max_position_usd": 200,
  "min_spread_bps": 5.0,
  "take_profit_bps": 30.0,
  "stop_loss_bps": 50.0,
  "max_orders_per_coin": 2
}
```

### 4. Run the Bot

```bash
python main.py
```

Or with custom config:

```bash
python main.py --config my_config.json --mode testnet
```

## Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `mode` | Trading mode (testnet/mainnet) | testnet |
| `wallet_address` | Your wallet address | - |
| `coins` | List of coins to trade | ["BTC", "ETH", "SOL", "DOGE"] |
| `loop_ms` | Main loop interval in milliseconds | 500 |
| `size_notional_usd` | Order size in USD | 25 |
| `max_position_usd` | Maximum position size in USD | 200 |
| `min_spread_bps` | Minimum spread in basis points | 5.0 |
| `take_profit_bps` | Take profit threshold in basis points | 30.0 |
| `stop_loss_bps` | Stop loss threshold in basis points | 50.0 |
| `max_orders_per_coin` | Maximum open orders per coin | 2 |

## How It Works

### Market Making Strategy

1. **Order Placement**: Places bid and ask orders slightly inside the spread
2. **Position Management**: Monitors positions for take profit/stop loss
3. **Risk Control**: Limits position sizes and order frequency
4. **Rebate Capture**: Uses maker orders to earn trading rebates

### Trading Logic

- **Spread Check**: Only trades when spread is wide enough (min_spread_bps)
- **Position Limits**: Prevents over-exposure with max_position_usd
- **Take Profit**: Closes profitable positions at take_profit_bps
- **Stop Loss**: Cuts losses at stop_loss_bps
- **Rate Limiting**: Prevents order spam with time-based limits

### Risk Management

- Maximum position size per coin
- Take profit and stop loss levels
- Order frequency limits
- Position monitoring and management

## File Structure

```
hyperbot/
├── main.py           # Main entry point
├── client.py         # Hyperliquid API client
├── strategy.py       # Market making strategy
├── config.json       # Configuration file
├── requirements.txt  # Python dependencies
└── README.md        # This file
```

## Safety Features

- **Testnet First**: Always test on testnet before mainnet
- **Small Sizes**: Conservative position sizes for small accounts
- **Stop Losses**: Automatic loss protection
- **Error Handling**: Graceful error recovery
- **Signal Handling**: Clean shutdown on Ctrl+C

## Monitoring

The bot provides real-time console output including:

- Account balance
- Position summaries
- Order placement status
- PnL tracking
- Error messages

## Example Output

```
🚀 Starting Simple Hyperliquid Market Making Bot
🌐 Mode: TESTNET
💰 Wallet: 0x07Cf550BFB384487dea8F2EA7842BE931c9aDae7
🪙 Coins: BTC, ETH, SOL, DOGE
⏱️  Loop interval: 500ms

✅ Client initialized for TESTNET
🎯 Market maker initialized for 4 coins
   Size: $25, Max Position: $200
   Min Spread: 5.0bps, Take Profit: 30.0bps

🔄 Starting main loop with 500ms intervals...
============================================================
💰 Account Balance: $1000.00

📈 BTC: Placing orders - Bid: 45000.50, Ask: 45001.50, Size: 0.0006
✅ BTC: Bid order placed successfully
✅ BTC: Ask order placed successfully

📊 Position Summary:
   No active positions
   Total PnL: $0.00
--------------------------------------------------
```

## Important Notes

- **Test First**: Always test on testnet before using real funds
- **Small Amounts**: Start with small position sizes
- **Monitor**: Keep an eye on the bot's performance
- **Backup**: Keep your private keys secure
- **Updates**: Check for SDK updates regularly

## Troubleshooting

### Common Issues

1. **"Failed to get meta"**: Check API connectivity
2. **"Order failed"**: Check balance and position limits
3. **"Rate limited"**: Reduce loop frequency or order size
4. **"Invalid price"**: Check tick size configuration

### Performance Tips

- Use faster loop intervals for more responsive trading
- Adjust spread thresholds based on market conditions
- Monitor and adjust position sizes based on volatility
- Consider market hours for optimal performance

## Disclaimer

This bot is for educational purposes. Trading cryptocurrencies involves risk. Use at your own risk and never invest more than you can afford to lose.
