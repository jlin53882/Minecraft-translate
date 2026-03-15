# -*- coding: utf-8 -*-
"""Medium Priority Module Tests - checkers, plugins, utils"""

import pytest


# ==================== checkers ====================


def test_english_residue_checker_import():
    """Verify english_residue_checker can be imported."""
    from translation_tool.checkers import english_residue_checker
    assert english_residue_checker is not None


def test_variant_comparator_import():
    """Verify variant_comparator can be imported."""
    from translation_tool.checkers import variant_comparator
    assert variant_comparator is not None


def test_variant_comparator_tsv_import():
    """Verify variant_comparator_tsv can be imported."""
    from translation_tool.checkers import variant_comparator_tsv
    assert variant_comparator_tsv is not None


# ==================== plugins ====================


def test_ftbquests_import():
    """Verify ftbquests can be imported."""
    from translation_tool.plugins import ftbquests
    assert ftbquests is not None


def test_kubejs_import():
    """Verify kubejs can be imported."""
    from translation_tool.plugins import kubejs
    assert kubejs is not None


def test_md_import():
    """Verify md can be imported."""
    from translation_tool.plugins import md
    assert md is not None


# ==================== utils ====================


def test_cache_loader_import():
    """Verify cache_loader can be imported."""
    from translation_tool.utils import cache_loader
    assert cache_loader is not None


def test_cache_overview_import():
    """Verify cache_overview can be imported."""
    from translation_tool.utils import cache_overview
    assert cache_overview is not None


def test_config_access_import():
    """Verify config_access can be imported."""
    from translation_tool.utils import config_access
    assert config_access is not None


# ==================== core modules (additional) ====================


def test_icon_classifier_import():
    """Verify icon_classifier can be imported."""
    from translation_tool.core import icon_classifier
    assert icon_classifier is not None


def test_icon_preview_cache_import():
    """Verify icon_preview_cache can be imported."""
    from translation_tool.core import icon_preview_cache
    assert icon_preview_cache is not None


def test_icon_reason_import():
    """Verify icon_reason can be imported."""
    from translation_tool.core import icon_reason
    assert icon_reason is not None


def test_icon_resolver_import():
    """Verify icon_resolver can be imported."""
    from translation_tool.core import icon_resolver
    assert icon_resolver is not None


def test_jar_processor_import():
    """Verify jar_processor can be imported."""
    from translation_tool.core import jar_processor
    assert jar_processor is not None


def test_jar_processor_discovery_import():
    """Verify jar_processor_discovery can be imported."""
    from translation_tool.core import jar_processor_discovery
    assert jar_processor_discovery is not None


def test_jar_processor_extract_import():
    """Verify jar_processor_extract can be imported."""
    from translation_tool.core import jar_processor_extract
    assert jar_processor_extract is not None


def test_jar_processor_preview_import():
    """Verify jar_processor_preview can be imported."""
    from translation_tool.core import jar_processor_preview
    assert jar_processor_preview is not None


def test_kubejs_translator_import():
    """Verify kubejs_translator can be imported."""
    from translation_tool.core import kubejs_translator
    assert kubejs_translator is not None


def test_kubejs_translator_clean_import():
    """Verify kubejs_translator_clean can be imported."""
    from translation_tool.core import kubejs_translator_clean
    assert kubejs_translator_clean is not None


def test_kubejs_translator_io_import():
    """Verify kubejs_translator_io can be imported."""
    from translation_tool.core import kubejs_translator_io
    assert kubejs_translator_io is not None


def test_kubejs_translator_paths_import():
    """Verify kubejs_translator_paths can be imported."""
    from translation_tool.core import kubejs_translator_paths
    assert kubejs_translator_paths is not None


def test_lang_merger_import():
    """Verify lang_merger can be imported."""
    from translation_tool.core import lang_merger
    assert lang_merger is not None


def test_lang_merge_content_import():
    """Verify lang_merge_content can be imported."""
    from translation_tool.core import lang_merge_content
    assert lang_merge_content is not None


