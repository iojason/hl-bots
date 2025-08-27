# list_testnet_symbols.py
from hyperliquid.info import Info
from hyperliquid.utils import constants

def get_readable_spot_name(symbol, spot_meta=None):
    """Convert @ symbols to readable coin names using official Hyperliquid data"""
    if not spot_meta:
        return symbol
    
    # Create mapping from official tokens data
    token_mapping = {}
    for token in spot_meta.get('tokens', []):
        token_index = token.get('index', 0)
        token_name = token.get('name', '')
        if token_name:
            # Map @{index} to token name
            token_mapping[f'@{token_index}'] = f'{token_name}/USDC'
    
    return token_mapping.get(symbol, symbol)

# Get testnet symbols and asset context
info_testnet = Info(constants.TESTNET_API_URL, skip_ws=True)
meta_testnet = info_testnet.meta()
testnet_coins = [c["name"] for c in meta_testnet["universe"]]

# Get mainnet symbols and asset context
info_mainnet = Info(constants.MAINNET_API_URL, skip_ws=True)
meta_mainnet = info_mainnet.meta()
mainnet_coins = [c["name"] for c in meta_mainnet["universe"]]

# Get spot markets
try:
    spot_meta = info_mainnet.spot_meta()
    spot_coins = [c["name"] for c in spot_meta["universe"]] if "universe" in spot_meta else []
    print(f"Found {len(spot_coins)} spot markets")
except Exception as e:
    print(f"Warning: Could not fetch spot markets: {e}")
    spot_coins = []

# Get asset context data for mainnet (testnet may not have volume data)
try:
    meta_and_ctxs_mainnet = info_mainnet.meta_and_asset_ctxs()
    asset_ctxs_mainnet = meta_and_ctxs_mainnet[1] if len(meta_and_ctxs_mainnet) > 1 else []
    # Create a mapping of coin name to asset context
    coin_to_ctx_mainnet = {}
    for i, coin in enumerate(mainnet_coins):
        if i < len(asset_ctxs_mainnet):
            coin_to_ctx_mainnet[coin] = asset_ctxs_mainnet[i]
except Exception as e:
    print(f"Warning: Could not fetch asset context data: {e}")
    coin_to_ctx_mainnet = {}

# Get spot asset context data
try:
    spot_meta_and_ctxs = info_mainnet.spot_meta_and_asset_ctxs()
    spot_asset_ctxs = spot_meta_and_ctxs[1] if len(spot_meta_and_ctxs) > 1 else []
    # Create a mapping of spot coin name to asset context
    spot_coin_to_ctx = {}
    for i, coin in enumerate(spot_coins):
        if i < len(spot_asset_ctxs):
            spot_coin_to_ctx[coin] = spot_asset_ctxs[i]
except Exception as e:
    print(f"Warning: Could not fetch spot asset context data: {e}")
    spot_coin_to_ctx = {}

def analyze_momentum(ctx):
    """Analyze momentum based on price change, open interest, and funding rate"""
    try:
        mark_px = float(ctx.get('markPx', 0))
        prev_day_px = float(ctx.get('prevDayPx', 0))
        oi_contracts = float(ctx.get('openInterest', 0))
        funding_rate = float(ctx.get('funding', 0))
        oracle_px = float(ctx.get('oraclePx', 0))
        
        if mark_px == 0 or prev_day_px == 0:
            return "N/A", "N/A", "N/A"
        
        # Calculate price change percentage
        price_change_pct = ((mark_px - prev_day_px) / prev_day_px) * 100
        
        # Calculate funding rate annualized (funding is typically 8h, so *3*365)
        funding_annualized = funding_rate * 3 * 365 * 100  # Convert to percentage
        
        # Calculate open interest in dollar value
        open_interest_dollar = oi_contracts * mark_px
        
        # Determine momentum based on price change
        if price_change_pct > 5:
            momentum = "🟢 Strong Bullish"
        elif price_change_pct > 2:
            momentum = "🟢 Bullish"
        elif price_change_pct > 0.5:
            momentum = "🟡 Slightly Bullish"
        elif price_change_pct < -5:
            momentum = "🔴 Strong Bearish"
        elif price_change_pct < -2:
            momentum = "🔴 Bearish"
        elif price_change_pct < -0.5:
            momentum = "🟡 Slightly Bearish"
        else:
            momentum = "⚪ Neutral"
        
        # Determine OI level based on dollar value
        if open_interest_dollar > 100_000_000:  # >$100M
            oi_level = "Very High OI"
        elif open_interest_dollar > 10_000_000:  # >$10M
            oi_level = "High OI"
        elif open_interest_dollar > 1_000_000:  # >$1M
            oi_level = "Medium OI"
        elif open_interest_dollar > 100_000:  # >$100K
            oi_level = "Low OI"
        else:
            oi_level = "Very Low OI"
        
        # Determine funding sentiment
        if funding_annualized > 10:
            funding_sentiment = "🟢 High Funding"
        elif funding_annualized > 5:
            funding_sentiment = "🟡 Med Funding"
        elif funding_annualized < -10:
            funding_sentiment = "🔴 Low Funding"
        elif funding_annualized < -5:
            funding_sentiment = "🟡 Low Funding"
        else:
            funding_sentiment = "⚪ Normal Funding"
        
        return momentum, oi_level, funding_sentiment
    except (ValueError, TypeError, ZeroDivisionError):
        return "N/A", "N/A", "N/A"

