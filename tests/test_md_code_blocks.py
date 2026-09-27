"""Task 10 / C8：Markdown 程式碼區塊不送翻譯。"""

from translation_tool.plugins.md.md_extract_qa import extract_blocks

MD = """# Title

Some intro text.

```js
// # not a heading
const x = "do not translate";
```

~~~~
~~~ still code
~~~~

After code.
"""


def test_code_blocks_are_not_extracted():
    blocks = extract_blocks(MD, "a.md", "all")
    texts = [b.text for b in blocks]
    assert texts == ["# Title", "Some intro text.", "After code."]
    # 行號仍對應原檔，注入時只替換這些範圍，程式碼原樣保留
    assert blocks[-1].start_line == MD.splitlines().index("After code.") + 1