def test_lang_merge_content_copy_import():
    """Verify lang_merge_content_copy can be imported."""
    from translation_tool.core import lang_merge_content_copy
    assert lang_merge_content_copy is not None


def test_lang_merge_content_patchers_import():
    """Verify lang_merge_content_patchers can be imported."""
    from translation_tool.core import lang_merge_content_patchers
    assert lang_merge_content_patchers is not None


def test_lang_merge_pending_import():
    """Verify lang_merge_pending can be imported."""
    from translation_tool.core import lang_merge_pending
    assert lang_merge_pending is not None


def test_lang_merge_pipeline_import():
    """Verify lang_merge_pipeline can be imported."""
    from translation_tool.core import lang_merge_pipeline
    assert lang_merge_pipeline is not None


def test_lang_merge_zip_io_import():
    """Verify lang_merge_zip_io can be imported."""
    from translation_tool.core import lang_merge_zip_io
    assert lang_merge_zip_io is not None


def test_lm_config_rules_import():
    """Verify lm_config_rules can be imported."""
    from translation_tool.core import lm_config_rules
    assert lm_config_rules is not None


def test_lm_translator_import():
    """Verify lm_translator can be imported."""
    from translation_tool.core import lm_translator
    assert lm_translator is not None


def test_lm_translator_main_import():
    """Verify lm_translator_main can be imported."""
    from translation_tool.core import lm_translator_main
    assert lm_translator_main is not None


def test_lm_translator_scan_import():
    """Verify lm_translator_scan can be imported."""
    from translation_tool.core import lm_translator_scan
    assert lm_translator_scan is not None


def test_lm_translator_shared_import():
    """Verify lm_translator_shared can be imported."""
    from translation_tool.core import lm_translator_shared
    assert lm_translator_shared is not None


def test_lm_translator_shared_cache_import():
    """Verify lm_translator_shared_cache can be imported."""
    from translation_tool.core import lm_translator_shared_cache
    assert lm_translator_shared_cache is not None


def test_lm_translator_shared_loop_import():
    """Verify lm_translator_shared_loop can be imported."""
    from translation_tool.core import lm_translator_shared_loop
    assert lm_translator_shared_loop is not None


def test_lm_translator_shared_preview_import():
    """Verify lm_translator_shared_preview can be imported."""
    from translation_tool.core import lm_translator_shared_preview
    assert lm_translator_shared_preview is not None


def test_lm_translator_shared_recording_import():
    """Verify lm_translator_shared_recording can be imported."""
    from translation_tool.core import lm_translator_shared_recording
    assert lm_translator_shared_recording is not None


def test_md_translation_assembly_import():
    """Verify md_translation_assembly can be imported."""
    from translation_tool.core import md_translation_assembly
    assert md_translation_assembly is not None


def test_md_translation_progress_import():
    """Verify md_translation_progress can be imported."""
    from translation_tool.core import md_translation_progress
    assert md_translation_progress is not None


def test_md_translation_stats_import():
    """Verify md_translation_stats can be imported."""
    from translation_tool.core import md_translation_stats
    assert md_translation_stats is not None


def test_md_translation_steps_import():
    """Verify md_translation_steps can be imported."""
    from translation_tool.core import md_translation_steps
    assert md_translation_steps is not None


def test_output_bundler_import():
    """Verify output_bundler can be imported."""
    from translation_tool.core import output_bundler
    assert output_bundler is not None


def test_ftb_translator_import():
    """Verify ftb_translator can be imported."""
    from translation_tool.core import ftb_translator
    assert ftb_translator is not None


def test_ftb_translator_clean_import():
    """Verify ftb_translator_clean can be imported."""
    from translation_tool.core import ftb_translator_clean
    assert ftb_translator_clean is not None


def test_ftb_translator_export_import():
    """Verify ftb_translator_export can be imported."""
    from translation_tool.core import ftb_translator_export
    assert ftb_translator_export is not None


def test_ftb_translator_template_import():
    """Verify ftb_translator_template can be imported."""
    from translation_tool.core import ftb_translator_template
    assert ftb_translator_template is not None
