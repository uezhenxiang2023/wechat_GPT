import re


COMMON_PROMPT_RATIO_MAP = {
    1.0: "1:1",
    1.33: "4:3",
    0.75: "3:4",
    1.78: "16:9",
    0.56: "9:16",
    1.5: "3:2",
    0.67: "2:3",
    2.33: "21:9",
}


def parse_aspect_ratio_from_prompt(prompt: str, ratio_map=None, decimal_tolerance=0.15, ratio_tolerance=0.3):
    ratio_candidates = dict(ratio_map or COMMON_PROMPT_RATIO_MAP)
    if not prompt or not ratio_candidates:
        return None

    decimal_pattern = r'(?<!\d)(\d+\.\d+)(?!\d)'
    for match in re.finditer(decimal_pattern, prompt):
        ratio = round(float(match.group(1)), 4)
        closest = min(ratio_candidates, key=lambda key: abs(key - ratio))
        if abs(closest - ratio) <= decimal_tolerance:
            return ratio_candidates[closest]

    pattern = r'(\d+(?:\.\d+)?)\s*(?::|：|比)\s*(\d+(?:\.\d+)?)'
    match = re.search(pattern, prompt)
    if not match:
        return None

    width = float(match.group(1))
    height = float(match.group(2))
    if height == 0:
        return None

    ratio = round(width / height, 4)
    closest = min(ratio_candidates, key=lambda key: abs(key - ratio))
    if abs(closest - ratio) > ratio_tolerance:
        return None
    return ratio_candidates[closest]
