# NOR / NPR Analyzer

Double-click **Start Analyzer.cmd** to open the desktop application on this computer.

1. Open a video. Choose encoding or test, animal ID, and duration. Encoding defaults to 10 minutes; test defaults to 5 minutes. You can edit either duration, and analysis stops at the end of shorter videos. Imported setups keep their saved durations. NOR and NPR use exactly the same scoring; no protocol selection is needed.
2. Choose Circular or Square. For a circle, select **Arena center**, click the center, then select **Arena edge** and click the boundary. For a square arena filmed at an angle, select **Arena corners** and click its four visible corners in any order. The actual quadrilateral becomes the analysis mask, so the edges do not need to be aligned with the image axes. Drag a numbered corner to refine it, use **Undo corner** to replace the last point, or **Redraw corners** to start again. Finish all four points before saving/running. Center/edge and numeric size controls remain available for an axis-aligned square; editing those controls replaces a traced quadrilateral. Circle size is a radius; square size is a full side length (for a traced arena, it displays the maximum bounding extent).
3. Select **Object 1** and **Object 2** and click their centers. For encoding, use **Object 1 edge** and **Object 2 edge** to measure the two radii. Their mean becomes the common radius, as in the original scripts. Alternatively enter the common radius directly (both selected radii then equal this value).
4. Set the zone multiplier. Investigation-zone radius = object radius × multiplier (for example 12 px × 4 = 48 px). This is measured from the object center. All distances use the original video resolution, regardless of preview size.
5. Choose the positive object: the novel object for NOR, or the moved object for NPR. The preference/discrimination score is (positive time − other time) / total investigation time. Zero total investigation produces an undefined score.
6. Run analysis. Open results folder when complete. Each run has its own folder, settings, and log. Stop cancels processing and marks the folder incomplete.

## Batch processing and animal pairing

1. Open **Batch & animal pairs…**. Use **Import metadata folder…** to load the original project's existing recording setups, including circular arenas and four-corner arenas. The current reference folder has 14 recordings for seven animals. You may instead **Add videos…** to select multiple recordings for setup.
2. Pairing uses the exact **animal ID**, including any visit/date suffix. Filename suffixes `_encoding`, `_encode`, `_test`, and `_testing` are recognized for new videos. Verify inferred IDs. The two sessions of one animal/visit must have the same ID. Multiple visits should use distinct IDs to avoid ambiguity.
3. Double-click a recording or choose **Configure selected**. Set its arena and object positions in the main window, then choose **Save current setup to batch**. Repeat for recordings needing setup. Each session retains its own arena, centers, duration, and positive object. Queue changes are saved automatically beside the app; **Save queue…** also exports a reusable setup file.
4. Set the **encoding** object radius and investigation-zone multiplier. These values automatically apply to that animal's test when the batch is validated/run. Changing a test's object radius does not override the encoding calibration. Different animals can have different radii. The batch runs encoding before test and loads the actual saved calibration file for the matching animal.
5. Choose **Validate pairs**, then **Run batch**. New videos with incomplete setup, duplicate sessions, missing videos, invalid geometry, or mismatched calibration animal IDs are rejected before processing starts. An encoding-only batch is supported. A test-only rerun requires an existing calibration belonging to that exact animal. If encoding fails, its test is blocked; other animals continue.
6. **Open batch results** shows `batch_sessions.csv` (one row per completed recording), `paired_animals.csv` (encoding and test results side by side per animal), and `batch_manifest.json` (settings, calibration provenance, status and output paths for every recording). Each session also has its own full exports, settings and log. Every run creates a new batch folder. **Stop batch** cancels the active recording and marks remaining recordings cancelled.

The common calibration applies to object size and investigation-zone radius. Object locations are always session-specific, which handles both a novel object in the same location and a familiar object moved to a new location.

## Existing data

Import encoding calibration loads the animal ID, common object radius and multiplier from the reference project's JSON files. Single test runs require a real matching encoding calibration and use its radius/multiplier. To change a paired calibration, edit the encoding setup in the batch. Test-session centers are selected independently.

