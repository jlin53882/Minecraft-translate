from translation_tool.core.md_translation_progress import _ProgressProxy


class _Session:
    def __init__(self):
        self.values = []

    def set_progress(self, value):
        self.values.append(value)


def test_progress_proxy_maps_inner_progress_to_outer_segment():
    session = _Session()
    proxy = _ProgressProxy(session, 0.33, 0.33)

    proxy.set_progress(0.5)

    assert session.values == [0.495]


def test_progress_proxy_clamps_invalid_values():
    session = _Session()
    proxy = _ProgressProxy(session, 0.1, 0.2)

    proxy.set_progress(-1)
    proxy.set_progress(3)

    assert session.values == [0.1, 0.30000000000000004]
