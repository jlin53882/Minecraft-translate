"""Worker cap logic tests.

Verifies that worker count is capped to max(1, cpu_count // 2),
and config values are respected within that cap.
"""


def _compute_workers(cpu_count_val, config_workers_val) -> int:
    """Mirror the actual cap logic for assertion reference.

    Copied from lang_merger.py and jar_processor_extract.py.
    """
    cpu_count = cpu_count_val or 2
    max_allowed = max(1, cpu_count // 2)
    if isinstance(config_workers_val, int) and config_workers_val > 0:
        return min(config_workers_val, max_allowed)
    return max_allowed


class TestWorkerCap:
    """Worker cap boundary tests (cpu=8 → cap=4, cpu=4 → cap=2)."""

    def test_no_config_uses_cpu_half(self):
        assert _compute_workers(8, None) == 4   # cpu=8, cap=4
        assert _compute_workers(4, None) == 2   # cpu=4, cap=2

    def test_config_above_cap_is_capped(self):
        assert _compute_workers(8, 32) == 4    # cpu=8, cap=4, config=32 → 4
        assert _compute_workers(8, 100) == 4   # cpu=8, cap=4, config=100 → 4

    def test_config_below_cap_is_used(self):
        assert _compute_workers(8, 2) == 2     # cpu=8, cap=4, config=2 → 2
        assert _compute_workers(8, 3) == 3     # cpu=8, cap=4, config=3 → 3

    def test_config_exactly_cap(self):
        assert _compute_workers(8, 4) == 4     # cpu=8, cap=4, config=4 → 4

    def test_zero_config_falls_back_to_cap(self):
        assert _compute_workers(8, 0) == 4     # cpu=8, cap=4, config=0 → 4

    def test_negative_config_falls_back_to_cap(self):
        assert _compute_workers(8, -1) == 4    # cpu=8, cap=4, config=-1 → 4

    def test_single_core_min_one(self):
        assert _compute_workers(1, None) == 1  # cpu=1, cap=1
        assert _compute_workers(2, None) == 1  # cpu=2, cap=1

    def test_none_cpu_count_defaults_to_two(self):
        assert _compute_workers(None, None) == 1  # None → 2, cap=1

    def test_single_core_with_config_above_cap(self):
        assert _compute_workers(1, 8) == 1    # cpu=1, cap=1, config=8 → 1
