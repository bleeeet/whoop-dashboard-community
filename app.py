#!/usr/bin/env python3
"""Standard entry point (hosts commonly probe for app.py / main.py).

It only delegates to cloud_server.main(), so whichever start command a platform
auto-detects, what runs is the hosted flavour: first-run wizard, daily scheduler and
a data root outside the source tree. Kept separate from cloud_server.py so the
detected entry name never has to be configured by hand.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cloud_server import main  # noqa: E402  (path set above)

if __name__ == '__main__':
    sys.exit(main())
