import re
import pandas as pd


# ============================================================
# R1: GENERIC / GENDER-ASSUMPTIVE PRONOUN RULE
# Detects gendered pronouns used for an unspecified/general
# person or role.
# ============================================================

def check_gender_assumptive_pronoun(text):
    generic_pattern = (
        r"\b(every|each|any)\b.*"
        r"\b(he|him|his|she|her|hers)\b"
    )

    roles = [
        "programmer",
        "developer",
        "engineer",
        "student",
        "teacher",
        "manager",
        "lecturer",
        "researcher"
    ]

    role_pattern = "|".join(roles)

    assumptive_pattern = (
        rf"\b(a|an|the)\s+({role_pattern})\b.*"
        rf"\b(he|him|his|she|her|hers)\b"
    )

    if re.search(generic_pattern, text, re.IGNORECASE) or re.search(
        assumptive_pattern, text, re.IGNORECASE
    ):
        return {
            "rule": "R1",
            "category": "Gendered Language",
            "indicator": "Generic or gender-assumptive pronoun"
        }

    return None


# ============================================================
# R2: GENDER-MARKED TERMINOLOGY RULE
# Detects gender-marked terms where a gender-neutral
# alternative is available.
# ============================================================

gender_marked_terms = {
    "chairman": "chairperson",
    "policeman": "police officer",
    "fireman": "firefighter",
    "salesman": "salesperson",
    "businessman": "businessperson",
    "mankind": "humankind",
    "manpower": "workforce",
    "freshman": "first-year student"
}


def check_gender_marked_terminology(text):
    for term, alternative in gender_marked_terms.items():
        pattern = rf"\b{re.escape(term)}\b"

        if re.search(pattern, text, re.IGNORECASE):
            return {
                "rule": "R2",
                "category": "Gendered Language",
                "indicator": "Gender-marked terminology",
                "detected_term": term,
                "suggestion": alternative
            }

    return None


# ============================================================
# R3: GENDERED COLLECTIVE / ADDRESS TERM RULE
# Detects predefined gendered expressions used to address or
# collectively refer to an unspecified or mixed group.
# ============================================================

gendered_collective_terms = {
    "hey guys": "hello everyone",
    "you guys": "everyone"
}


def check_gendered_collective_term(text):
    for term, alternative in gendered_collective_terms.items():
        pattern = rf"\b{re.escape(term)}\b"

        if re.search(pattern, text, re.IGNORECASE):
            return {
                "rule": "R3",
                "category": "Gendered Language",
                "indicator": "Gendered collective or address term",
                "detected_term": term,
                "suggestion": alternative
            }

    return None


# ============================================================
# COMBINED GENDERED LANGUAGE ANALYSIS
# Runs R1, R2 and R3 on the same text.
# ============================================================

def analyze_gendered_language(text):
    results = []

    checks = [
        check_gender_assumptive_pronoun,
        check_gender_marked_terminology,
        check_gendered_collective_term
    ]

    for check in checks:
        result = check(text)

        if result is not None:
            results.append(result)

    return results


# ============================================================
# STAGE 3: CONTROLLED TEST DATASET EVALUATION
# Reads the original CARE test dataset and evaluates each case.
# ============================================================

input_file = "CARE_Test_Dataset_V1_Updated.xlsx"

df = pd.read_excel(input_file)

evaluation_results = []

for index, row in df.iterrows():
    test_id = row["Test ID"]
    text = str(row["Test Content"])

    # Convert blank Excel categories from NaN to "None".
    expected_category = row["Expected Category"]

    if pd.isna(expected_category):
        expected_category = "None"
    else:
        expected_category = str(expected_category)

    # The current rule engine evaluates Gendered Language only.
    expected_detection = expected_category == "Gendered Language"

    # Run the combined R1-R3 rule engine.
    results = analyze_gendered_language(text)

    # True when at least one rule detects a concern.
    detected = len(results) > 0

    # Store all rules that were triggered.
    detected_rules = [result["rule"] for result in results]

    if detected_rules:
        detected_rules_text = ", ".join(detected_rules)
    else:
        detected_rules_text = "None"

    # Compare expected and detected results.
    status = "PASS" if detected == expected_detection else "FAIL"

    evaluation_results.append({
        "Test ID": test_id,
        "Test Content": text,
        "Expected Category": expected_category,
        "Expected Gendered Language Detection": (
            "Yes" if expected_detection else "No"
        ),
        "Detected Gendered Language": (
            "Yes" if detected else "No"
        ),
        "Triggered Rules": detected_rules_text,
        "Result": status
    })


# ============================================================
# CREATE STAGE 3 RESULTS DATAFRAME
# ============================================================

results_df = pd.DataFrame(evaluation_results)


# ============================================================
# DISPLAY STAGE 3 RESULTS
# ============================================================

print("\n========== CARE Rule-Based Dataset Evaluation ==========\n")