# Get all unique symbols and sort alphabetically
all_symbols = sorted(set(testnet_coins + mainnet_coins + spot_coins))

# Print table header
print(f"\n{'Symbol':<15} {'Testnet':<7} {'Mainnet':<7} {'Volume (24h)':<12} {'Mark Price':<10} {'Open Interest':<12} {'Momentum':<20} {'OI Level':<15} {'Funding':<15}")
print("-" * 120)

# Print each symbol with availability and market data
for symbol in all_symbols:
    # Skip spot markets in the main table - they'll be shown in the spot analysis section
    if symbol.startswith('@') or symbol.endswith('/USDC'):
        continue
        
    testnet_available = "✅" if symbol in testnet_coins else "❌"
    mainnet_available = "✅" if symbol in mainnet_coins else "❌"
    
    # Get market data for mainnet
    volume_24h = "N/A"
    mark_price = "N/A"
    open_interest = "N/A"
    momentum = "N/A"
    oi_level = "N/A"
    funding_sentiment = "N/A"
    
    if symbol in coin_to_ctx_mainnet:
        ctx = coin_to_ctx_mainnet[symbol]
        try:
            # Format volume in millions/billions for readability
            vol = float(ctx.get('dayNtlVlm', 0))
            if vol >= 1_000_000_000:
                volume_24h = f"${vol/1_000_000_000:.1f}B"
            elif vol >= 1_000_000:
                volume_24h = f"${vol/1_000_000:.1f}M"
            elif vol >= 1_000:
                volume_24h = f"${vol/1_000:.1f}K"
            else:
                volume_24h = f"${vol:.0f}"
            
            # Format mark price
            mark_px = float(ctx.get('markPx', 0))
            if mark_px >= 1000:
                mark_price = f"${mark_px:,.0f}"
            elif mark_px >= 1:
                mark_price = f"${mark_px:.2f}"
            else:
                mark_price = f"${mark_px:.4f}"
            
            # Format open interest (convert from contract units to dollar value)
            oi_contracts = float(ctx.get('openInterest', 0))
            mark_px = float(ctx.get('markPx', 0))
            oi_dollar_value = oi_contracts * mark_px
            
            if oi_dollar_value >= 1_000_000_000:
                open_interest = f"${oi_dollar_value/1_000_000_000:.1f}B"
            elif oi_dollar_value >= 1_000_000:
                open_interest = f"${oi_dollar_value/1_000_000:.1f}M"
            elif oi_dollar_value >= 1_000:
                open_interest = f"${oi_dollar_value/1_000:.1f}K"
            else:
                open_interest = f"${oi_dollar_value:.0f}"
            
            # Analyze momentum
            momentum, oi_level, funding_sentiment = analyze_momentum(ctx)
            
        except (ValueError, TypeError):
            pass
    
    print(f"{symbol:<15} {testnet_available:<7} {mainnet_available:<7} {volume_24h:<12} {mark_price:<10} {open_interest:<12} {momentum:<20} {oi_level:<15} {funding_sentiment:<15}")

