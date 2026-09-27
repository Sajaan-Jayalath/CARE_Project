"""Local Stage 4 evaluation; the Stage 2 extractor remains frozen.

Run: python -B evaluate_nlp_challenge.py
"""
from pathlib import Path
import sys

import pandas as pd
import spacy

import nlp_relationship_analysis as stage2
from evaluate_nlp_controlled import OUTCOMES, file_hash, safe_ratio, summarize

PROJECT_DIR = Path(__file__).resolve().parent
DATASET_PATH = PROJECT_DIR / 'datasets' / 'CARE_NLP_Stage4_Challenge_Dataset.xlsx'
RESULTS_PATH = PROJECT_DIR / 'results' / 'nlp_stage4_challenge_results.xlsx'
# Historical counts supplied for Stage 3; these do not influence extraction.
STAGE3_COUNTS = {'Number of cases': 24, 'Passed': 17, 'Failed': 7,
                 'TP': 5, 'TN': 12, 'FP': 0, 'FN': 7}


def metrics_for(counts):
    precision = safe_ratio(counts['TP'], counts['TP'] + counts['FP'])
    recall = safe_ratio(counts['TP'], counts['TP'] + counts['FN'])
    return {'Accuracy': safe_ratio(counts['TP'] + counts['TN'], counts['Number of cases']),
            'Precision': precision, 'Recall': recall,
            'F1': safe_ratio(2 * precision * recall, precision + recall)}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    protected = [PROJECT_DIR / name for name in (
        'nlp_component_test.py', 'nlp_relationship_analysis.py', 'evaluate_nlp_controlled.py')]
    before = {path: file_hash(path) for path in protected + [DATASET_PATH]}
    dataset = stage2.load_dataset(DATASET_PATH)
    if 'Challenge Type' not in dataset.columns:
        raise ValueError('Missing dataset column: Challenge Type')
    if not dataset['Challenge Type'].map(lambda v: isinstance(v, str) and bool(v.strip())).all():
        raise ValueError('Every case must have a non-empty Challenge Type.')
    ids = dataset['ID'].astype(str).str.strip()
    if ids.eq('').any() or ids.duplicated().any():
        raise ValueError('Test IDs must be non-empty and unique.')
    nlp = spacy.load('en_core_web_sm')  # Installed local model; no downloads.
    # The frozen evaluator passes text only to spaCy/extract_relationships,
    # filters returned matches by target, and retains original evidence.
    results = stage2.evaluate_dataset(dataset, nlp)
    results.insert(results.columns.get_loc('Expected Relationship'), 'Challenge Type',
                   dataset['Challenge Type'].tolist())
    results.insert(results.columns.get_loc('Pass/Fail'), 'Outcome (TP/TN/FP/FN)',
                   [OUTCOMES[(expected, detected)] for expected, detected in
                    zip(results['Expected Relationship'], results['Detected Relationship'])])
    results['Pass/Fail'] = results['Outcome (TP/TN/FP/FN)'].map(
        {'TP': 'Pass', 'TN': 'Pass', 'FP': 'Fail', 'FN': 'Fail'})
    totals = summarize(results)
    metrics = metrics_for(totals)
    codes = results['Target NLP Relationship Type'].map(stage2.target_code)
    per_type = pd.DataFrame([
        {'Relationship Type': f'{code} {name}', **summarize(results.loc[codes.eq(code)])}
        for code, name in stage2.RELATIONSHIP_TYPES.items()])
    per_challenge = pd.DataFrame([
        {'Challenge Type': name, **{key: value for key, value in summarize(group).items()
                                  if key in ('Number of cases', 'Passed', 'Failed')}}
        for name, group in results.groupby('Challenge Type', sort=False)])
    failed = results.loc[results['Pass/Fail'].eq('Fail'), [
        'Test ID', 'Target NLP Relationship Type', 'Challenge Type',
        'Expected Relationship', 'Detected Relationship', 'Outcome (TP/TN/FP/FN)']].copy()
    failed.rename(columns={'Target NLP Relationship Type': 'Relationship Type'}, inplace=True)
    summary = pd.DataFrame(
        [{'Metric': key, 'Value': value} for key, value in {**totals, **metrics}.items()])
    stage3 = {**STAGE3_COUNTS, **metrics_for(STAGE3_COUNTS)}
    stage4 = {**totals, **metrics}
    comparison = pd.DataFrame([
        {'Metric': key, 'Stage 3': stage3[key], 'Stage 4': stage4[key]}
        for key in ('Accuracy', 'Precision', 'Recall', 'F1', 'TP', 'TN', 'FP', 'FN')])
    for path, digest in before.items():
        if file_hash(path) != digest:
            raise RuntimeError(f'Protected input changed during evaluation: {path.name}')
    notes = {
        'Evaluation': 'Stage 4 challenge NLP relationship extraction accuracy',
        'Scope': 'Linguistic relationship extraction, not final contextual bias classification.',
        'Context': 'Reported, rejected, negated or quoted relationships are not endorsements.',
        'P6': 'Local gender-linked actions do not establish document-level representation imbalance.',
        'Label observation': 'S4-P6-03 expects No for a balanced/shared outcome; frozen P6 extracts local actions. The supplied label is preserved.',
        'Reuse': 'Stage 2 load_dataset and evaluate_dataset unchanged; Stage 3 reporting helpers only.',
        'Comparison source': 'User-supplied Stage 3 counts; metrics recomputed from those counts.',
        'Zero denominators': 'Undefined metrics are reported as 0%.',
        'Python version': sys.version, 'spaCy version': spacy.__version__,
        'Model': 'en_core_web_sm', 'Model version': nlp.meta.get('version', ''),
        'Dataset': str(DATASET_PATH),
        **{f'{path.name} SHA256': digest for path, digest in before.items()},
        'Evaluation script SHA256': file_hash(Path(__file__).resolve()),
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(RESULTS_PATH, engine='openpyxl') as writer:
        for name, frame in [
            ('Case Results', results), ('Overall Metrics', summary),
            ('Per Relationship', per_type), ('Challenge Types', per_challenge),
            ('Failed Cases', failed), ('Stage 3 Comparison', comparison),
            ('Evaluation Notes', pd.DataFrame(notes.items(), columns=['Field', 'Value']))]:
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.book[name]
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
        for name in ('Overall Metrics', 'Stage 3 Comparison'):
            for row in writer.book[name].iter_rows(min_row=2):
                if row[0].value in metrics:
                    for cell in row[1:]:
                        cell.number_format = '0.00%'
    print('Stage 4 — Unseen / Challenge Dataset Evaluation')
    print(f"\nTotal cases: {len(results)}\nPassed: {totals['Passed']}\nFailed: {totals['Failed']}\n")
    for key in ('TP', 'TN', 'FP', 'FN'):
        print(f'{key}: {totals[key]}')
    print('\nStage 4 challenge NLP relationship extraction metrics:')
    for key, value in metrics.items():
        print(f'{key}: {value:.2%}')
    print('\nFailed test IDs: ' + (', '.join(failed['Test ID'].astype(str)) or 'None'))
    print('\nPer-relationship summary:\n' + per_type.to_string(index=False))
    print('\nChallenge-type summary:\n' + per_challenge.to_string(index=False))
    print('\nFailed cases:\n' + (failed.to_string(index=False) if not failed.empty else 'None'))
    display_comparison = comparison.copy().astype(object)
    for index, row in comparison.iterrows():
        for column in ('Stage 3', 'Stage 4'):
            display_comparison.loc[index, column] = (
                f'{row[column]:.2%}' if row['Metric'] in metrics else str(int(row[column])))
    print('\nStage 3 comparison:\n' + display_comparison.to_string(index=False))
    print(f'\nResults saved to: {RESULTS_PATH}')
    print('Protected source files and dataset verified unchanged.')
    print('Linguistic relationships only; no final contextual bias verdict or document-level representation imbalance claim.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        raise SystemExit(f'Stage 4 could not complete: {error}') from error
