import csv
import os
import re
from pathlib import Path

EXCEL_PATH = os.getenv("APARTMENT_EXCEL_PATH", "Central_NJ_Apartment_Comparison_WITH_TOUR_CLUSTERS_FINAL.xlsx")
MAX_PRICE = int(os.getenv("MAX_PRICE", "3200"))
COMMUTE_LIMIT = int(os.getenv("COMMUTE_LIMIT", "30"))
REQUIRED_BEDS = int(os.getenv("MIN_BEDS", "2"))
REQUIRED_BATHS = int(os.getenv("MIN_BATHS", "2"))


def normalize_field(value):
    if value is None:
        return ""
    text = str(value).strip()
    return re.sub(r"\s+", " ", text)


def parse_number(value):
    if value is None:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def parse_commute_minutes(value):
    text = normalize_field(value)
    if not text:
        return None
    match = re.search(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*(?:min|mins|minute|minutes)", text, re.IGNORECASE)
    if not match:
        return None
    lower = match.group(1)
    upper = match.group(2)
    if upper:
        return (int(lower) + int(upper)) / 2
    return float(lower)


def row_is_managed_community(row):
    text = normalize_field(
        " ".join(
            [
                row.get("Management") or "",
                row.get("Property Type") or "",
                row.get("Community Name") or "",
                row.get("Notes") or "",
            ]
        )
    ).lower()
    if not text:
        return False
    managed_markers = [
        "managed",
        "leasing office",
        "property management",
        "apartment community",
        "townhome community",
    ]
    return any(marker in text for marker in managed_markers)


def repair_row(row):
    repaired = {}
    for key, value in row.items():
        cleaned = normalize_field(value)
        if key.lower() in {"community name", "property", "name", "community"}:
            repaired[key] = cleaned
        elif key.lower() in {"price", "monthly rent", "estimated 2-bed price range"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        elif key.lower() in {"beds", "bedroom", "bedrooms"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        elif key.lower() in {"baths", "bathroom", "bathrooms"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        elif key.lower() in {"property type", "type"}:
            repaired[key] = cleaned if cleaned else "Apartment"
        elif key.lower() in {"address", "location", "community address"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        elif key.lower() in {"commute", "drive time", "commute time"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        elif key.lower() in {"leasing office phone", "phone", "contact"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        elif key.lower() in {"official website", "website", "link", "url"}:
            repaired[key] = cleaned if cleaned else "Not listed"
        else:
            repaired[key] = cleaned
    return repaired


def validate_row(row):
    issues = []
    normalized = {
        k: normalize_field(v)
        for k, v in row.items()
    }

    community_name = normalized.get("Community Name") or normalized.get("Property") or normalized.get("Name") or ""
    prices = [
        parse_number(normalized.get("Price")),
        parse_number(normalized.get("Monthly Rent")),
        parse_number(normalized.get("Estimated 2-Bed Price Range")),
    ]
    price_values = [n for n in prices if n is not None]
    if not community_name:
        issues.append("Missing community name.")
    if not row_is_managed_community(row):
        issues.append("Community is not clearly identified as a managed property.")
    if not normalized.get("Address") and not normalized.get("Location"):
        issues.append("Missing address.")
    if not normalized.get("Beds") and not normalized.get("Bedroom"):
        issues.append("Missing bedroom count.")
    if not normalized.get("Baths") and not normalized.get("Bathroom"):
        issues.append("Missing bathroom count.")
    if not normalized.get("Property Type"):
        issues.append("Missing property type.")
    if not normalized.get("Amenities") and not normalized.get("Key Amenities"):
        issues.append("Missing amenities.")
    if not normalized.get("Leasing Office Phone") and not normalized.get("Phone") and not normalized.get("Contact"):
        issues.append("Missing leasing office phone/contact.")
    if not normalized.get("Official Website") and not normalized.get("Website") and not normalized.get("Link"):
        issues.append("Missing official website or booking link.")

    contains_2_bed = False
    bed_fields = [normalized.get("Beds"), normalized.get("Bedroom"), normalized.get("Bedrooms")]
    for field in bed_fields:
        if field and re.search(r"2\s*(?:bed|bedroom|br)", field, re.IGNORECASE):
            contains_2_bed = True
    if not contains_2_bed:
        issues.append("Property is not clearly a 2-bedroom unit.")

    bath_field = normalized.get("Baths") or normalized.get("Bathroom") or normalized.get("Bathrooms") or ""
    if bath_field and re.search(r"(1|2)\s*(?:bath|bathroom|ba)", bath_field, re.IGNORECASE):
        if not re.search(r"2\s*(?:bath|bathroom|ba)", bath_field, re.IGNORECASE) and not re.search(r"1\s*(?:bath|bathroom|ba)", bath_field, re.IGNORECASE):
            issues.append("Bathroom count is not within the preferred 1-2 range.")

    if price_values:
        if max(price_values) > MAX_PRICE:
            issues.append(f"Price exceeds budget cap of ${MAX_PRICE}.")
    else:
        issues.append("Missing or invalid rent amount.")

    commute = parse_commute_minutes(normalized.get("Commute") or normalized.get("Drive Time") or normalized.get("Commute Time") or "")
    if commute is None:
        issues.append("Missing commute time to 08807.")
    elif commute > COMMUTE_LIMIT:
        issues.append(f"Commute exceeds {COMMUTE_LIMIT} minutes.")

    amenities_text = (normalized.get("Amenities") or normalized.get("Key Amenities") or "").lower()
    if "in-unit laundry" not in amenities_text and "washer" not in amenities_text and "dryer" not in amenities_text:
        issues.append("Missing in-unit laundry amenity.")
    if "disposal" not in amenities_text and "garbage disposal" not in amenities_text:
        issues.append("Missing garbage disposal amenity preference.")

    return {
        "passes": not issues,
        "issues": issues,
        "row": row,
    }


def repair_workbook(path=EXCEL_PATH, output_path=None):
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=False)
    changed = False
    for sheet in workbook.worksheets:
        first_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        if not first_row:
            continue
        keys = [str(cell or "").strip() for cell in first_row]
        for row in sheet.iter_rows(min_row=2, values_only=False):
            values = {}
            for idx in range(min(len(keys), len(row))):
                cell = row[idx]
                if cell._value is None:
                    continue
                if cell.coordinate not in sheet.merged_cells.ranges and hasattr(cell, "value"):
                    values[keys[idx]] = cell.value
            if not values or not any(value not in (None, "") for value in values.values()):
                continue
            repaired = repair_row(values)
            for idx, key in enumerate(keys[: len(row)]):
                if idx >= len(row):
                    continue
                cell = row[idx]
                if cell.coordinate in sheet.merged_cells.ranges:
                    continue
                new_value = repaired.get(key)
                if new_value is not None and cell.value != new_value:
                    cell.value = new_value
                    changed = True
    if output_path:
        workbook.save(output_path)
    elif changed:
        workbook.save(path)
    return {"changed": changed, "path": output_path or path}


def validate_workbook(path=EXCEL_PATH):
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True)
    all_issues = []
    rows = []

    for sheet in workbook.worksheets:
        first_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        if not first_row:
            continue
        keys = [str(cell or "").strip() for cell in first_row]
        for row in sheet.iter_rows(min_row=2, values_only=True):
            record = {keys[idx]: value for idx, value in enumerate(row[: len(keys)])}
            if not any(value not in (None, "") for value in record.values()):
                continue
            result = validate_row(record)
            rows.append({"sheet": sheet.title, "record": record, "result": result})
            if not result["passes"]:
                all_issues.append({"sheet": sheet.title, "issues": result["issues"], "record": record})

    return {"pass": not all_issues, "issues": all_issues, "rows": rows}


def validate_csv(path):
    results = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            result = validate_row(row)
            results.append(result)
    return {"pass": not any(not item["passes"] for item in results), "results": results}


if __name__ == "__main__":
    path = os.getenv("APARTMENT_EXCEL_PATH", EXCEL_PATH)
    repair_result = repair_workbook(path)
    print(f"Workbook repair changed file: {repair_result['changed']}")
    result = validate_workbook(path)
    print(f"Workbook validation passed: {result['pass']}")
    if result["issues"]:
        for issue in result["issues"][:10]:
            print(f"Sheet: {issue['sheet']}")
            print(f"Issues: {issue['issues']}")
            print(issue["record"])
