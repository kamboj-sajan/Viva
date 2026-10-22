"""Chunk line numbers must match the source exactly: a wrong line number is a wrong citation."""

import pathlib
import textwrap

import pytest

from viva.chunking import _CLOSER, MAX_LINES, WINDOW, _is_comment, chunk_file, symbols
from viva.files import decode

ROOT = pathlib.Path(__file__).resolve().parents[1]


def check(text: str, language: str | None):
    chunks = chunk_file(text, language)
    lines = text.split("\n")
    prev_end = 0
    covered: set[int] = set()
    for c in chunks:
        assert 1 <= c.start <= c.end <= len(lines), c
        assert c.text == "\n".join(lines[c.start - 1:c.end]), c
        assert c.start > prev_end, "chunks overlap or are out of order"
        assert c.end - c.start + 1 <= MAX_LINES
        prev_end = c.end
        covered.update(range(c.start, c.end + 1))
    for i, line in enumerate(lines, 1):
        if i not in covered:
            assert not line.strip() or _CLOSER.match(line) or _is_comment(line, True) or _is_comment(line, False), (
                f"line {i} dropped: {line!r}"
            )
    return chunks


def line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.split("\n"), 1) if needle in line)


def by_symbol(chunks, symbol):
    return next(c for c in chunks if c.symbol == symbol)


PY = textwrap.dedent('''\
    """Module docstring."""
    import os
    from typing import Any

    RETRIES = 3


    # A friendly class.
    # It greets people.
    class Greeter:
        def __init__(self, name: str):
            self.name = name

        @staticmethod
        def greet(name):
            return f"hi {name}"


    @decorator(with_args=True)
    def outer(x):
        def inner(y):
            return y * 2
        return inner(x)


    if __name__ == "__main__":
        print(outer(RETRIES))
''')


def test_python_definitions_have_exact_lines():
    chunks = check(PY, "python")
    greeter = by_symbol(chunks, "Greeter")
    assert greeter.kind == "class"
    assert greeter.start == line_of(PY, "# A friendly class.")  # doc comment attached
    assert greeter.end == line_of(PY, 'return f"hi {name}"')
    outer = by_symbol(chunks, "outer")
    assert outer.start == line_of(PY, "@decorator")
    assert outer.end == line_of(PY, "return inner(x)")
    assert symbols(chunks) == ["Greeter", "outer"]


def test_trailing_newline_does_not_change_chunks():
    with_nl = check(PY, "python")
    without = check(PY.rstrip("\n"), "python")
    assert [(c.start, c.end) for c in with_nl] == [(c.start, c.end) for c in without]


def test_big_class_is_split_into_methods():
    methods = "\n".join(
        f"    def method_{i}(self, x):\n" + "".join(f"        x = x + {j}\n" for j in range(8)) + "        return x\n"
        for i in range(15)
    )
    src = "import math\n\n\nclass Big:\n    \"\"\"Many methods.\"\"\"\n\n" + methods
    chunks = check(src, "python")
    m7 = by_symbol(chunks, "Big.method_7")
    assert m7.kind == "function"
    assert m7.start == line_of(src, "def method_7(")
    assert m7.end == line_of(src, "def method_8(") - 2
    assert "Big.method_0" in symbols(chunks)


def test_huge_function_without_members_is_windowed_exactly():
    body = "".join(f"    total += {i}\n" for i in range(300))
    src = f"def huge():\n    total = 0\n{body}    return total\n"
    chunks = check(src, "python")
    assert len(chunks) > 1
    assert all(c.end - c.start + 1 <= max(WINDOW, MAX_LINES) for c in chunks)
    assert chunks[0].start == 1 and chunks[-1].end == line_of(src, "return total")


def test_form_feed_does_not_shift_line_numbers():
    # str.splitlines() would treat \x0c as a line break; GitHub does not.
    src = "def a():\n    x = 1\n    return x\n\x0c\ndef b():\n    y = 2  # \x0c inline\n    return y\n"
    chunks = check(src, "python")
    assert by_symbol(chunks, "b").start == 5


def test_crlf_is_normalised_before_chunking():
    raw = b"def a():\r\n    x = 1\r\n    return x\r\n"
    text = decode(raw)
    assert text == "def a():\n    x = 1\n    return x\n"
    assert by_symbol(check(text, "python"), "a").end == 3


def test_multibyte_characters_do_not_shift_lines():
    src = "# héllo wörld — ünïcode ✓\n# 日本語のコメント\n\ndef after_unicode(a):\n    b = a\n    return b\n"
    assert by_symbol(check(src, "python"), "after_unicode").start == 4


