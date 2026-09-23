"""Put the repo root on sys.path so `import dl2model` works under pytest
regardless of the working directory. Shared by every task's tests (read-only)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
