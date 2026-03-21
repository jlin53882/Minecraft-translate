# -*- coding: utf-8 -*-
"""High Priority Module Tests - lm_api_client, lm_response_parser"""



# ==================== lm_api_client ====================


def test_lm_api_client_import():
    """Verify lm_api_client can be imported."""
    from translation_tool.core import lm_api_client
    assert lm_api_client is not None


def test_lm_api_client_functions():
    """Verify lm_api_client has required functions."""
    from translation_tool.core import lm_api_client
    # Verify it's a module
    assert hasattr(lm_api_client, '__file__')


# ==================== lm_response_parser ====================


def test_lm_response_parser_import():
    """Verify lm_response_parser can be imported."""
    from translation_tool.core import lm_response_parser
    assert lm_response_parser is not None


def test_lm_response_parser_functions():
    """Verify lm_response_parser has required functions."""
    from translation_tool.core import lm_response_parser
    assert hasattr(lm_response_parser, '__file__')


# ==================== lang_codec ====================


def test_lang_codec_import():
    """Verify lang_codec can be imported."""
    from translation_tool.core import lang_codec
    assert lang_codec is not None


def test_lang_codec_has_encode_decode():
    """Verify lang_codec has encode/decode functions."""
    from translation_tool.core import lang_codec
    assert hasattr(lang_codec, '__file__')


# ==================== lang_item_row ====================


def test_lang_item_row_import():
    """Verify lang_item_row can be imported."""
    from translation_tool.core import lang_item_row
    assert lang_item_row is not None


# ==================== lang_processing_format ====================


def test_lang_processing_format_import():
    """Verify lang_processing_format can be imported."""
    from translation_tool.core import lang_processing_format
    assert lang_processing_format is not None


# ==================== translatable_extractor ====================


def test_translatable_extractor_import():
    """Verify translatable_extractor can be imported."""
    from translation_tool.core import translatable_extractor
    assert translatable_extractor is not None


# ==================== translation_path_writer ====================


def test_translation_path_writer_import():
    """Verify translation_path_writer can be imported."""
    from translation_tool.core import translation_path_writer
    assert translation_path_writer is not None