Import metadata loads the referenced video and its object centers, session, duration, radius and multiplier. Circular metadata retains the exact arena center/radius. Existing four-corner arenas retain their polygon mask, including perspective distortion. Editing the arena size, center or shape replaces that imported polygon with a new circle or axis-aligned square. Verify the overlay before running.

## Results

Each run produces frame-by-frame CSV, investigation-bout CSV, summary CSV, annotated MP4, background image, object-zone image, settings.json and analysis.log. COMPLETE.txt appears only after successful completion. Interrupted or failed results are partial. The summary includes protocol, arena shape, center and size. In circular runs the inherited corner fields describe the circle's bounding square; settings.json stores the authoritative shape and radius.

## Immobility exclusion

Qualifying immobility is excluded automatically from final investigation times, bout counts, object preference percentages and preference/discrimination indices. The raw tracking and pre-exclusion classifications are retained for audit.

- **Exclude immobility > (s; 0=off)** defaults to 10 seconds. Once an interval is strictly longer than this threshold, the **whole interval**, including its initial 10 seconds, is excluded from investigation. Set 0 to disable detection and exclusion.
- **Immobility motion tolerance (px)** defaults to 2 original-video pixels. Both body and head must stay within this distance of their positions at the interval start. Motion beyond the tolerance restarts the interval; missing head/body tracking breaks it. This pixel tolerance is a configurable software default, not a published universal value.
- Excluded frames split investigation bouts. Minimum bout duration is reapplied to the remaining pieces, and gap bridging is not repeated across excluded frames. If no investigation remains, the preference index is undefined (blank/NaN), not zero.

Frame CSVs include `pre_immobility_state`, final `cleaned_state`, `immobility_excluded`, and `short_bout_after_immobility_excluded`, alongside the original tracking and flags. Interval CSVs retain all detected immobility periods. Final bout CSVs contain only eligible investigation. Orange **EXCLUDED: IMMOBILITY** video labels indicate investigation removed by this rule.

Session and batch summaries report corrected scores, pre-exclusion object times, per-object immobility exclusions, and any further time removed by minimum-bout filtering. `immobility_action` is `exclude_from_investigation` (or `disabled`). Recording/selected-window duration is unchanged. Previous result files are not recalculated automatically; rerun analysis to apply the new rule.

The 10-second default is informed by [Miedel et al. (2017), section 4.2.16](https://pmc.ncbi.nlm.nih.gov/articles/PMC5608159/). Retrospectively excluding the entire stationary interval is this software's explicit rule, not a claim that the paper specifies that exact retrospective procedure. Positional stability does not prove freezing and may include stationary sniffing; validate the tolerance against manually scored clips and use consistent settings across groups.

## Method and provenance

The scoring engine is adapted from `score_object_investigation_skeleton_v4_arena.py` in the `chrizx1/mice-object-investigation-scoring` repository. Batch setup and paired calibration follow the original `setup_recording.py` and circular batch/setup workflows. The original folder is unchanged. Background subtraction, skeleton head/tail estimation, orientation confidence, short-gap bridging, minimum bouts and preference calculations are retained. Circular and square masks are applied to mouse detection, with configurable radii, angle, bout/gap durations, detection threshold and mouse area limits.

This is automated skeleton-based scoring, whose accuracy depends on lighting, visibility and calibration. Review the annotated video, detection rate and orientation-confidence rate before interpreting experiment results. Brief real-video export tests verify functionality, not scientific accuracy on every recording. Analysis starts at the first frame and is capped at the video length.

## Installation on another computer

Use Python 3.12 with Tkinter. In this folder run `python -m pip install -r requirements.txt`, then `python analyzer.py`. The `_packages` directory contains extra Python 3.12 dependencies for the prepared copy on this computer. The compact source ZIP omits installed packages. No videos are uploaded or bundled.
