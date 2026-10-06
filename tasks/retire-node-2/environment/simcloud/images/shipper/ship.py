import time
from pathlib import Path

buf = Path("/buffer/spool.log")
while True:
    with buf.open("a") as f:
        f.write(f"{time.time():.0f} shipped\n")
    time.sleep(5)
