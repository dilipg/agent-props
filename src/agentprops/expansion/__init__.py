"""Seeded deterministic expansion.

Filled at M7: dataset_expand, driven by Seeded(seed, salt) so that the same
seed always produces the same output. No bare random, uuid4() or
datetime.now() anywhere in this subpackage.
"""
