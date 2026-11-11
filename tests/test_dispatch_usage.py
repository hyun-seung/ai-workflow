import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_record as nr
from dispatch_usage import ROLE_IDS
from test_native_record import event, context, assigned


def boundary(kind, turn='turn_one'):
    return event('event_msg', dict(type=kind, turn_id=turn))


def count(total=(100, 20, 70, 5), last=None):
    def values(parts):
        i, o, c, r = parts
        result = dict(input_tokens=i, output_tokens=o, total_tokens=i + o)
        if c is not None: result['cached_input_tokens'] = c
        if r is not None: result['reasoning_output_tokens'] = r
        return result
    return event('event_msg', dict(type='token_count', info=dict(
        total_token_usage=values(total), last_token_usage=values(last or total))))


class DispatchUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.source = self.root / 'session.jsonl'
        self.meta = event('session_meta', dict(id='session_example', cwd=str(self.root),
                                             source='vscode', originator='orca_desktop'))
        self.first = [boundary('task_started'), context(), assigned(), count(), boundary('task_complete')]
        self.rows = [self.meta] + copy.deepcopy(self.first)

    def inspect(self, **changes):
        self.source.write_text(''.join(json.dumps(r) + '\n' for r in self.rows))
        args = dict(project=self.root, session_file=self.source, session_id='session_example',
                    task_id='task_example', dispatch_id='ctx_example', request_id='request_example', role='backend')
        args.update(changes)
        return nr.inspect_session(**args)

    def followup(self):
        return [boundary('task_started', 'turn_two'), context(), assigned('task_two', 'ctx_two'),
                count((300, 50, 200, 10), (200, 30, 130, 5)), boundary('task_complete', 'turn_two')]

    def test_fresh_closed_assignment_and_explicit_request_binding(self):
        record = self.inspect(); value = record['dispatch_usage']
        self.assertEqual(value['measurements']['total_tokens'], 120)
        self.assertTrue(value['task_attributed']); self.assertEqual(value['request_id'], 'request_example')
        self.assertEqual(value['role'], 'backend'); self.assertEqual(value['actor_id'], 'ctx_example')
        self.assertEqual(value['baseline']['method'], 'zero_reconciled_with_first_usage')
        self.assertEqual(value['interval']['end_line'], 6)
        self.assertEqual(record['usage']['scope'], 'session_cumulative')
        self.assertFalse(record['usage']['task_attributed'])
        self.assertNotIn('PRIVATE_', json.dumps(record))
        self.assertEqual(ROLE_IDS, set(json.loads((Path(__file__).parents[1] / 'workflow.json').read_text())['roles']))

    def test_reused_session_subtracts_prior_assignment_without_double_counting(self):
        original = self.inspect()['dispatch_usage']; self.rows += self.followup()
        first = self.inspect()['dispatch_usage']
        second = self.inspect(task_id='task_two', dispatch_id='ctx_two', request_id='request_two', role='frontend')['dispatch_usage']
        self.assertEqual(first, original)
        self.assertEqual(second['measurements'], dict(input_tokens=200, output_tokens=30,
                          cached_input_tokens=130, reasoning_output_tokens=5, total_tokens=230))
        self.assertEqual(first['measurements']['total_tokens'] + second['measurements']['total_tokens'], 350)
        self.assertNotEqual(first['measurement_key'], second['measurement_key'])
        rebound = self.inspect(request_id='another')['dispatch_usage']
        self.assertEqual(first['measurement_key'], rebound['measurement_key'])

    def test_duplicate_counter_notifications_not_added_again(self):
        self.rows.insert(-1, count()); self.rows.insert(-1, count())
        value = self.inspect()['dispatch_usage']
        self.assertEqual(value['measurements']['total_tokens'], 120)
        self.assertEqual(len(value['increments']), 1)

    def test_incomplete_and_conflicting_counters_not_attributed(self):
        original = copy.deepcopy(self.rows)
        cases = [count((80, 20, 70, 5), (1, 0, 0, 0)),
                 count((200, 30, 140, 7), (50, 10, 70, 2)),
                 count((200, 30, 140, 7), (100, 10, 69, 2))]
        for sample in cases:
            self.rows = copy.deepcopy(original); self.rows.insert(-1, sample)
            with self.subTest(sample=sample):
                value = self.inspect()['dispatch_usage']
                self.assertEqual(value['status'], 'unavailable'); self.assertFalse(value['task_attributed'])
                self.assertEqual(value['measurements'], {})
        self.rows = copy.deepcopy(original); self.rows[4]['payload']['info'].pop('last_token_usage')
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['last_usage_unavailable'])

    def test_native_terminal_must_match_and_close_the_assignment(self):
        original = copy.deepcopy(self.rows)
        variants = [original[:-1], original[:-1] + [boundary('turn_aborted')],
                    original[:-1] + [boundary('task_complete', 'different')],
                    original[:-1] + [boundary('task_started', 'overlap'), original[-1]]]
        for rows in variants:
            self.rows = rows
            with self.subTest(rows=rows):
                self.assertFalse(self.inspect()['dispatch_usage']['task_attributed'])

    def test_missing_data_is_not_zero_but_explicit_zero_is_measured(self):
        self.rows.pop(4)
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['missing_dispatch_usage'])
        self.rows.insert(4, count((0, 0, 0, 0)))
        self.assertEqual(self.inspect()['dispatch_usage']['measurements']['total_tokens'], 0)

    def test_optional_subcounts_not_assumed_zero(self):
        self.rows[4] = count((100, 20, None, None))
        value = self.inspect()['dispatch_usage']['measurements']
        self.assertEqual(value['total_tokens'], 120)
        self.assertIsNone(value['cached_input_tokens']); self.assertIsNone(value['reasoning_output_tokens'])
        self.rows[4] = count((0, 0, None, None))
        value = self.inspect()['dispatch_usage']['measurements']
        self.assertEqual(value['total_tokens'], 0)
        self.assertIsNone(value['cached_input_tokens']); self.assertIsNone(value['reasoning_output_tokens'])

    def test_initial_inherited_or_missing_baseline_is_unknown(self):
        self.rows[4] = count((200, 40, 140, 10), (100, 20, 70, 5))
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['counter_increment_mismatch'])
        self.rows[4] = count(); self.rows[0]['payload']['forked_from_id'] = 'parent'
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['initial_baseline_unknown'])
        self.meta['payload'].pop('forked_from_id')
        self.rows = [self.meta] + self.first[:3] + [self.first[-1]] + self.followup()
        self.assertEqual(self.inspect(task_id='task_two', dispatch_id='ctx_two')['dispatch_usage']['issues'], ['initial_baseline_unknown'])

    def test_bad_latest_baseline_not_replaced_by_earlier_valid_counter(self):
        self.rows.insert(-1, event('event_msg', dict(type='token_count', info=None)))
        self.rows += self.followup()
        self.assertEqual(self.inspect(task_id='task_two', dispatch_id='ctx_two')['dispatch_usage']['issues'], ['invalid_baseline'])

    def test_unbound_user_and_followup_turns_not_assigned_silently(self):
        self.rows.insert(-1, event('response_item', dict(role='user', content=[dict(type='input_text', text='PRIVATE_UNRELATED')])) )
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['unbound_user_message'])
        self.rows = [self.meta] + self.first + [boundary('task_started', 'unbound')]
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['unbound_followup_turn'])

    def test_mid_turn_messages_require_matching_native_routing_not_text_claims(self):
        message=event('response_item',dict(role='user',content=[dict(type='input_text',text='PRIVATE_GUIDANCE')],
            internal_chat_message_metadata_passthrough=dict(turn_id='turn_one')))
        self.rows.insert(-1,message)
        self.assertEqual(self.inspect()['dispatch_usage']['status'],'measured')
        message['payload']['internal_chat_message_metadata_passthrough']['turn_id']='other'
        self.assertEqual(self.inspect()['dispatch_usage']['issues'],['unbound_user_message'])

    def test_multiple_dispatches_in_one_native_turn_are_ambiguous(self):
        self.rows.insert(-1, context()); self.rows.insert(-1, assigned('task_two', 'ctx_two'))
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['ambiguous_turn_assignment'])

    def test_counter_before_assignment_is_not_included(self):
        self.rows.insert(3, count())
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['usage_before_assignment'])

    def test_counter_after_terminal_and_reused_native_id_are_ambiguous(self):
        self.rows.append(count((200, 40, 140, 10), (100, 20, 70, 5)))
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['usage_outside_native_turn'])
        followup=self.followup()
        followup[0]['payload']['turn_id']='turn_one';followup[-1]['payload']['turn_id']='turn_one'
        self.rows=[self.meta]+self.first+followup
        self.assertEqual(self.inspect()['dispatch_usage']['issues'], ['duplicate_native_turn_id'])

    def test_unknown_native_boundary_keeps_session_reference(self):
        self.rows = [self.meta, context(), assigned(), count()]
        record = self.inspect()
        self.assertEqual(record['dispatch_usage']['status'], 'unavailable')
        self.assertEqual(record['usage']['tokens']['total_tokens'], 120)

    def test_request_context_is_paired_and_validated(self):
        for change in [dict(role=None), dict(request_id=None), dict(request_id='../other'), dict(role='arbitrary')]:
            with self.subTest(change=change), self.assertRaises(ValueError):self.inspect(**change)
        value = self.inspect(request_id=None, role=None)['dispatch_usage']
        self.assertEqual(value['status'], 'measured'); self.assertFalse(value['task_attributed'])
        self.assertEqual(value['request_binding'], 'unlinked')


if __name__ == '__main__': unittest.main()
