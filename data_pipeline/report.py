"""Render the JSON build report as Markdown (numbers come only from the report dict)."""

from __future__ import annotations

from typing import Any, Dict, List


def _fmt(n: Any) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_fmt(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(report: Dict[str, Any]) -> str:
    after, before = report["after"], report.get("before", {})
    dd, of = after["ddinter"], after["openfda"]
    L: List[str] = ["# Data quality report", "",
                    f"Pipeline v{report['pipeline_version']} - generated {report.get('generated_at_utc', 'n/a')}", ""]

    L += ["## Production safety", "",
          f"Production DB files unchanged by the build: **{report['production_untouched']['unchanged']}** "
          "(SHA-256 before/after).", ""]

    if before:
        L += ["## Before (databases as shipped)", ""]
        rows = []
        for label in ("production_db", "legacy_backup"):
            b = before.get(label, {})
            if not b.get("present"):
                rows.append([label, "absent", "", "", "", ""])
                continue
            k = b["knowledge_rows"]
            rows.append([label, b.get("integrity_check"), k["drugs"], k["interaction_evidence"],
                         k["label_evidence"], k["safety_rules"]])
        L += [_table(["database", "integrity", "drugs", "interaction_evidence", "label_evidence", "safety_rules"], rows), ""]
        bk = before.get("legacy_backup", {})
        if bk.get("present"):
            L += ["Legacy backup details:", ""]
            for key in ("interactions_matching_source_csv", "interactions_not_in_source_csv",
                        "source_csv_pairs_missing_from_db", "interaction_description_equals_severity",
                        "label_evidence_duplicate_text_groups", "test_fixture_patients", "drugs_with_rxnorm"):
                if key in bk:
                    L.append(f"- {key}: {_fmt(bk[key])}")
            L += [f"- clinical_rows (should be 0 in a knowledge DB): {bk['clinical_rows']}", ""]
    L += ["## After: DDInter", "",
          _table(["metric", "value"], [
              ["raw rows (verbatim, all kept)", dd["raw_rows"]],
              ["quarantined rows", dd["quarantined_rows"]],
              ["unordered pairs", dd["pairs"]],
              ["drugs (DDInter IDs)", dd["drugs"]],
              ["pairs by status", dd["pairs_by_status"]],
              ["pairs by label", dd["pairs_by_label"]],
              ["reversed duplicate pairs merged", dd["reversed_duplicate_pairs"]],
              ["same-direction duplicates merged", dd["same_direction_duplicate_pairs"]],
              ["severity conflicts (unlabelled)", dd["level_conflicts"]],
              ["files present / expected", f"{','.join(dd['files_present'])} / {','.join(dd['files_expected'])}"],
              ["distinct base drugs merged by normaliser", dd["base_name_collisions_after_normalization"]],
          ]), ""]
    if dd.get("legacy_json_check"):
        L += [f"Legacy `ddinter_normalized.json` check: {dd['legacy_json_check']}", ""]

    L += ["## After: openFDA", "",
          _table(["metric", "value"], [
              ["source rows (part files present)", of["source_rows"]],
              ["labels with a drug name", of["named_labels"]],
              ["labels WITHOUT a drug name", f"{of['unnamed_labels']} ({of['unnamed_pct']}%)"],
              ["ingestible labels (named + >=1 section)", of["ingestible_labels"]],
              ["section rows", of["sections"]],
              ["sections by type", of["sections_by_type"]],
              ["renal / hepatic keyword flags true", f"{of['renal_flag_true']} / {of['hepatic_flag_true']}"],
              ["multi-product names", of["multi_product_names"]],
              ["raw parts present / expected", f"{of['raw_parts_present']} / {of['raw_parts_expected']}"],
          ]), "",
          f"DailyMed labels ingested: **{after['dailymed']['labels_ingested']}** ({after['dailymed']['status']}). "
          f"DDInter-to-openFDA exact-name links: {after['cross_source_xref_rows']}.", ""]

    L += ["## Staging table row counts", "",
          _table(["table", "rows"], [[k, v] for k, v in after["staging_table_rows"].items()]), ""]

    L += ["## ML feature coverage", "",
          _table(["feature", "status", "available", "total", "blocker"],
                 [[f["feature"], f["status"], f["available_rows"], f["total_rows"], (f["blocker"] or "")[:110]]
                  for f in report["feature_coverage"]]), ""]
    r = report["ml_readiness"]
    L += ["## ML readiness", "", f"**{r['verdict']}**", "",
          f"- trainable labelled pairs: {r['trainable_labeled_pairs']} {r['class_distribution']} "
          f"(majority class {r['majority_class_share_pct']}%)",
          f"- unusable `Unknown` pairs: {r['unusable_unknown_pairs']}; conflicts: {r['conflict_pairs']}",
          f"- {r['split_warning']}", ""]

    L += ["## Issues log", "",
          _table(["dataset", "check", "severity", "affected", "total"],
                 [[i["dataset"], i["check_name"], i["severity"], i["affected"], i["total"]]
                  for i in report["issues"]]), ""]
    return "\n".join(L)
