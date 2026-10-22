import math


def target_notional_usd(balance: float, target_mdd: float, backtest_mdd: float) -> float:
    if balance <= 0:
        raise ValueError(f"balance={balance} 가 0 이하")
    if target_mdd <= 0 or backtest_mdd <= 0:
        raise ValueError(f"target_mdd={target_mdd}, backtest_mdd={backtest_mdd} 가 0 이하")
    notional = balance * target_mdd / backtest_mdd
    return notional


def order_volume(notional_usd: float, price: float, units_per_lot: int, step_volume: int, max_volume: int) -> int:
    if price <= 0:
        raise ValueError(f"price={price} 가 0 이하")
    if units_per_lot <= 0 or step_volume <= 0:
        raise ValueError(f"units_per_lot={units_per_lot}, step_volume={step_volume} 가 0 이하")
    steps = math.ceil(round(notional_usd / price * units_per_lot / step_volume, 9))
    volume = steps * step_volume
    if volume > max_volume:
        raise ValueError(f"volume={volume} 가 maxVolume={max_volume} 초과")
    return volume
