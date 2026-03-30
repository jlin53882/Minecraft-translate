# -*- coding: utf-8 -*-
"""Remaining modules import tests - 9 modules"""



# ==================== app ====================


def test_app_services_import():
    """Verify app.services can be imported."""
    from app import services
    assert services is not None


def test_app_services_impl_import():
    """Verify app.services_impl can be imported."""
    from app import services_impl
    assert services_impl is not None


def test_app_task_session_import():
    """Verify app.task_session can be imported."""
    from app import task_session
    assert task_session is not None


# ==================== translation_tool.utils ====================


def test_log_unit_import():
    """Verify log_unit can be imported."""
    from translation_tool.utils import log_unit
    assert log_unit is not None


def test_safe_json_loader_import():
    """Verify safe_json_loader can be imported."""
    from translation_tool.utils import safe_json_loader
    assert safe_json_loader is not None


def test_species_cache_import():
    """Verify species_cache can be imported."""
    from translation_tool.utils import species_cache
    assert species_cache is not None


def test_ui_logging_handler_import():
    """Verify ui_logging_handler can be imported."""
    from translation_tool.utils import ui_logging_handler
    assert ui_logging_handler is not None
