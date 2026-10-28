"""The suite runs as a dev stack: every switchable feature on (``VEXA_FEATURES=all``), so the CRM is
served. ``test_features.py`` sets or clears the value itself to test the release default (off)."""
import os

os.environ.setdefault('VEXA_FEATURES', 'all')
