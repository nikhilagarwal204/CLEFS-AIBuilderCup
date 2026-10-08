"""python -m clefs.worker [--once] [--no-services] [--budget S]"""

import sys

from clefs.worker.loop import main

if __name__ == "__main__":
    sys.exit(main())
