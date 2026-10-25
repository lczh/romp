#!/usr/bin/env python3
"""Each schema version names one change, and the newest note names the version in force.

Three numbers stamp what the judges and the parse store on disk: PLACEMENTS_V (the placements
store's identity schema, kernel/judge.py), the captions tasks memo's "v" (kernel/judge.py), and
_ASM_CKPT_V (the assembly document, kernel/event_model.py). A change that moves what they cover
bumps the number and writes a dated note for the new value beside it.

Two changes opened from the same main both take the same next number. When the second one merges,
git merges their identical constant lines cleanly and leaves only the two notes in conflict, so a
resolution that keeps both notes ships the second change with no bump of its own: stores already
stamped with the shared number never re-seal, and documents already at it restore without being
refused, so a restored parse can differ from a whole parse. The pins elsewhere check only the
number, so they stay green. These tests read the source and fail on that resolution: no version
has two notes, and the newest note names the number the code uses. The later change takes the
next number, in its note, its constant, and the pins that read it."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _lines(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read().splitlines()


def _block_after(lines, prefix):
    """The constant's own line and the comment lines that follow it, up to the next code line."""
    starts = [i for i, ln in enumerate(lines) if ln.startswith(prefix)]
    assert len(starts) == 1, "expected one line starting with %r, found %d" % (prefix, len(starts))
    i = starts[0]
    block = [lines[i]]
    for ln in lines[i + 1:]:
        if not ln.lstrip().startswith("#"):
            break
        block.append(ln)
    return block


def _check(case, what, current, notes):
    seen = {}
    for v in notes:
        seen[v] = seen.get(v, 0) + 1
    twice = sorted(v for v, n in seen.items() if n > 1)
    case.assertEqual(twice, [], "%s: version(s) %s carry more than one note, so two changes took the same number. "
                     "Give the later change the next number: its note, the constant and the pins that read it."
                     % (what, twice))
    case.assertTrue(notes, "%s: no version notes found beside the constant" % what)
    case.assertEqual(max(notes), current, "%s: the code uses version %d but the newest note names %d. A bump writes "
                     "its note, and a note names the version the code stamps." % (what, current, max(notes)))


class SchemaVersionNotes(unittest.TestCase):

    def test_placements_version_notes(self):
        block = _block_after(_lines("kernel/judge.py"), "PLACEMENTS_V = ")
        current = int(re.match(r"PLACEMENTS_V = (\d+)", block[0]).group(1))
        notes = [int(m.group(1)) for ln in block[1:]
                 for m in [re.match(r"#\s+v(\d+) \((?:T\w+, )?\d{4}-\d{2}-\d{2}", ln)] if m]
        _check(self, "PLACEMENTS_V", current, notes)

    def test_captions_memo_version_notes(self):
        lines = _lines("kernel/judge.py")
        reads = [i for i, ln in enumerate(lines) if 'o.get("capKey") == cap_key and o.get("v") == ' in ln]
        self.assertEqual(len(reads), 1, "expected one captions tasks memo read check")
        i = reads[0]
        current = int(re.search(r'o\.get\("v"\) == (\d+):', lines[i]).group(1))
        writes = [int(m.group(1)) for ln in lines for m in [re.search(r'"capKey": cap_key, "v": (\d+), "tasks"', ln)] if m]
        self.assertEqual(writes, [current], "the captions tasks memo is read at version %d but written at %s"
                         % (current, writes))
        comment = [lines[i].split("#", 1)[1]]
        for ln in lines[i + 1:]:
            if not ln.lstrip().startswith("#"):
                break
            comment.append(ln)
        notes = [int(m.group(1)) for ln in comment for m in re.finditer(r"(?<!\w)v(\d+) = ", ln)]
        _check(self, "the captions tasks memo", current, notes)

    def test_assembly_document_version_notes(self):
        block = _block_after(_lines("kernel/event_model.py"), "_ASM_CKPT_V = ")
        current = int(re.match(r"_ASM_CKPT_V = (\d+)", block[0]).group(1))
        notes = [int(m.group(1)) for ln in block[1:] for m in [re.match(r"#\s{20,}(\d+): ", ln)] if m]
        _check(self, "_ASM_CKPT_V", current, notes)


if __name__ == "__main__":
    unittest.main()