for _, row in results_df.iterrows():
    print("Test ID:", row["Test ID"])
    print("Expected Category:", row["Expected Category"])
    print(
        "Expected Gendered Language:",
        row["Expected Gendered Language Detection"]
    )
    print(
        "Detected Gendered Language:",
        row["Detected Gendered Language"]
    )
    print("Triggered Rules:", row["Triggered Rules"])
    print("Result:", row["Result"])
    print()


# ============================================================
# STAGE 3 EVALUATION SUMMARY
# ============================================================

total_tests = len(results_df)

passed_tests = (
    results_df["Result"] == "PASS"
).sum()

failed_tests = (
    results_df["Result"] == "FAIL"
).sum()

print("========== Stage 3 Evaluation Summary ==========")
print("Total tests:", total_tests)
print("Passed:", passed_tests)
print("Failed:", failed_tests)


# ============================================================
# SAVE STAGE 3 RESULTS TO EXCEL
# ============================================================

output_file = "CARE_Rule_Based_Evaluation_Results.xlsx"

results_df.to_excel(
    output_file,
    index=False
)

print("\nResults saved to:", output_file)


# ============================================================
# STAGE 4: UNSEEN / CHALLENGE DATASET EVALUATION
# Tests the SAME R1-R3 rules on new challenge cases.
# The rules are not changed before this evaluation.
# ============================================================

challenge_file = "CARE_Rule_Challenge_Dataset_V1.xlsx"

challenge_df = pd.read_excel(challenge_file)

challenge_results = []

for index, row in challenge_df.iterrows():
    test_id = row["Test ID"]
    text = str(row["Test Content"])
    expected = str(row["Expected Gendered Language"])
    expected_rule = str(row["Expected Rule"])
    challenge_type = str(row["Challenge Type"])

    # Convert expected Yes/No into True/False.
    expected_detection = expected == "Yes"

    # Run the SAME existing R1-R3 rules.
    results = analyze_gendered_language(text)

    # True if at least one rule was triggered.
    detected = len(results) > 0

    # Store all rules that were triggered.
    detected_rules = [result["rule"] for result in results]

    if detected_rules:
        detected_rules_text = " + ".join(detected_rules)
    else:
        detected_rules_text = "None"

    # Compare expected and actual detection.
    status = "PASS" if detected == expected_detection else "FAIL"

    # Identify the type of error.
    if expected_detection and not detected:
        error_type = "False Negative"

    elif not expected_detection and detected:
        error_type = "False Positive"

    else:
        error_type = "None"

    challenge_results.append({
        "Test ID": test_id,
        "Test Content": text,
        "Challenge Type": challenge_type,
        "Expected Gendered Language": expected,
        "Expected Rule": expected_rule,
        "Detected Gendered Language": (
            "Yes" if detected else "No"
        ),
        "Triggered Rules": detected_rules_text,
        "Result": status,
        "Error Type": error_type
    })


# ============================================================
# CREATE STAGE 4 RESULTS DATAFRAME
# ============================================================

challenge_results_df = pd.DataFrame(challenge_results)


# ============================================================
# DISPLAY STAGE 4 RESULTS
# ============================================================

print("\n========== CARE Rule-Based Challenge Evaluation ==========\n")

for _, row in challenge_results_df.iterrows():
    print("Test ID:", row["Test ID"])
    print("Challenge Type:", row["Challenge Type"])
    print(
        "Expected Gendered Language:",
        row["Expected Gendered Language"]
    )
    print(
        "Detected Gendered Language:",
        row["Detected Gendered Language"]
    )
    print("Expected Rule:", row["Expected Rule"])
    print("Triggered Rules:", row["Triggered Rules"])
    print("Result:", row["Result"])
    print("Error Type:", row["Error Type"])
    print()


# ============================================================
# STAGE 4 EVALUATION SUMMARY
# ============================================================

total_challenge_tests = len(challenge_results_df)

passed_challenge_tests = (
    challenge_results_df["Result"] == "PASS"
).sum()

failed_challenge_tests = (
    challenge_results_df["Result"] == "FAIL"
).sum()

false_positives = (
    challenge_results_df["Error Type"] == "False Positive"
).sum()

false_negatives = (
    challenge_results_df["Error Type"] == "False Negative"
).sum()

print("========== Stage 4 Challenge Evaluation Summary ==========")
print("Total tests:", total_challenge_tests)
print("Passed:", passed_challenge_tests)
print("Failed:", failed_challenge_tests)
print("False positives:", false_positives)
print("False negatives:", false_negatives)


# ============================================================
# SAVE STAGE 4 RESULTS TO EXCEL
# ============================================================

challenge_output_file = (
    "CARE_Rule_Challenge_Evaluation_Results.xlsx"
)

challenge_results_df.to_excel(
    challenge_output_file,
    index=False
)

print(
    "\nChallenge results saved to:",
    challenge_output_file
)