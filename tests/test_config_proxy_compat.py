from translation_tool.utils import config_manager
from translation_tool.utils.config_access import get_runtime_config


def test_lazy_config_proxy_reads_current_config(monkeypatch):
    monkeypatch.setattr(config_manager, 'load_config', lambda *args, **kwargs: {'x': 1, 'nested': {'y': 2}})

    assert config_manager.config['x'] == 1
    assert config_manager.config.get('nested') == {'y': 2}
    assert 'x' in config_manager.config


def test_get_runtime_config_returns_load_config_result(monkeypatch):
    monkeypatch.setattr('translation_tool.utils.config_access.load_config', lambda: {'runtime': True})
    assert get_runtime_config() == {'runtime': True}
