import asyncio
import sys

sys.path.insert(0, "/app/src")

from common.ctrader_onboard import run_onboard

result = asyncio.run(run_onboard(sys.argv[1]))
print(result)
