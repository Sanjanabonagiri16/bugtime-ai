from app.services.chunking import chunk_python, chunk_markdown


def test_python_chunk_line_accuracy():
    source = "\n".join(
        [
            "import os",
            "",
            "def greet(name):",
            "    return f'hi {name}'",
            "",
            "class Thing:",
            "    def run(self):",
            "        return 1",
        ]
    )
    chunks = chunk_python(source)
    assert chunks
    for chunk in chunks:
        lines = source.splitlines()
        sliced = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
        assert chunk.content == sliced
        assert chunk.start_line <= chunk.end_line


def test_markdown_heading_chunks():
    text = "# Title\n\nIntro\n\n## Details\n\nMore text\n"
    chunks = chunk_markdown(text)
    assert any(c.symbol_name == "Title" for c in chunks)
    assert any(c.symbol_name == "Details" for c in chunks)
