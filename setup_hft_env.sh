#!/bin/bash

# HFT Trading Environment Setup - Real-Time Per-Coin Order Placement
# This script sets up environment variables for optimized dual rate limiting
# The bot now places orders immediately when market data arrives via WebSocket callbacks
# This provides sub-millisecond latency for order placement

echo "Setting up HFT trading environment variables..."

# Dual Rate Limiting Configuration - Optimized for HFT
export HL_WS_CAPACITY_PER_MIN=2000        # WebSocket operations (high capacity)
export HL_REST_CAPACITY_PER_MIN=1200       # REST API operations (conservative)

# Rate limiting weights - Optimized for trading performance
export HL_RL_WEIGHT_ORDER=1               # Order placement weight (minimal)
export HL_RL_WEIGHT_CANCEL=1              # Cancel weight (minimal)
export HL_RL_WEIGHT_L2BOOK=2              # L2Book weight (market data)
export HL_RL_WEIGHT_META=20               # Meta weight (startup only, cached)
export HL_RL_WEIGHT_USERFEES=10           # User fees weight (cached 15min, reduced from 20)

echo "Environment variables set:"
echo "  HL_WS_CAPACITY_PER_MIN=$HL_WS_CAPACITY_PER_MIN (WebSocket operations)"
echo "  HL_REST_CAPACITY_PER_MIN=$HL_REST_CAPACITY_PER_MIN (REST API operations)"
echo "  HL_RL_WEIGHT_ORDER=$HL_RL_WEIGHT_ORDER"
echo "  HL_RL_WEIGHT_CANCEL=$HL_RL_WEIGHT_CANCEL"
echo "  HL_RL_WEIGHT_L2BOOK=$HL_RL_WEIGHT_L2BOOK"
echo "  HL_RL_WEIGHT_META=$HL_RL_WEIGHT_META"
echo "  HL_RL_WEIGHT_USERFEES=$HL_RL_WEIGHT_USERFEES"

echo ""
echo "To run the bot with these settings:"
echo "  source setup_hft_env.sh && python -m py_mm_bot.run --db ./mm_data.db --config ./configs/fast_trading.json"
echo ""
echo "Or add these to your .env file for permanent use."