# Show summary statistics
print(f"\nTotal symbols: {len(all_symbols)}")
print(f"Testnet symbols: {len(testnet_coins)}")
print(f"Mainnet perpetual symbols: {len(mainnet_coins)}")
print(f"Spot symbols: {len(spot_coins)}")

# Show top 10 by volume
print(f"\nTop 10 by Volume (24h):")
print("-" * 40)
volume_data = []
for symbol in mainnet_coins:
    if symbol in coin_to_ctx_mainnet:
        ctx = coin_to_ctx_mainnet[symbol]
        try:
            vol = float(ctx.get('dayNtlVlm', 0))
            volume_data.append((symbol, vol))
        except (ValueError, TypeError):
            continue

# Sort by volume
volume_data.sort(key=lambda x: x[1], reverse=True)
for symbol, vol in volume_data[:10]:
    vol_str = f"${vol/1_000_000:.1f}M" if vol >= 1_000_000 else f"${vol/1_000:.1f}K"
    print(f"{symbol:<8} {vol_str}")

# Show high open interest assets (potential for large moves)
print(f"\nHigh Open Interest Assets (>$10M OI):")
print("-" * 40)
high_oi_assets = []
for symbol in mainnet_coins:
    if symbol in coin_to_ctx_mainnet:
        ctx = coin_to_ctx_mainnet[symbol]
        try:
            oi_contracts = float(ctx.get('openInterest', 0))
            mark_px = float(ctx.get('markPx', 0))
            oi_dollar_value = oi_contracts * mark_px
            if oi_dollar_value > 10_000_000:  # >$10M
                momentum, _, _ = analyze_momentum(ctx)
                high_oi_assets.append((symbol, oi_dollar_value, momentum))
        except (ValueError, TypeError):
            continue

# Sort by open interest
high_oi_assets.sort(key=lambda x: x[1], reverse=True)
for symbol, oi, momentum in high_oi_assets[:10]:
    if oi >= 1_000_000_000:
        oi_str = f"${oi/1_000_000_000:.1f}B"
    else:
        oi_str = f"${oi/1_000_000:.1f}M"
    print(f"{symbol:<8} {oi_str:<8} {momentum}")

# Show funding rate analysis
print(f"\nFunding Rate Analysis:")
print("-" * 40)
funding_data = []
for symbol in mainnet_coins:
    if symbol in coin_to_ctx_mainnet:
        ctx = coin_to_ctx_mainnet[symbol]
        try:
            funding_rate = float(ctx.get('funding', 0))
            funding_annualized = funding_rate * 3 * 365 * 100
            funding_data.append((symbol, funding_annualized))
        except (ValueError, TypeError):
            continue

# Sort by funding rate
funding_data.sort(key=lambda x: x[1], reverse=True)
print("Highest Funding Rates (Annualized):")
for symbol, funding in funding_data[:5]:
    print(f"{symbol:<8} {funding:+.2f}%")

print("\nLowest Funding Rates (Annualized):")
for symbol, funding in funding_data[-5:]:
    print(f"{symbol:<8} {funding:+.2f}%")

# Show momentum summary
print(f"\nMomentum Summary:")
print("-" * 40)
momentum_counts = {"🟢 Strong Bullish": 0, "🟢 Bullish": 0, "🟡 Slightly Bullish": 0, 
                   "⚪ Neutral": 0, "🟡 Slightly Bearish": 0, "🔴 Bearish": 0, "🔴 Strong Bearish": 0}

for symbol in mainnet_coins:
    if symbol in coin_to_ctx_mainnet:
        ctx = coin_to_ctx_mainnet[symbol]
        momentum, _, _ = analyze_momentum(ctx)
        if momentum in momentum_counts:
            momentum_counts[momentum] += 1

for momentum, count in momentum_counts.items():
    if count > 0:
        print(f"{momentum}: {count} assets")

# ============================================================================
# SPOT MARKET ANALYSIS
# ============================================================================
print(f"\n" + "="*80)
print("SPOT MARKET ANALYSIS - USDC PAIRS")
print("="*80)

