import os
import sys
import time

sys.path.insert(0, "/app/src")

from common.ctrader_deals import _fetch_symbol_and_deals


def main() -> None:
    from common.ctrader_token_store import get_tokens
    from features.strategy.common.config_loader import get_ctrader_config

    strategy = sys.argv[1]
    days = int(sys.argv[2])
    cfg = get_ctrader_config(strategy)
    account_id = cfg["ctrader_account_id"]
    env = cfg["ctrader_env"]
    symbol_id = cfg["ctrader_symbol_id"]
    units_per_lot = cfg["ctrader_units_per_lot"]

    client_id = os.environ["CTRADER_CLIENT_ID"].strip()
    client_secret = os.environ["CTRADER_CLIENT_SECRET"].strip()
    access_token, _ = get_tokens()
    if not access_token:
        raise RuntimeError("DB 에 cTrader access token 없음")

    to_ms = int(time.time() * 1000)
    from_ms = to_ms - days * 86_400_000

    result = _fetch_symbol_and_deals(
        account_id, symbol_id, env == "live",
        client_id, client_secret, access_token,
        from_ms, to_ms,
    )
    sym = result["symbol"]
    print(f"[deals] strategy={strategy} account={account_id} env={env} symbol_id={symbol_id} units_per_lot(yaml)={units_per_lot}")
    print(
        f"[deals] lotSize={sym.lotSize} minVolume={sym.minVolume} "
        f"stepVolume={sym.stepVolume} maxVolume={sym.maxVolume}"
    )
    print(f"[deals] count={len(result['deals'])} hasMore={result['hasMore']}")
    for deal in result["deals"]:
        print("----")
        print(deal)


main()
