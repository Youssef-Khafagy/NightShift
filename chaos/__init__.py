"""The chaos control plane. Never deployed, never imported by src/.

Scenario definitions, injection code and ground truth live here and run from
the owner's laptop. The agent (M5 onwards) must never see any of it: it gets
only what a real on-call engineer would see in AWS. tests/test_integrity.py
enforces the separation.
"""