def analyze_spot_momentum(ctx):
    """Analyze spot market momentum based on price change and volume"""
    try:
        mark_px = float(ctx.get('markPx', 0))
        prev_day_px = float(ctx.get('prevDayPx', 0))
        day_volume = float(ctx.get('dayNtlVlm', 0))
        
        if mark_px == 0 or prev_day_px == 0:
            return "N/A", "N/A", "N/A"
        
        # Calculate price change percentage
        price_change_pct = ((mark_px - prev_day_px) / prev_day_px) * 100
        
        # Determine price momentum
        if price_change_pct > 10:
            price_momentum = "🚀 Strong Bull"
        elif price_change_pct > 5:
            price_momentum = "🟢 Bullish"
        elif price_change_pct > 2:
            price_momentum = "🟡 Slightly Bull"
        elif price_change_pct < -10:
            price_momentum = "💥 Strong Bear"
        elif price_change_pct < -5:
            price_momentum = "🔴 Bearish"
        elif price_change_pct < -2:
            price_momentum = "🟡 Slightly Bear"
        else:
            price_momentum = "⚪ Neutral"
        
        # Determine volume activity
        if day_volume > 1_000_000:  # >$1M volume
            volume_activity = "🔥 High Volume"
        elif day_volume > 100_000:  # >$100K volume
            volume_activity = "📈 Active"
        elif day_volume > 10_000:  # >$10K volume
            volume_activity = "📊 Moderate"
        elif day_volume > 1_000:  # >$1K volume
            volume_activity = "📉 Low"
        else:
            volume_activity = "💤 Dormant"
        
        # Calculate volatility (simplified as price range)
        volatility = abs(price_change_pct)
        if volatility > 20:
            vol_level = "🌪️ High Vol"
        elif volatility > 10:
            vol_level = "⚡ Medium Vol"
        elif volatility > 5:
            vol_level = "🌊 Low Vol"
        else:
            vol_level = "🌊 Stable"
        
        return price_momentum, volume_activity, vol_level
        
    except (ValueError, TypeError, ZeroDivisionError):
        return "N/A", "N/A", "N/A"

# Get active spot markets (with volume > $1K)
active_spot_markets = []
for symbol in spot_coins:
    if symbol in spot_coin_to_ctx:
        ctx = spot_coin_to_ctx[symbol]
        try:
            volume = float(ctx.get('dayNtlVlm', 0))
            if volume > 1000:  # Only show markets with >$1K volume
                active_spot_markets.append(symbol)
        except (ValueError, TypeError):
            continue

print(f"\nActive Spot Markets (Volume > $1K): {len(active_spot_markets)}")
print("-" * 120)
print(f"{'Symbol':<15} {'Price':<12} {'24h Change':<12} {'Volume (24h)':<15} {'Price Momentum':<15} {'Volume':<12} {'Volatility':<12}")
print("-" * 120)

# Sort active spot markets by volume
spot_volume_data = []
for symbol in active_spot_markets:
    if symbol in spot_coin_to_ctx:
        ctx = spot_coin_to_ctx[symbol]
        try:
            volume = float(ctx.get('dayNtlVlm', 0))
            spot_volume_data.append((symbol, volume))
        except (ValueError, TypeError):
            continue

spot_volume_data.sort(key=lambda x: x[1], reverse=True)

for symbol, volume in spot_volume_data:
    ctx = spot_coin_to_ctx[symbol]
    try:
        # Format price
        mark_px = float(ctx.get('markPx', 0))
        if mark_px >= 1000:
            price_str = f"${mark_px:,.0f}"
        elif mark_px >= 1:
            price_str = f"${mark_px:.2f}"
        else:
            price_str = f"${mark_px:.4f}"
        
        # Calculate 24h change
        prev_day_px = float(ctx.get('prevDayPx', 0))
        if prev_day_px > 0:
            price_change = ((mark_px - prev_day_px) / prev_day_px) * 100
            change_str = f"{price_change:+.2f}%"
        else:
            change_str = "N/A"
        
        # Format volume
        if volume >= 1_000_000:
            vol_str = f"${volume/1_000_000:.1f}M"
        elif volume >= 1_000:
            vol_str = f"${volume/1_000:.1f}K"
        else:
            vol_str = f"${volume:.0f}"
        
        # Analyze momentum
        price_momentum, volume_activity, vol_level = analyze_spot_momentum(ctx)
        
        # Display readable symbol name
        display_symbol = get_readable_spot_name(symbol, spot_meta)
        
        print(f"{display_symbol:<15} {price_str:<12} {change_str:<12} {vol_str:<15} {price_momentum:<15} {volume_activity:<12} {vol_level:<12}")
        
    except (ValueError, TypeError):
        continue

