# WaniKani → Notion JLPT dashboard

This repository synchronizes WaniKani v2 statistics to a private Notion dashboard using GitHub Actions. Python 3.12 and standard library only.

**Already created in your Notion workspace:**

- [Dashboard](https://app.notion.com/p/3fd98ffb213081e9948af06cb0ae5888)
- [WaniKani Snapshots](https://app.notion.com/p/67adf543c29941c894b9379bc5542ad5) — data source `a35d8794-a5c6-4208-ab52-b21d793bf010`
- [JLPT Milestones](https://app.notion.com/p/722a67ebb36e4d819fdd09d8fd03fc05) — data source `b45e8463-c10a-4d95-88c3-e43eaa260290`

## Daily target is editable in Notion

Update [Learning Settings](https://app.notion.com/p/cff5db14491e4289bb4840e822796a23), row **Configuration**, property **Daily Lessons Target** (currently 15). **Tracking Start** is set to 2026-10-18 (Vietnam time). Historical lessons are retained for context and lesson-volume modeling, but 7d/30d rates count only days from this date onward, **including today**. If there are **0 new lessons/day since Tracking Start**, actual `ETA` has **no finite date** and `ETA Status` is `Paused: 0 new lessons/day`; it never substitutes the 15-lessons target as measured pace. **Recent Pace (7d)** is shown alongside 30-day Actual Pace. Counts include radical, kanji, vocabulary, and kana vocabulary based on `assignments.data.started_at`. This is an observed lesson-start proxy, not the WaniKani recommended-daily-lessons website setting. WaniKani 7/30-day averages include elapsed calendar days (including the current partial day) with zero lessons, in the timezone defined in that row. The target is read every run; no Python edits needed. Grant the GitHub Notion integration access to Learning Settings as well as the other databases.

**ETA** now uses the observed **Radical+Kanji lessons/day** (7-day average, 30-day fallback), exact level-by-level gate-critical Radical/Kanji lessons and the current level's SRS earliest bound. Vocabulary activity does not inflate the level-up pace. **No daily activity means no actual ETA.** **Target ETA** is an optimistic level-up estimate with 15 lessons/day, not a requirement to finish all Vocabulary. **Actual Full Lessons ETA** uses observed **all subject types lessons/day**, including Radical, Kanji, Vocabulary and Kana Vocabulary, and cannot precede the actual level-up ETA. If no Radical/Kanji learning has been observed, actual full ETA remains blank (gate progression unmeasurable). **Target Full Lessons ETA** uses 15 lessons/day as an optimistic lower bound to start all remaining subjects including Vocabulary, computed with total unstarted subject inventory (not the historical 141 lessons per level) and cannot predate the target level-up ETA. The future-level SRS lower-bound is conservatively represented by a *heuristic* seven days per level; review mistakes or delays can push all these dates later. Review accuracy is tracked but is not used as an invented ETA probability. None of the dates prove JLPT readiness.

## 1. Get your WaniKani token (read-only)

Open https://www.wanikani.com/settings/personal_access_tokens , create a v2 API token. **Do not enable any write/update permissions**. Copy the token into a password manager temporarily.

## 2. Create a separate Notion API connection for GitHub

> ChatGPT's existing Notion connection does not supply its access token to your GitHub Actions workflow.

1. Open Notion `Settings → Connections` and turn on Developer Mode if needed, then create an internal connection / personal integration at the developer tools area (or https://www.notion.com/my-integrations).
2. Name it `WK Notion Sync`. Grant the minimum `Read content`, `Insert content`, and `Update content` capabilities needed to query and write the two databases. Copy its token.
3. On the linked Notion dashboard page, click `••• → Connections → Add connections`, choose `WK Notion Sync` and approve its access. If either database returns 403/404, open that specific database and repeat `Add connections` there.

## 3. Create a private GitHub repository

At https://github.com/new create a repository named e.g. `wanikani-notion-sync`, visibility **Private**. Upload the contents of this folder, **including hidden folder `.github/`**; or extract locally and push with Git.

```bash
git init
git add .
git commit -m "Add WaniKani Notion sync"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/wanikani-notion-sync.git
git push -u origin main
```

## 4. Configure GitHub Actions secrets

In the new repo open `Settings → Secrets and variables → Actions → Secrets → New repository secret`.

| Secret name | Value |
|---|---|
| `WK_TOKEN` | WaniKani API v2 read-only token |
| `NOTION_TOKEN` | Token of the new `WK Notion Sync` Notion integration |

Do not put tokens in `sync.py`, README, workflow YAML, Git commits, screenshots, chat messages, or Actions logs. The three Notion data source IDs are already configured inside the workflow; they are identifiers, not secrets.

## 5. Test first run

In your repository open `Actions → Sync WaniKani to Notion → Run workflow` (select `main`).

The job first runs unit tests and then fetches WaniKani data. A successful run creates **one daily snapshot row** and upserts three milestone rows. Re-running on the same UTC date updates that daily snapshot, rather than duplicating it. The first run may need several WaniKani pages to read all your history.

See the Actions log if it fails:

- `Missing ...`: secret absent or named incorrectly.
- `HTTP 401`: API token expired, revoked, or copied incorrectly.
- `HTTP 403` or `HTTP 404` for `data_sources`: grant the Notion integration access to the destination databases, check the IDs.
- `missing properties`: don't rename Notion columns; the script requires their exact names.
- Milestone ETA empty: fewer than 2 historically observed level durations; script **does not invent a forecast**.

## 6. Scheduling and customization

The workflow runs at `00:17`, `06:17`, `12:17`, `18:17` UTC (in Viet Nam at **07:17, 13:17, 19:17, 01:17**). GitHub schedules can be delayed or occasionally missed, and only run from the default branch. Manual `Run workflow` is always available.

Edit `milestones.json` to change the **configurable WaniKani proxy levels**. The supplied values 10/20/30 are **examples only**, not official JLPT correspondence. N5/N4/N3 additionally require grammar, listening, reading, and mock-test preparation. WaniKani alone cannot certify JLPT readiness. Historical median days per level remains visible for reference only; the new model estimates lesson workload.

Metrics:

- `Learned Kanji/Vocab` = assignments with SRS stage >=1, not proof of mastery.
- SRS stage groups are current states, not counts of unique characters.
- `Accuracy Pct` = weighted historical meaning + reading response accuracy on kanji/vocabulary review statistics (not an official JLPT score).
- `Reviews Now` / `Reviews Next 24h` = snapshot of WaniKani summary, which is updated roughly hourly.
- `Days Per Level` = median historical interval between starts of consecutive levels. If fewer than 2 intervals, no forecast.
- `Progress Pct` in Milestones = WaniKani level / configured proxy target; **not actual JLPT coverage**.

## 7. Local verification (optional)

```bash
python -m unittest discover -s tests -v
# Never put secrets in the repo. Export them locally only if you want a manual sync.
# WK_TOKEN=... NOTION_TOKEN=... NOTION_SNAPSHOTS_DATA_SOURCE_ID=... NOTION_MILESTONES_DATA_SOURCE_ID=... python sync.py
```

## Privacy and permissions

Keep the repository private. Limit collaborators because people with write access may be able to modify workflows that consume secrets. Rotate tokens if leaked. Notion integrations need direct permission to the relevant pages/databases. No need to send a WaniKani or Notion API token to ChatGPT.

Useful references: https://docs.api.wanikani.com/20170710/ · https://developers.notion.com/reference/intro · https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets


## Detailed course progress and review accuracy (new)

- [WaniKani Level Breakdown](https://app.notion.com/p/68ff00960f4d4bd5b15f490beea350ee): counts subjects in each accessible WaniKani level, lessons started/unstarted, unlocked/locked lessons, the 90% Kanji Guru level-up gate, and an **optimistic minimum** number of required future reviews to Guru.
- [WaniKani Review Checkpoints](https://app.notion.com/p/6e19821f815c4b23a9330485828e94b9): saves totals from `review_statistics` each sync, then calculates differences from the **first recorded checkpoint** and preceding sync. **The API does not expose an event-by-event review history; `Reviews Since Baseline` is a LOWER BOUND proxy of correct meaning answers, NOT the exact number of review sessions completed.** Accuracy is computed from counter differences of correct/incorrect meaning and reading answers. First checkpoint has no history and new accuracy is blank.
- `Next Gate Earliest` is a best-case SRS lower bound for the **current** level, assuming reviews and lessons are done immediately with no mistakes. This is not an expected ETA.
- `Remaining Lessons (Full)` counts all unstarted subjects from current level up to, **but not including**, the requested milestone level. `Gate Lessons Remaining` counts kanji and prerequisite radicals needed for Guru progression, without vocabulary. These differ by design.
- Future levels may be inaccessible through a WaniKani account's subscription/API access. Empty catalog results cannot be interpreted as zero required lessons. Completion estimates need validated accessible subject counts.
- The projected `ETA` and `Target ETA` still rely on the historical workload model and must not be read as a verified SRS-aware date. Accuracy is diagnostic, not a numeric probability for ETA.

For the two new databases, the GitHub Notion integration needs Read/Insert/Update access. Their data source IDs are in the workflow YAML.


### October 18 model correction

The prior model used `141 historical lessons/level`, even though WaniKani has exact per-level subject counts. This has been replaced by `wk_detailed.forecast_with_inventory()` and `forecast_full_lessons()`. `Lessons/Level` in old Notion snapshot rows remains a historical diagnostic only; the ETA calculation never uses it. `ETA Status`, `Target ETA`, `Target Full Lessons ETA`, `Gate Lessons Remaining` and `Remaining Lessons (Full)` should be read together. Gate estimates assume prioritizing Kanji/Radical lessons; daily lesson budget spent on Vocabulary may delay level-up. The Notion tracking plan begins 2026-10-18, and older `started_at` events are excluded from 7d and 30d pace.


### Daily dashboard activity: Lessons Today vs Reviews Today

The N5 live page shows **Lessons Today / Daily Lessons Target**, a Radical/Kanji/Vocabulary breakdown, and **Reviews Today (ƯỚC TÍNH)**. Lessons Today is exact from assignment `started_at` timestamps in the configured Vietnamese timezone. The Reviews number is **not an exact session count**: WaniKani deprecated `GET /reviews`, so `daily_reviews.py` compares the current **cumulative meaning-correct answers** with the latest saved review checkpoint *before* local midnight. The source and time window are stored in Snapshot `Reviews Today Window`, and the proxy total in `Reviews Today Approx`.

**Caveats:** If someone reviewed after the prior-day checkpoint but before midnight, their activity can be counted for the new day. Meaning-correct counts are not a complete record of review events; missed intervals, resets and missing midnight baseline prevent an exact calculation. A missing prior-day checkpoint results in a blank value, **not zero**. Completing a lesson quiz is not a review. All cards update on the GitHub Actions cadence, not live with every tap.

### Comparable ETAs and subject-type lesson rates

| Forecast | Daily pace input | Remaining work |
|---|---|---|
| `ETA` | Actual `Gate 7d Avg` (Radical+Kanji), else 30d | Per-level Radical/Kanji Guru-critical gate lessons and SRS lower bounds |
| `Target ETA` | Configured daily target (15) **optimistically allocated to gate work** | Same gate work and SRS assumptions |
| `Actual Full Lessons ETA` | Actual `Lessons 7d Avg` (Radical+Kanji+Vocabulary+Kana Vocabulary), else 30d | All unstarted lessons, date no earlier than actual gate ETA |
| `Target Full Lessons ETA` | Configured daily target (15) | All unstarted lessons, date no earlier than target gate ETA |

The `WaniKani Snapshots` table stores `Radical 7d Avg`, `Kanji 7d Avg`, `Vocab 7d Avg` and matching 30d rates plus `Gate 7d Avg`, `Gate 30d Avg` and per-type lessons today. `Vocab` includes kana vocabulary. JLPT Milestones shows the 7d split as `Radical Pace (7d)`, `Kanji Pace (7d)`, `Vocab Pace (7d)` and `Gate Pace (7d)`.

For example, starting **3 Kanji + 2 Vocabulary** today results in 5 full lessons/day but only 3 gate-relevant lessons/day. Actual full ETA is therefore not compared to Target ETA (different goals); it is compared to Target Full Lessons ETA. These dates are optimistic planning indicators: a Radical/Kanji started is not automatically one of the subjects needed to satisfy the Guru gate, SRS delays remain, and a single day is a highly unstable estimate. Historical lessons before Tracking Start never enter the new per-type rates.
