"""Synthetic evidence only: no browser, native capture, or image quality claims."""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ui_capture_evidence as capture


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.native = self.root / 'native.png'
        self.image = self.root / 'preserved.png'
        self.png = capture.PNG + b'synthetic signature fixture, not decoded pixels'
        self.native.write_bytes(self.png)
        self.image.write_bytes(self.png)
        self.envelope = dict(id='receipt-success', ok=True,
            result=dict(format='png', path=str(self.native), expiresAt='2000-01-01T00:00:00Z'),
            _meta=dict(runtimeId='runtime-example'))
        self.wrapper = dict(command=['/Applications/Orca.app/Contents/Resources/bin/orca',
            'full-screenshot', '--page', 'page-example', '--json'], time='capture-example',
            exit=0, stdout=json.dumps(self.envelope), stderr='')
        self.receipt = self.root / 'receipt.json'
        self.row = dict(expected_receipt_id='receipt-success', expected_page_id='page-example',
            receipt_path=str(self.receipt), native_image_path=str(self.native),
            image_path=str(self.image), image_sha256=hashlib.sha256(self.png).hexdigest())
        self.manifest = self.root / 'manifest.json'
        self.write()

    def write(self):
        self.receipt.write_text(json.dumps(self.wrapper))
        self.manifest.write_text(json.dumps(dict(version=1, captures=[self.row])))

    def cli(self, args=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = capture.main(args if args is not None else ['--manifest', str(self.manifest)])
        return code, json.loads(output.getvalue())

    def test_bound_coverage_single_read_and_standalone_compatibility(self):
        with patch.object(capture.Inputs, 'read', autospec=True,
                          side_effect=capture.Inputs.read) as read:
            self.assertEqual(capture.check_manifest(str(self.manifest),
                expected_image_refs=[str(self.image)])['status'], 'pass')
        self.assertEqual(sum(call.args[1] == str(self.manifest) for call in read.call_args_list), 1)
        for refs in ([], [str(self.native)], [str(self.image), str(self.native)],
                     [str(self.image)] * 2, [None], [1], [''], 'bad', {}):
            with self.subTest(refs=refs), self.assertRaises(capture.Invalid):
                capture.check_manifest(str(self.manifest), expected_image_refs=refs)
        self.manifest.write_text(json.dumps(dict(version=1, captures=[self.row, self.row])))
        self.assertEqual(self.cli()[0], 0)  # Original standalone behavior stays intact.
        with self.assertRaisesRegex(capture.Invalid, 'duplicate_capture_coverage'):
            capture.check_manifest(str(self.manifest), expected_image_refs=[str(self.image)])

    def test_bound_multiple_captures_and_extra_or_ambiguous_rows(self):
        other = self.root / 'other.png'; other.write_bytes(self.png)
        native = self.root / 'other-native.png'; native.write_bytes(self.png)
        wrapper = dict(self.wrapper, stdout=json.dumps(dict(self.envelope,
            id='other-receipt', result=dict(format='png', path=str(native)))))
        receipt = self.root / 'other-receipt.json'; receipt.write_text(json.dumps(wrapper))
        second = dict(self.row, image_path=str(other), native_image_path=str(native),
            expected_receipt_id='other-receipt', receipt_path=str(receipt))
        def write(row):
            self.manifest.write_text(json.dumps(dict(version=1, captures=[self.row, row])))
        write(second)
        refs = [str(self.image), str(other)]
        self.assertEqual(capture.check_manifest(str(self.manifest), expected_image_refs=refs)['status'], 'pass')
        with self.assertRaisesRegex(capture.Invalid, 'capture_coverage_mismatch'):
            capture.check_manifest(str(self.manifest), expected_image_refs=refs[:1])
        for key in ('image_path', 'native_image_path', 'expected_receipt_id'):
            write(dict(second, **{key: self.row[key]}))
            with self.subTest(key=key), self.assertRaisesRegex(capture.Invalid, 'duplicate_capture_coverage'):
                capture.check_manifest(str(self.manifest), expected_image_refs=refs)

    def test_both_commands_success_and_expiry_is_informational(self):
        for command in ('screenshot', 'full-screenshot'):
            self.wrapper['command'][1] = command
            self.write()
            code, result = self.cli()
            self.assertEqual(code, 0)
            self.assertEqual(result['scope'], 'metadata_consistency_only')

    def test_motivating_wrong_receipt_id_equal_image_bytes(self):
        self.row['expected_receipt_id'] = 'receipt-stated-in-old-report'
        self.write()
        code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['captures'][0]['code'], 'receipt_id_mismatch')
        self.assertEqual(self.native.read_bytes(), self.image.read_bytes())

    def test_page_path_hash_and_png_mismatch(self):
        for field, value in [('expected_page_id', 'wrong-page'),
                             ('native_image_path', str(self.image)), ('image_sha256', '0'*64)]:
            old = self.row[field]
            self.row[field] = value
            self.write()
            self.assertEqual(self.cli()[0], 1)
            self.row[field] = old
        for path in (self.native, self.image):
            path.write_bytes(b'not png')
            self.write()
            self.assertEqual(self.cli()[1]['captures'][0]['code'], 'invalid_png_signature')
            path.write_bytes(self.png)

    def test_missing_native_even_if_expired_and_preserved_present(self):
        self.native.unlink()
        self.assertEqual(self.cli()[0], 1)

    def test_changed_images_fail(self):
        for path in (self.native, self.image):
            path.write_bytes(self.png + b'changed')
            self.assertEqual(self.cli()[0], 1)
            path.write_bytes(self.png)

    def test_command_ambiguity_and_failed_envelopes(self):
        original = list(self.wrapper['command'])
        for argv in [original + ['--page', 'another'], original + ['--json'],
                     original[:2] + ['--page=page-example', '--json'],
                     ['sh'] + original[1:], original[:1] + ['eval'] + original[2:]]:
            self.wrapper['command'] = argv
            self.write()
            self.assertEqual(self.cli()[0], 1)
        self.wrapper['command'] = original
        for field, value in [('exit', True), ('exit', 1), ('stderr', 'private error')]:
            old = self.wrapper[field]; self.wrapper[field] = value
            self.write()
            self.assertEqual(self.cli()[0], 1)
            self.wrapper[field] = old
        self.envelope['ok'] = False
        self.wrapper['stdout'] = json.dumps(self.envelope)
        self.write()
        self.assertEqual(self.cli()[0], 1)

    def test_strict_json_at_each_layer(self):
        for raw in ['{"version":1,"version":1,"captures":[]}', '{',
                    '{"version":NaN,"captures":[]}', '{"version":1e999,"captures":[]}',
                    '[]', '{"version":true,"captures":[]}']:
            self.manifest.write_text(raw)
            self.assertEqual(self.cli()[1]['status'], 'invalid')
        self.write()
        for raw in ['{"ok":true,"ok":true}', '{"ok":Infinity}', '{', 'null']:
            self.wrapper['stdout'] = raw
            self.write()
            self.assertEqual(self.cli()[0], 1)
        self.receipt.write_text('{"exit":0,"exit":0}')
        self.assertEqual(self.cli()[0], 1)

    def test_schema_bounds(self):
        for value in [[], [self.row]*33, 'bad', [dict(self.row, extra='bad')],
                      [dict(self.row, image_path=None)], [dict(self.row, image_sha256='A'*64)]]:
            self.manifest.write_text(json.dumps(dict(version=1, captures=value)))
            self.assertEqual(self.cli()[0], 1)
        self.manifest.write_text(' '* (capture.JSON_LIMIT + 1))
        self.assertEqual(self.cli()[0], 1)
        self.write()
        with patch.object(capture, 'IMAGE_LIMIT', len(self.png)-1):
            self.assertEqual(self.cli()[0], 1)
        with patch.object(capture, 'TOTAL_LIMIT', self.manifest.stat().st_size):
            self.assertEqual(self.cli()[0], 1)

    def test_nonfiles_links_and_parent_traversal(self):
        paths = [self.root, self.root / 'link', self.root / 'fifo',
                 self.root / 'linked-parent' / self.native.name,
                 str(self.root) + '/../' + self.root.name + '/native.png']
        (self.root/'link').symlink_to(self.native)
        (self.root/'linked-parent').symlink_to(self.root, target_is_directory=True)
        os.mkfifo(self.root/'fifo')
        for path in paths:
            self.row['image_path'] = str(path)
            self.write()
            self.assertEqual(self.cli()[0], 1)

    def test_receipt_path_never_opened(self):
        self.envelope['result']['path'] = '/must-not-open/secret.png'
        self.wrapper['stdout'] = json.dumps(self.envelope)
        self.write()
        with patch.object(capture, 'open_artifact', wraps=capture.open_artifact) as opened:
            self.assertEqual(self.cli()[0], 1)
            self.assertNotIn(Path('/must-not-open/secret.png'), [x.args[0] for x in opened.call_args_list])

    def test_race_content_replacement_and_final_recheck(self):
        real = capture.Inputs.unchanged
        for mutation in ('content', 'replacement'):
            def race(inputs, path, expected):
                if path == self.native:
                    if mutation == 'replacement':
                        replacement = self.root/'replacement'
                        replacement.write_bytes(self.png)
                        replacement.replace(path)
                    else:
                        path.write_bytes(self.png + b'raced')
                return real(inputs, path, expected)
            with patch.object(capture.Inputs, 'unchanged', race):
                self.assertEqual(self.cli()[0], 1)
            self.native.write_bytes(self.png)
        real_verify = capture.Inputs.verify
        def final_race(inputs):
            self.manifest.write_text('{}')
            real_verify(inputs)
        with patch.object(capture.Inputs, 'verify', final_race):
            self.assertEqual(self.cli()[1]['status'], 'invalid')

    def test_macos_aliases_without_resolving_arbitrary_links(self):
        with patch.object(capture.sys, 'platform', 'darwin'):
            self.assertEqual(capture.canonical_path('/var/folders/example.png'),
                             Path('/private/var/folders/example.png'))
            self.assertEqual(capture.canonical_path('/tmp/example.png'), Path('/private/tmp/example.png'))
        if sys.platform == 'darwin':
            self.row['native_image_path'] = str(self.native).replace('/private/var/', '/var/')
            self.write()
            self.assertEqual(self.cli()[0], 0)

    def test_no_execution_network_or_source_mutation_and_import_safe(self):
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('subprocess')), \
             patch.object(socket, 'socket', side_effect=AssertionError('network')):
            with patch.object(os, 'open', side_effect=AssertionError('import IO')):
                importlib.reload(capture)
            self.assertEqual(self.cli()[0], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.iterdir()})

    def test_cli_errors_generic_json_no_tracebacks_or_private_dump(self):
        for args in [[], ['--unexpected'], ['--manifest', '/missing/private-example'],
                     ['--manifest', str(self.manifest), '--other']]:
            code, result = self.cli(args)
            self.assertEqual(code, 1)
            self.assertEqual(result['code'], 'invalid_or_unavailable_input')
            self.assertNotIn('private-example', json.dumps(result))

    def test_real_cli_process_success_failure_and_import(self):
        script = Path(capture.__file__)
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        for expected in (0, 1):
            if expected:
                self.row['expected_receipt_id'] = 'receipt-wrong'
                self.write()
            result = subprocess.run([sys.executable, '-B', str(script), '--manifest', str(self.manifest)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected)
            self.assertEqual(json.loads(result.stdout)['status'], 'fail' if expected else 'pass')
            self.assertEqual(result.stderr, '')
        self.row['expected_receipt_id'] = 'receipt-success'; self.write()
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.iterdir()})

    def test_wrapper_and_response_schema_types(self):
        original = json.loads(json.dumps(self.envelope))
        for key, value in [('id', 1), ('ok', 1), ('result', []), ('_meta', []),
                           ('_meta', {'runtimeId': False})]:
            self.envelope = dict(original, **{key: value})
            self.wrapper['stdout'] = json.dumps(self.envelope)
            self.write()
            self.assertEqual(self.cli()[0], 1)
        self.envelope = original
        for result in [dict(format='jpeg', path=str(self.native)),
                       dict(format='png', path=str(self.native), expiresAt=False),
                       dict(format='png', path=str(self.native), extra='unknown')]:
            self.envelope['result'] = result
            self.wrapper['stdout'] = json.dumps(self.envelope)
            self.write()
            self.assertEqual(self.cli()[0], 1)


if __name__ == '__main__':
    unittest.main()
