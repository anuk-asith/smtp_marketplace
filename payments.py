"""
Static wallet payment addresses.
No third-party gateway — buyer sends to your address, admin confirms manually.
"""
from __future__ import annotations

BTC_ADDRESS = "bc1q7y4j57c5965e30dmd6ez2s9hschuwfvga82guf"
USDT_TRC20_ADDRESS = "TJfbLgPnfxtvenJyriBLDz4Sy415iwwyP8"

PAYMENT_METHODS: dict[str, dict] = {
    "BTC": {
        "label": "₿ Bitcoin (BTC)",
        "address": BTC_ADDRESS,
        "network": "Bitcoin on-chain",
        "confirm_time": "~30–60 min",
    },
    "USDT-TRC20": {
        "label": "💵 USDT TRC-20 (Tron)",
        "address": USDT_TRC20_ADDRESS,
        "network": "Tron (TRC-20)",
        "confirm_time": "~1–3 min",
    },
}
