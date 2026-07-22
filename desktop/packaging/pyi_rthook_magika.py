"""PyInstaller runtime hook: stub `magika` so markitdown runs without onnxruntime.

markitdown hard-depends on magika, which loads an ONNX model via onnxruntime
(tens of MB) purely to sniff a file's type from its content. OpenKB only ever
converts real files with correct extensions, so markitdown's extension/mimetype
based stream-info guessing is sufficient and magika's content sniffing is
redundant. This hook installs a lightweight in-memory `magika` module before
markitdown imports it; `identify_stream` returns a non-"ok" status, which makes
markitdown fall back to the extension guess. Paired with
`--exclude-module magika --exclude-module onnxruntime` at build time, this drops
onnxruntime (and magika's model) from the packaged sidecar entirely.
"""

import sys
import types


class _Result:
    # Any status other than "ok" makes markitdown use its extension-based guess.
    status = "stub"


class Magika:
    def __init__(self, *args, **kwargs):
        pass

    def identify_stream(self, *args, **kwargs):
        return _Result()

    def identify_bytes(self, *args, **kwargs):
        return _Result()

    def identify_path(self, *args, **kwargs):
        return _Result()


_stub = types.ModuleType("magika")
_stub.Magika = Magika
sys.modules.setdefault("magika", _stub)
