"""Stage 3 controlled evaluation of the current Stage 2 relationship extractor.

Run locally: python -B evaluate_nlp_controlled.py
No model downloads, network calls, or extraction rule changes are performed.
"""

from hashlib import sha256
from pathlib import Path
import sys

import pandas as pd
import spacy

import nlp_relationship_analysis as stage2

PROJECT_DIR = Path(__file__).resolve().parent
DATASET_PATH = PROJECT_DIR / 'datasets' / 'CARE_NLP_Stage3_Controlled_Dataset.xlsx'
RESULTS_PATH = stage2.RESULTS_PATH.with_name('nlp_stage3_controlled_results.xlsx')
OUTCOMES = {('Yes', 'Yes'): 'TP', ('No', 'No'): 'TN',
            ('No', 'Yes'): 'FP', ('Yes', 'No'): 'FN'}


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def summarize(results):
    counts = results['Outcome (TP/TN/FP/FN)'].value_counts()
    totals = {kind: int(counts.get(kind, 0)) for kind in ('TP', 'TN', 'FP', 'FN')}
    return {'Number of cases': len(results),
            'Passed': totals['TP'] + totals['TN'],
            'Failed': totals['FP'] + totals['FN'], **totals}


def safe_ratio(numerator, denominator):
    # Undefined metrics use 0.0, explicitly documented in the workbook.
    return numerator / denominator if denominator else 0.0


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    protected = [PROJECT_DIR / 'nlp_component_test.py', Path(stage2.__file__).resolve()]
    before = {path: file_hash(path) for path in protected}
    dataset_hash = file_hash(DATASET_PATH)
    dataset = stage2.load_dataset(DATASET_PATH)
    if dataset['ID'].astype(str).str.strip().eq('').any() or dataset['ID'].duplicated().any():
        raise ValueError('Test IDs must be non-empty and unique.')
    nlp = spacy.load('en_core_web_sm')

    # Reuse the entire current evaluation path: text -> local spaCy Doc ->
    # extract_relationships(Doc) -> target filtering. Labels never reach the extractor.
    results = stage2.evaluate_dataset(dataset, nlp)
    results.insert(results.columns.get_loc('Pass/Fail'), 'Outcome (TP/TN/FP/FN)',
                   [OUTCOMES[(expected, detected)] for expected, detected in
                    zip(results['Expected Relationship'], results['Detected Relationship'])])
    results['Pass/Fail'] = results['Outcome (TP/TN/FP/FN)'].map(
        {'TP': 'Pass', 'TN': 'Pass', 'FP': 'Fail', 'FN': 'Fail'})
    totals = summarize(results)
    precision = safe_ratio(totals['TP'], totals['TP'] + totals['FP'])
    recall = safe_ratio(totals['TP'], totals['TP'] + totals['FN'])
    metrics = {'Accuracy': safe_ratio(totals['Passed'], len(results)),
               'Precision': precision, 'Recall': recall,
               'F1': safe_ratio(2 * precision * recall, precision + recall)}
    codes = results['Target NLP Relationship Type'].map(stage2.target_code)
    per_type = pd.DataFrame([
        {'Relationship Type': f'{code} {name}', **summarize(results.loc[codes.eq(code)])}
        for code, name in stage2.RELATIONSHIP_TYPES.items()
    ])
    failed_ids = results.loc[results['Pass/Fail'].eq('Fail'), 'Test ID'].astype(str).tolist()
    summary = pd.DataFrame([
        {'Metric': key, 'Value': value} for key, value in
        {**{'Total test cases': totals['Number of cases']},
         **{key: value for key, value in totals.items() if key != 'Number of cases'},
         **metrics}.items()
    ])
    for path, digest in before.items():
        if file_hash(path) != digest:
            raise RuntimeError(f'Protected source changed during evaluation: {path.name}')
    if file_hash(DATASET_PATH) != dataset_hash:
        raise RuntimeError('Dataset changed during evaluation.')
    metadata = {
        'Evaluation': 'Stage 3 controlled NLP relationship extraction accuracy',
        'Scope': 'Linguistic relationship extraction; not overall CARE gender-bias detection accuracy.',
        'Interpretation': 'No final contextual bias determination. P6 does not establish document-level representation imbalance.',
        'Reuse': 'Unmodified Stage 2 load_dataset and evaluate_dataset; calls extract_relationships on text only.',
        'Zero denominators': 'Undefined accuracy, precision, recall, or F1 is reported as 0%.',
        'Dataset': str(DATASET_PATH), 'Dataset SHA256': dataset_hash,
        'Python version': sys.version, 'spaCy version': spacy.__version__,
        'Model': 'en_core_web_sm', 'Model version': nlp.meta.get('version', ''),
        'Failed test IDs': ', '.join(failed_ids) or 'None',
        **{f'{path.name} SHA256': digest for path, digest in before.items()},
        'Evaluation script SHA256': file_hash(Path(__file__).resolve()),
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(RESULTS_PATH, engine='openpyxl') as writer:
        results.to_excel(writer, sheet_name='Case Results', index=False)
        summary.to_excel(writer, sheet_name='Overall Metrics', index=False)
        per_type.to_excel(writer, sheet_name='Per Relationship', index=False)
        pd.DataFrame(metadata.items(), columns=['Field', 'Value']).to_excel(
            writer, sheet_name='Evaluation Notes', index=False)
        for sheet in writer.book.worksheets:
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
        for row in writer.book['Overall Metrics'].iter_rows(min_row=2):
            if row[0].value in metrics:
                row[1].number_format = '0.00%'

    print('Stage 3 — Controlled Dataset Evaluation')
    print(f"\nTotal cases: {len(results)}\nPassed: {totals['Passed']}\nFailed: {totals['Failed']}")
    for key in ('TP', 'TN', 'FP', 'FN'):
        print(f'{key}: {totals[key]}')
    print('\nStage 3 controlled NLP relationship extraction metrics:')
    for key, value in metrics.items():
        print(f'{key}: {value:.2%}')
    print('\nFailed test IDs: ' + (', '.join(failed_ids) or 'None'))
    print('\n' + per_type.to_string(index=False))
    print(f'\nResults saved to: {RESULTS_PATH}')
    print('Current source hashes verified unchanged.')
    print('Linguistic relationships only; no final bias verdict or document-level representation imbalance claim.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        raise SystemExit(f'Stage 3 could not complete: {error}') from error
