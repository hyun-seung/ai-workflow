#!/usr/bin/env python3
"""Explicit functional acceptance for RP-03; no candidate generation or metrics."""

import argparse
import importlib.util
from pathlib import Path


def require(condition, criterion):
    if not condition:
        raise AssertionError(criterion)


def check(copy_rows):
    cell = {'values': [1]}
    row = [cell, 7]
    original = [row, row, []]
    result = copy_rows(original)
    require(isinstance(result, list) and result is not original and result == original, 'AC-01 outer/value/order')
    require(len(original) == 3 and original[0] is row and original[1] is row and row == [cell, 7]
            and cell == {'values': [1]} and original[2] == [], 'AC-03 input unchanged')
    require(all(isinstance(item, list) and item is not source for item, source in zip(result, original)), 'AC-01 rows')
    require(result[0] is not result[1], 'AC-04 repeated row')
    require(result[0][0] is cell and result[1][0] is cell, 'AC-02 cell identity')
    result[0].append(9)
    require(len(result[1]) == 2 and len(row) == 2 and result[2] == [], 'AC-04 row mutation isolation')
    result[0][0]['values'].append(2)
    require(cell['values'] == [1, 2], 'AC-02 shared cell mutation')
    empty = []
    copied = copy_rows(empty)
    require(isinstance(copied, list) and copied == [] and copied is not empty, 'AC-03 empty input')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    if not args.candidate.is_absolute() or not args.candidate.is_file():
        parser.error('candidate must be an explicit absolute Python file')
    spec = importlib.util.spec_from_file_location('routing_pilot_candidate', args.candidate)
    if spec is None or spec.loader is None:
        parser.error('candidate must be a loadable Python module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check(module.copy_rows)
    print('RP-03 functional_contract=pass; explanation_sources_controls=not_evaluated')


if __name__ == '__main__':
    main()
