# Trading Bot Documentation

## Overview
This real‑time ETH/USDT scalping bot for Binance Futures uses multi‑timeframe analysis, dynamic risk controls, and zero‑loss strategies.

## Setup
1. Clone the repository.
2. Install dependencies: `pip install -r requirements.txt`
3. Configure your API keys in `.env` and `config.json`.
4. Run the bot: `python main.py`

## Testing
Run unit tests and end‑to‑end tests with: `pytest`

## Logging & History
Trades are logged into an SQLite database (`trading_bot.db`) with orders and logs stored.