# WHO ATC dependencies (licensing unresolved)

**Status:** WHO ATC/DDD licensing and redistribution terms remain unresolved. This note makes no legal or licensing determination.

## Public candidate contents

- The cleaned public drug dataset has an empty `atc_codes` column.
- The shipped public severity artifact is name-only and contains no ATC lookup, ATC features, or WHO-derived mapping.
- Raw WHO source files, restricted ATC exports, restricted model artifacts, and restricted reports are excluded.
- ATC mapping code remains optional; without local source data, it does not add ATC features to the public artifact.

No aggregate ATC coverage figures or restricted-model performance metrics are included here. They are withheld pending licensing review.

## Historical Git data

This candidate is a source snapshot without a `.git` directory or Git objects. The original repository's base commit and reachable history contain earlier WHO-derived ATC values in the cleaned dataset and a previous model artifact with an ATC section. This snapshot does not sanitize or rewrite that history. Do not publish a branch descended from that history while the licensing question is unresolved.

## Application behavior

The public model remains usable without WHO data. The advisor can estimate severity only for recognized DDInter drugs; unknown drugs receive no estimate. Severity output does not establish whether an interaction exists and is not clinically validated.

## Licensing review still needed

Review the applicable WHO ATC/DDD terms before redistributing source data, derived mappings, restricted model artifacts, or results based on those data. Other data sources in the repository have their own terms and may require separate review. No alternative vocabulary has been adopted as a substitute.
