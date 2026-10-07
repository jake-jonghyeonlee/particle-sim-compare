METHOD-REVIEW TASK (read-only advisory) - cross-product local vulnerability correspondence methodology

You are an independent methodology reviewer (Codex gpt-6-astra, effort high) inside Orca Run run_2336681f13e5. You advise the Opus coordinator; you gain no coordination authority, launch no nested workers/subagents, and implement nothing. Another worker (Sol editor) is actively editing src/ and tests/ of this repository for an accepted plan: do not touch, stage, revert or reformat anything; do not run commands that write into the repository (no test runs that create files; reading is fine).

TARGET
- Repository /Users/jonghyeonlee/git/particle-sim-compare (branch record-cno-open-fab-decisions, HEAD b507da96554baa658171dcae61a99342c9ec36e3, working tree has in-flight regional work: treat files as a moving snapshot and say which snapshot you read). Sibling /Users/jonghyeonlee/git/particle-sim is read-only reference for CA/hotspot producer semantics.
- Read: docs/orca/run_2336681f13e5/request.txt, plan-v1.md, plan-review-v1.md, checkpoint.md; docs/open_questions.md; src/simcompare (schema.py, model.py, pipeline.py compute_spaces/run_analyze, retrieval.py, neighborhood.py, pair_geometry.py, variants.py, and the new regional_*.py / adapters/regional.py as they currently exist).

USER'S CLARIFIED GOAL (exact quote, Korean)
"내가 원하는건 어느정도 달라져도 전극 layout의 공통된 ca값이 매칭되면 비교가 가능하고 신규 매칭 사라진 매칭을 트레이싱해서 전체적인 제품군 비교를 해서 이 모델의 취약 위치를 찾아내는거야. 한개일 필요는 없어. 이 내용 opus5.5로 전달해서 다른 방법론이 없는지 피드백 받고 astra도 방법론 검토해서 종합해봐"
Context: the user wants AE-selected similar products compared to find MULTIPLE vulnerable local structures even when electrode positions/layout/hotspot shapes differ between products. They rejected naive same-coordinate hotspot subtraction (layouts differ), questioned raw CA / ROI scoring, and asked about TreeSHAP without hand-computing distances/areas. The existing AE is a similarity/reconstruction model, not a risk predictor; ae_struct reuses risk inputs. What they now ask for is robust correspondence/tracking across a product family (matched / newly appearing / disappeared, possibly several per product), not registered image differencing, not one brightest ROI, and not necessarily a new CA predictor.

CHANGE (single deliverable)
Write ONE Korean UTF-8 report: /tmp/particle-sim-method-review-20261001-astra.txt . Give YOUR OWN independent methodology assessment; you are deliberately not shown the coordinator's conclusions. Do not assume any method family named below is the answer; recommend a simpler alternative if it is better. The report must:
1. Restate the goal faithfully and clarify what "공통된 CA값 매칭" can mean: an equal scalar CA is not structural identity; weigh per-diameter CA size-curve signature vs shape/context descriptors; separate baseline amplitude from a worsening score.
2. Assess at least three plausible method families, e.g. (a) learned region/patch correspondence, (b) attributed graph matching over electrodes/regions with many-to-many and null assignments (including optimal transport: partial / unbalanced / Gromov-type where suitable), (c) family-wide archetype/motif tracking; and whether TreeSHAP or a new surrogate predictor is necessary at all.
3. Explicitly handle: moved/deformed layouts, hotspot shape changes, multiple foci, split/merge, genuinely new/lost structure vs unmatched/unknown vs risk onset/resolution inside an existing structure; no forced matches; no zero-filling of missing data.
4. Distinguish ordered product versions (true temporal tracing) from unordered products (a family correspondence graph); no false evolutionary or causal claims.
5. Ensure a CA-matching objective does not suppress exactly the CA changes the user wants to find: propose separate axes for correspondence confidence and vulnerability severity; assess dual layout / CA representations.
6. Give a concrete worked example over products A/B/C with at least two vulnerable structures and matched / new / unmatched / split-or-merged events, and show how local locations are returned for a target product.
7. State minimal required existing inputs, any truly necessary extra upstream data, whether hand-engineered geometry is avoidable, original quantitative CA source vs display footprint, why global CA is insufficient to attribute local severity, and boundary/truncation/scale issues.
8. Fit/gap against the current code including the in-flight regional work (distinguish current files from the accepted plan).
9. Validation WITHOUT fabricated accuracy: controlled shape-preserving shifts, true geometry-driven risk changes, equal-CA different geometry, multiple matches, absent observations, split/merge, product-lineage held-out; report correspondence precision/coverage/abstention only if annotated; report localization and CA conservation separately; stability alone is not truth.
10. Where useful, cite primary papers/docs with URLs and distinguish established algorithm properties from your proposed application to this problem. Use only what you can access without paid APIs/credits; if you cannot verify a source online, say so.
11. End with a practical staged recommendation and explicitly rejected alternatives (not a large implementation backlog), plus the essential unresolved assumptions that need user or closed-network data.

CONSTRAINTS
- Method review only. It does not replace the accepted implementation plan. The only file you may create or modify is /tmp/particle-sim-method-review-20261001-astra.txt. No git mutations, no installs, no edits in either repository.
- This is methodological advice, not a code-review PASS; do not label it as one. No accuracy/yield claims.

OWNERSHIP: write only the report file above.
OBSERVABLE ACCEPTANCE: the report exists, covers items 1-11, names the snapshot read; worker_done sent once with --report-path /tmp/particle-sim-method-review-20261001-astra.txt.
BASELINE: branch/HEAD as above; working tree in flux due to the active editor (expected).