TS = textwrap.dedent("""\
    import { db } from "./db";

    /** Fetch a user. */
    export async function getUser(id: string) {
      const row = await db.query(id);
      return row;
    }

    export class Cache<T> {
      private items = new Map<string, T>();
      get(key: string): T | undefined {
        return this.items.get(key);
      }
    }

    export const handler = async (req: Request) => {
      const user = await getUser(req.id);
      return user;
    };
""")


def test_typescript():
    chunks = check(TS, "typescript")
    get_user = by_symbol(chunks, "getUser")
    assert get_user.start == line_of(TS, "/** Fetch a user. */")
    assert get_user.end == line_of(TS, "return row;") + 1
    assert by_symbol(chunks, "Cache").start == line_of(TS, "export class Cache")
    assert by_symbol(chunks, "handler").end == line_of(TS, "};")


GO = textwrap.dedent("""\
    package store

    import (
    \t"context"
    \t"errors"
    )

    // Store keeps items.
    type Store struct {
    \titems map[string]string
    \tmu    sync.Mutex
    }

    func (s *Store) Get(ctx context.Context, k string) (string, error) {
    \tv, ok := s.items[k]
    \tif !ok {
    \t\treturn "", errors.New("missing")
    \t}
    \treturn v, nil
    }
""")


def test_go():
    chunks = check(GO, "go")
    assert by_symbol(chunks, "Store").start == line_of(GO, "// Store keeps items.")
    get = by_symbol(chunks, "Get")
    assert (get.start, get.end) == (line_of(GO, "func (s *Store) Get"), len(GO.rstrip("\n").split("\n")))


def test_c_function_names():
    src = "#include <stdio.h>\n\nstatic int add(int a, int b) {\n    int c = a + b;\n    return c;\n}\n"
    assert by_symbol(check(src, "c"), "add").start == 3


VERILOG = textwrap.dedent("""\
    `timescale 1ns/1ps
    // Simple counter.
    module counter #(parameter W = 8) (
      input  wire clk,
      input  wire rst,
      output reg [W-1:0] q
    );
      always @(posedge clk) begin
        if (rst) q <= 0;
        else     q <= q + 1;
      end

      function [W-1:0] inc;
        input [W-1:0] x;
        inc = x + 1;
      endfunction
    endmodule

    module top(input clk);
      wire [3:0] q;
      counter #(.W(4)) u0 (.clk(clk), .rst(1'b0), .q(q));
    endmodule
""")


def test_verilog_modules():
    chunks = check(VERILOG, "verilog")
    counter = by_symbol(chunks, "counter")
    assert counter.start == line_of(VERILOG, "// Simple counter.")
    assert counter.end == line_of(VERILOG, "endmodule")
    top = by_symbol(chunks, "top")
    assert (top.start, top.end) == (line_of(VERILOG, "module top"), len(VERILOG.rstrip("\n").split("\n")))


def test_big_verilog_module_splits_on_always_blocks():
    always = "\n".join(
        f"  always @(posedge clk) begin\n" + "".join(f"    r{i}_{j} <= d{j};\n" for j in range(10)) + "  end\n"
        for i in range(12)
    )
    src = "module big(input clk);\n  reg a;\n\n" + always + "\n  function automatic int f(int x);\n    return x;\n  endfunction\nendmodule\n"
    chunks = check(src, "verilog")
    assert len(chunks) > 3
    first_always = line_of(src, "always @(posedge clk)")
    assert any(c.start == first_always and c.symbol == "big.always" for c in chunks)
    f = by_symbol(chunks, "big.f")
    assert f.start == line_of(src, "function automatic int f")


SV = textwrap.dedent("""\
    package pkg;
      typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
    endpackage

    class Driver extends Base;
      virtual task run();
        forever begin
          @(posedge vif.clk);
          drive();
        end
      endtask

      extern function void drive();
    endclass
""")


def test_systemverilog_class_and_package():
    chunks = check(SV, "systemverilog")
    assert by_symbol(chunks, "pkg").end == line_of(SV, "endpackage")
    driver = by_symbol(chunks, "Driver")
    assert (driver.start, driver.end) == (line_of(SV, "class Driver"), line_of(SV, "endclass"))


def test_unknown_language_falls_back_to_windows():
    src = "\n".join(f"local x{i} = {i}" for i in range(200)) + "\n"
    chunks = check(src, "lua")
    assert all(c.kind == "window" and c.end - c.start + 1 <= WINDOW for c in chunks)
    assert chunks[0].start == 1 and chunks[-1].end == 200


@pytest.mark.parametrize("path", sorted((ROOT / "viva").glob("*.py")), ids=lambda p: p.name)
def test_invariants_hold_on_real_files(path):
    check(path.read_text(), "python")
