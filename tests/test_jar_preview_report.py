from pathlib import Path

from translation_tool.core.jar_processor_preview import ExtractionSummary, generate_preview_report


def test_extraction_summary_counts_success_warning_failure():
    s = ExtractionSummary()
    s.add_success('a.jar', 2)
    s.add_warning('b.jar', 'none')
    s.add_failure('c.jar', 'boom')

    summary = s.get_summary()
    assert summary['success_count'] == 1
    assert summary['warning_count'] == 1
    assert summary['failure_count'] == 1


def test_generate_preview_report_writes_markdown_file(tmp_path: Path):
    path = generate_preview_report(
        {
            'total_jars': 2,
            'preview_results': [{'jar': 'a.jar', 'files': ['assets/demo/lang/en_us.json'], 'count': 1, 'size_mb': 1.2}],
            'total_files': 1,
            'total_size_mb': 0.01,
        },
        'lang',
        str(tmp_path),
    )

    report = Path(path)
    assert report.exists()
    text = report.read_text(encoding='utf-8')
    assert '# JAR 提取預覽報告 - LANG' in text
    assert 'assets/demo/lang/en_us.json' in text
