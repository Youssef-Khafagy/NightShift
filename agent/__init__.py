"""The on-call agent. Reads the store through read-only tools, never through
the chaos control plane (tests/test_integrity.py enforces the separation)."""
