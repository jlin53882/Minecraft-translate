from app import services


def test_qc_services_module_exports_expected_facade_names():
    exported = set(services.__all__)
    assert exported == {
        'run_untranslated_check_service',
        'run_variant_compare_service',
        'run_english_residue_check_service',
        'run_variant_compare_tsv_service',
    }


def test_untranslated_service_wraps_generator_updates(monkeypatch):
    monkeypatch.setattr(
        services,
        'check_untranslated_generator',
        lambda en, tw, out: iter([{'log': 'ok', 'progress': 0.5}]),
    )
    monkeypatch.setattr(services.GLOBAL_LOG_LIMITER, 'filter', lambda update: update)

    rows = list(services.run_untranslated_check_service('a', 'b', 'c'))

    assert rows == [{'log': 'ok', 'progress': 0.5}]


def test_variant_compare_tsv_service_yields_error_update_on_exception(monkeypatch):
    def boom(tsv, out):
        raise RuntimeError('boom')
        yield  # pragma: no cover

    monkeypatch.setattr(services, 'compare_variants_tsv_generator', boom)

    rows = list(services.run_variant_compare_tsv_service('a.tsv', 'out.csv'))

    assert rows
    assert rows[0]['error'] is True
    assert 'TSV 簡繁差異比較失敗' in rows[0]['log']
