
import time, json
from dataclasses import dataclass
from typing import Optional, Dict, Any

try:
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    HAVE_HL = True
except Exception:
    HAVE_HL = False

@dataclass
class PingStat:
    last_ms: Optional[float] = None
    samples: list = None
    def __post_init__(self):
        if self.samples is None: self.samples = []

class HLClient:
    def __init__(self, db, bot_id: str, mode: str):
        self.db = db
        self.bot_id = bot_id
        self.mode = mode
        self.ping = PingStat()

    def connect(self):
        # Real WS connect can be added here with SDK WS where available.
        # We just record a ping sample to keep dashboard non-empty.
        self._record_ping(50.0)

    def _record_ping(self, ms: float):
        self.ping.last_ms = ms
        self.ping.samples.append(ms)

    def avg_latency_ms(self):
        if not self.ping.samples: return None
        n = min(60, len(self.ping.samples))
        arr = self.ping.samples[-n:]
        return sum(arr)/len(arr)

    def subscribe_ticker(self, coins):
        # TODO: implement real book subscription via SDK WS
        pass

    def place_post_only(self, order: Dict[str, Any]):
        # TODO: implement signed order via SDK Exchange endpoint
        # return dict(order_id='', client_oid=order.get('client_oid'))
        return {"order_id": "", "client_oid": order.get("client_oid")}

    def cancel_by_invalidating_nonce(self):
        # TODO: implement as described in docs
        pass
