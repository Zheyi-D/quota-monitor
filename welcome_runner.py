#!/usr/bin/env python3
"""Welcome email runner — called by welcome.yml when new subscribers join."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

# Import from ci_run
from ci_run import _send_welcome_emails

_send_welcome_emails()
