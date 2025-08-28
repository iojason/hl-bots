#!/bin/bash

# Simple Hyperliquid Market Making Bot Runner
# This script sets up the environment and runs the bot

echo "🚀 Starting Simple Hyperliquid Market Making Bot"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo "Please create a .env file with your credentials:"
    echo "  HL_ACCOUNT_ADDRESS=your_wallet_address"
    echo "  HL_SECRET_KEY=your_private_key"
    echo ""
    echo "You can copy from env_example.txt as a starting point."
    exit 1
fi

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed!"
    exit 1
fi

# Check if requirements are installed
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install requirements
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Run the bot
echo "🔄 Starting bot..."
python main.py "$@"