# Show spot market insights
print(f"\nSpot Market Insights:")
print("-" * 50)

# Top gainers and losers
print("Top Spot Gainers (24h):")
gainers = []
for symbol in active_spot_markets:
    if symbol in spot_coin_to_ctx:
        ctx = spot_coin_to_ctx[symbol]
        try:
            mark_px = float(ctx.get('markPx', 0))
            prev_day_px = float(ctx.get('prevDayPx', 0))
            if prev_day_px > 0:
                change = ((mark_px - prev_day_px) / prev_day_px) * 100
                gainers.append((symbol, change))
        except (ValueError, TypeError):
            continue

gainers.sort(key=lambda x: x[1], reverse=True)
for symbol, change in gainers[:5]:
    display_symbol = get_readable_spot_name(symbol, spot_meta)
    print(f"{display_symbol:<15} {change:+.2f}%")

print("\nTop Spot Losers (24h):")
for symbol, change in gainers[-5:]:
    display_symbol = get_readable_spot_name(symbol, spot_meta)
    print(f"{display_symbol:<15} {change:+.2f}%")

# Volume leaders
print(f"\nHighest Volume Spot Markets:")
volume_leaders = []
for symbol in active_spot_markets:
    if symbol in spot_coin_to_ctx:
        ctx = spot_coin_to_ctx[symbol]
        try:
            volume = float(ctx.get('dayNtlVlm', 0))
            volume_leaders.append((symbol, volume))
        except (ValueError, TypeError):
            continue

volume_leaders.sort(key=lambda x: x[1], reverse=True)
for symbol, volume in volume_leaders[:10]:
    display_symbol = get_readable_spot_name(symbol, spot_meta)
    if volume >= 1_000_000:
        vol_str = f"${volume/1_000_000:.1f}M"
    else:
        vol_str = f"${volume/1_000:.1f}K"
    print(f"{display_symbol:<15} {vol_str}")

# Market breadth analysis
print(f"\nSpot Market Breadth:")
print("-" * 30)
bullish_count = 0
bearish_count = 0
neutral_count = 0

for symbol in active_spot_markets:
    if symbol in spot_coin_to_ctx:
        ctx = spot_coin_to_ctx[symbol]
        price_momentum, _, _ = analyze_spot_momentum(ctx)
        if "Bull" in price_momentum:
            bullish_count += 1
        elif "Bear" in price_momentum:
            bearish_count += 1
        else:
            neutral_count += 1

print(f"Bullish: {bullish_count} markets")
print(f"Bearish: {bearish_count} markets") 
print(f"Neutral: {neutral_count} markets")
print(f"Total Active: {len(active_spot_markets)} markets")

# Correlation analysis with major pairs
print(f"\nCorrelation Analysis (vs BTC/USDC):")
print("-" * 40)
btc_price = None
for symbol in active_spot_markets:
    if symbol == '@2':  # BTC/USDC
        if symbol in spot_coin_to_ctx:
            ctx = spot_coin_to_ctx[symbol]
            btc_price = float(ctx.get('markPx', 0))
            break

if btc_price and btc_price > 0:
    print(f"BTC/USDC Price: ${btc_price:.4f}")
    print("Relative Performance vs BTC:")
    
    for symbol in active_spot_markets[:10]:  # Top 10 by volume
        if symbol in spot_coin_to_ctx:
            ctx = spot_coin_to_ctx[symbol]
            try:
                mark_px = float(ctx.get('markPx', 0))
                if mark_px > 0:
                    relative_perf = (mark_px / btc_price) * 100
                    display_symbol = get_readable_spot_name(symbol, spot_meta)
                    print(f"{display_symbol:<15} {relative_perf:.2f}% of BTC")
            except (ValueError, TypeError):
                continue
