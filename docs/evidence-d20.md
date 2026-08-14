# Bằng chứng D20 — phần AIE-1 (engine) · GATE-2 + plan-vs-actual

> **Mục đích:** phần AIE-1 trong evidence-pack GATE-2 (issue **kit#126**, con của gate **kit#129**).
> Chuẩn *"đủ để chấm không cần hỏi"* — mọi con số ở §2 **tái lập được** bằng khối lệnh ở §1, không tin
> lời khai. §3 là **plan-vs-actual** đối chiếu [design-note D11](design-notes/aie1-day11.md) §5. §4 là
> điều tra thật cho finding (e)① mà `dholmes0207` nêu ở `kit#126` (13/08) — đo bằng thực nghiệm, không
> phải suy luận.
>
> **Sửa sau review `engine#26` round 1 (dholmes0207, `CHANGES_REQUESTED`, 4 finding — cả 4 đã kiểm
> tra lại độc lập bằng lệnh thật, cả 4 đều đúng):** F-1 sửa câu sai số lượng script chạm `studio_kb`
> (§3, §5). F-2 thêm §6 (gitlink). F-3 đổi §4.1 sang "diff là bằng chứng chính, thực nghiệm là đối
> chứng có negative control". F-4 chạy thí nghiệm tách biến, đổi kết luận §4.2 sang "2 biến,
> retrieval backend là biến chính" — đổi owner tiếp theo (thêm DE).
>
> **Sửa sau round 2 (dholmes0207, 3 điểm — cả 3 đã kiểm tra lại độc lập, cả 3 đều đúng):** R-1 —
> §6 bịa quy ước "commit thẳng không qua PR" không có thật; sửa về đúng đường 5 lần bump trước đã
> dùng (qua PR). R-2 — §4.1 neo sai dòng (`126,132-133` là số dòng của output diff, không phải file
> thật; số đúng là `358,364`). R-3 — số quan trọng nhất của §4.2 (thí nghiệm tách biến) từng là
> placeholder không dán-chạy được; đã commit thành script thật
> (`apps/studio/scripts/probe_isolate_retrieval_d20.py`), và ghi thêm 1 hiện tượng chưa giải thích
> được (đảo dấu giữa 2 chỉ số khi đổi biến runner/LLM) mà round-2 review phát hiện. Không finding
> nào ở cả 2 round bị bác.

---

## 1. Chạy lại — khối lệnh dán-là-chạy

Từ **thư mục gốc của kit** (`agentcore-studio-kit/`):

```bash
uv run --package agentcore-studio-engine pytest packages/engine/tests -q   # 168 passed
uv run ruff check packages/engine                                          # All checks passed!
uv run ruff format --check packages/engine                                 # 36 files already formatted
uv run mypy packages/engine                                                # Success: no issues found in 36 source files
uv run lint-imports                                                        # 1 kept, 0 broken

uv run python packages/engine/scripts/run_golden_batch.py                  # 30/30 case khớp nhãn golden
```

`run_golden_batch.py` **không cần Postgres** (dùng `StaticKbSearch` thật, in-memory) — chạy được
standalone. §4 dưới đây (đường `EngineAgentRunner`+`EvalHarness`) **cần** Postgres:

```bash
docker compose -f docker-compose.test.yml up -d --wait
export STUDIO_DATABASE_URL_ADMIN=postgresql://studio_owner:changeme@localhost:5433/studio_test
export STUDIO_DATABASE_URL=postgresql://studio_app:changeme@localhost:5433/studio_test
```

---

## 2. Số đo — DoD `kit#126` từng item

| Item `#126` | Bằng chứng (test / lệnh / SHA) | Kết |
|---|---|---|
| **6 node-type executor chạy DAG thật qua ES** | `test_condition_dag_e2e.py` + `test_executors_behavior.py`: 6/6 `NodeType` (`kb-retrieve`/`llm-step`/`condition`/`tool-call`/`hitl-pause`/`end`) chạy chung **một** DAG thật, khoá bởi `test_node_type_closed.py` (cấm loại thứ 7) | ✅ |
| **Bảng chunking×embedding trade-off có số, ghép vào spine** | `measure_chunk_embed.py` tái lập khớp D14 từng số (`aie1-day14-grid-harness.md`) — verify độc lập trước merge PR#25 | ✅ |
| **Đã ghép vào spine (golden-30 correctness)** | `run_golden_batch.py`: **30/30** case khớp nhãn (citations + refused), qua `StaticKbSearch` thật + `interpreter.run()` thật | ✅ |
| **Regression xanh trước PR** | `168 passed` (pytest) · ruff/mypy/lint-imports sạch — 5 lệnh nằm trong job `test-reconstructed` của `reusable-domain-ci.yml` (không phải job `lint` — job đó tên `lint-shallow`, chạy bộ yếu hơn `uvx ruff check src tests`, và **skip** trên PR docs-only này) | ✅ |

**3 PR riêng biệt của lane này, mỗi PR ứng với 1 issue day-N, không gộp** (cả 3 tạo **và** merge
cùng ngày 12/08, 06:19→09:06 UTC — "1 PR/ngày" ở đây nghĩa là 1 PR cho 1 day-issue, không phải nhịp
làm việc trải 3 ngày lịch):

| Ngày | PR | Merge commit | Nội dung |
|---|---|---|---|
| D18 | `engine#23` | `941c8a0` | `llm-step` output ổn định cho judge + ES gateway-flag |
| D19 | `engine#24` | `562346b` | Token accounting thật (`Tokens(prompt,completion)`) + idempotent-qua-replay + failure-mode retrieval |
| D20 | `engine#25` | `bfa19cc` | 6-node DAG spine thật + tái lập bảng trade-off |

---

## 3. Plan-vs-actual — đối chiếu design-note D11 §5 "Câu chặn phải giải trước khi ký"

| # | Cam kết D11 (03/08, §5) | Actual D20 | Kết |
|---|---|---|---|
| Q-3 | *"cost một-nguồn: đồng ý, executor chỉ cấp `tokens`, sink tính `cost`. Code đã đúng sẵn: `cost=_NO_COST`."* | Giữ nguyên đúng như cam kết: D19 (`engine#24`) đã thêm **token accounting thật** (`Tokens(prompt, completion)` không còn hard-code `(0,0)`), nhưng `interpreter.py:73,438` **vẫn** `cost=_NO_COST` (0.0) — executor không tự tính `cost`, đúng như đã ký ở D11. Sink (`obs.trace_events`/cost-lineage, lane DE) đọc `tokens` và **tự tính** `cost` on-read (`kb#22`, D19, merge 13/08). | ✅ **đúng như đã ký — không phải một lỗ sót, là kiến trúc đã chốt từ D11** |
| Q-D | *"stub `kb.search` sống trong engine, không nhận `StubKbSearch` từ `packages/kb` — `.importlinter` cấm `studio_engine` import `studio_kb`."* | Giữ nguyên phần ranh giới layer: mọi chỗ engine chạm `studio_kb` nằm ở `scripts/` (ngoài `src/studio_engine/`), ngoài vùng `.importlinter` quét — đúng lý do đã nêu ở D11. **Sửa sau review `engine#26` (F-1, dholmes0207):** câu gốc ở đây từng viết "`run_golden_batch.py` là nơi duy nhất" — **sai**, đếm thật có **3 script, 5 dòng import** (`run_real_batch.py`×1, `run_golden_batch.py`×2, `measure_chunk_embed.py`×2). Chi tiết + honest-TODO ở §5. | ✅ ranh giới layer đúng như đã ký · ⚠️ câu mô tả cũ sai số lượng, đã sửa |
| — | *(D11 không có mục dự đoán riêng cho D20 — file chỉ tới §5, không có §6 "điểm S2 đã biết" như bản DE)* | D20 làm đúng phạm vi đã khoá ở plan `260812-1133-d18-d20-aie1-engine-daily-prs`: 6/6 node-type trong 1 DAG thật, không build gateway LLM/embedding thật (ngoài scope R-6), không đụng `packages/contracts`. | ✅ **không có mục nào D11 hứa mà D20 làm thiếu** |

---

## 4. Finding (e)① — điều tra thật, không phải khai báo

`dholmes0207` nêu ở `kit#126` (13/08, sau `apps/studio` chạy golden-30 qua `EngineAgentRunner` +
`PgKbSearch` + Postgres thật): `success_rate = 0.1667` (5/30), trong khi `DEC-D17-04`
(`evalhub/docs/decisions/scorecard.md:362`) đo `0.2667` (8/30) trên "cùng golden-30". Giả thuyết nêu
ra nhưng chưa xác nhận: *"`engine` hôm nay (`bfa19cc`, D20 6-node) mới hơn bản D17 đã đo — có thể là
nguyên nhân."* Điều kiện lật đã hẹn: chạy lại trên `engine@62773ba` (D17) và so.

> **Sửa sau review `engine#26` (dholmes0207, F-3/F-4):** bản gốc §4 dưới đây chỉ đưa phép đo thực
> nghiệm làm bằng chứng, không kèm bằng chứng rẻ hơn (diff) và quy 1 nguyên nhân cho 1 biến trong khi
> có 2. Cả hai đã sửa — xem 4.1 (thêm diff + negative control) và 4.2 (thêm thí nghiệm tách biến thật,
> đổi kết luận owner).

### 4.1 Giả thuyết "do `engine` D20" — bằng chứng rẻ (diff) trước, thực nghiệm sau (đối chứng)

**Bằng chứng rẻ nhất, đủ để đóng câu hỏi:** diff thật giữa 2 bản engine mà finding so sánh —

```bash
$ git -C packages/engine diff --stat 62773ba bfa19cc -- src/
 src/studio_engine/executors.py | 101 +++++++++++++++++++++++++++++++++++------
 1 file changed, 87 insertions(+), 14 deletions(-)
```

Một file. Xem đúng dòng đổi (không phải context):

```bash
$ git -C packages/engine diff 62773ba bfa19cc -- src/studio_engine/executors.py | grep -n "^[+-].*\(citations\|refused\)"
74:-        Tokens(0, 0), "citations": [...], "refused": <bool>}`. `tokens` is
80:+        Tokens(prompt=N, completion=M), "citations": [...], "refused": <bool>,
```

Hai dòng đổi thật duy nhất nhắc `citations`/`refused` nằm **trong docstring ví dụ**, không phải code.
Dòng tính `citations`/`refused` thật (`executors.py:358,364` tại `bfa19cc` — **sửa sau round-2 review
`engine#26` (R-2, dholmes0207)**: bản trước ghi nhầm `126,132-133`, đó là số dòng của *output diff*
đếm bằng `grep -n`, không phải số dòng thật trong file; `git show bfa19cc:src/studio_engine/
executors.py | sed -n '358p;364p'` xác nhận đúng `citations = [cid for cid in _CITATION_RE.findall
(answer) if cid in retrieved_ids]` / `"refused": not citations,`) là **context không đổi** ở cả hai
commit. Delta ngữ nghĩa thật giữa hai
bản chỉ có `llm_source` flag (D18) + `Tokens(prompt, completion)` thật (D19) — không chạm logic
citation/refusal. ⇒ **"sai số 0" giữa hai bản engine là kết quả bị đảm bảo trước khi chạy bởi chính
diff này, không phải một phát hiện thực nghiệm.**

**Thực nghiệm (đối chứng, không phải bằng chứng chính) — kèm negative control:**

```text
$ git -C packages/engine rev-parse --short HEAD && uv run pytest ... -s
PROBE engine@bfa19cc success_rate=0.16667 citation_accuracy=0.22727 n_scored=22 verdict=FAIL len=30

$ git -C packages/engine checkout 62773ba && git -C packages/engine rev-parse --short HEAD && uv run pytest ... -s
PROBE engine@62773ba success_rate=0.16667 citation_accuracy=0.22727 n_scored=22 verdict=FAIL len=30
```

`git rev-parse --short HEAD` in ngay trước mỗi lần chạy — xác nhận checkout thật sự có hiệu lực
(không phải nhãn gõ tay), dọn `__pycache__` trước mỗi lần (bẫy bytecode đã biết từ D19/D20). Kết quả
**khớp diff dự đoán** — không có nondeterminism ẩn, và không mâu thuẫn kết luận ở trên. Sau khi đo,
checkout lại `bfa19cc` (trạng thái gốc khôi phục nguyên vẹn).

**Kết luận §4.1: giả thuyết "do `engine` D20" BỊ BÁC BỎ** — bằng diff (bằng chứng chính) và xác nhận
bằng thực nghiệm (đối chứng, không mâu thuẫn).

### 4.2 Nguyên nhân thật — HAI biến khác nhau giữa `DEC-D17-04` và đường D20, không phải một

`DEC-D17-04` (`evalhub/docs/decisions/scorecard.md:357-362`) đo qua `run_golden_batch.py` →
`StubAgentRunner` **nạp từ interpreter thật chạy trên `StaticKbSearch`** (in-memory) →
`success_rate=0.2667`, `citation_accuracy=1.0000`. Đường D20
(`test_gate2_verdict_from_live_spine.py`) chạy `EngineAgentRunner` **sống** qua `ExtractiveFakeLLM`
**trên `PgKbSearch`** (Postgres thật) → `citation_accuracy=0.2273`.

Hai đường lệch **cả hai** trục cùng lúc: **(a)** runner/LLM double (`StubAgentRunner` ghi lại vs
`EngineAgentRunner`+`ExtractiveFakeLLM` sống) **và (b)** retrieval backend (`StaticKbSearch` vs
`PgKbSearch`). Bản gốc của mục này chỉ quy cho (a) — thiếu thí nghiệm tách biến. Đã bổ sung:

**Thí nghiệm tách biến — đổi đúng 1 biến so với đường D20, giữ nguyên runner/LLM sống. Sửa sau
round-2 review (R-3, dholmes0207):** bản trước dán placeholder không chạy được
(`<probe: EngineAgentRunner(...)>`), vi phạm chính chuẩn "dán-là-chạy" pack tự đặt ra ở đầu file.
Đã commit thành script thật, permanent, trong repo:

```bash
$ uv run python apps/studio/scripts/probe_isolate_retrieval_d20.py
kb_search=StaticKbSearch (giữ nguyên runner/LLM sống — chỉ đổi retrieval backend)
success_rate=0.6667  citation_accuracy=0.9091  n_scored_citation=22  verdict=FAIL  len=30
```

| Cấu hình | `kb_search` | runner/LLM | `success_rate` | `citation_accuracy` |
|---|---|---|---|---|
| `DEC-D17-04` | `StaticKbSearch` | `StubAgentRunner` (ghi lại) | 0.2667 | **1.0000** |
| Đường D20 | `PgKbSearch` | `EngineAgentRunner`+`ExtractiveFakeLLM` (sống) | 0.1667 | 0.2273 |
| **Tách biến** (đường D20, chỉ đổi `kb_search`) | `StaticKbSearch` | `EngineAgentRunner`+`ExtractiveFakeLLM` (sống) | **0.6667** | **0.9091** |

**Đổi đúng 1 biến (retrieval backend) đã kéo `citation_accuracy` từ `0.2273` lên `0.9091`** — gần sát
`1.0000` của `DEC-D17-04` mà không đụng runner/LLM. Retrieval backend là biến **chính**, không phải
runner/LLM như bản gốc quy kết một mình.

**Một điểm CHƯA giải thích được — ghi lại, không giấu (R-3, dholmes0207):** đi từ hàng "Tách biến"
sang hàng `DEC-D17-04` chỉ đổi biến (a) (runner/LLM: sống → ghi lại). `citation_accuracy` **tăng**
(`0.9091 → 1.0000`) nhưng `success_rate` **giảm** (`0.6667 → 0.2667`) — **hai chỉ số đảo dấu khi đổi
cùng một biến.** "Cả hai biến cùng góp phần tuyến tính" không mô tả được hiện tượng này. Không điều
tra tiếp trong D20 (ngoài đường găng Gate-2) — có thể là biến thứ ba, hoặc đặc tính của cách
`success_rate` được tính (không chỉ trên citation). Chi tiết + giả thuyết ở docstring của
`probe_isolate_retrieval_d20.py`.

**Không sửa số, không sửa harness của AIE-2/DE** (ngoài lane cả hai). Kết luận "không phải do bản
`engine` D20" **đứng vững hơn trước** (giờ có diff + 2 thực nghiệm tái lập được, không phải suy luận
một chiều) — nhưng cơ chế đầy đủ của cả 2 biến thì **chưa** đóng, ghi rõ ở trên.

**Chủ tiếp theo:** **AIE-2** (chủ `EvalHarness`/`StubAgentRunner`, quyết có hội tụ 2 đường đo hay
không) **+ DE** (chủ `PgKbSearch`/retrieval — biến chính lộ ra ở đây). **Không còn việc nào chờ
AIE-1** trên finding (e)① — điều kiện lật đã đo xong ở cả 2 lớp bằng chứng, kết luận là "không phải
engine, chủ yếu là retrieval backend".

---

## 5. Honest-TODO — không giấu

- **`cost=_NO_COST` (0.0) vẫn hard-code** trong `interpreter.py:73,438` — **đây KHÔNG phải một lỗ
  sót**, là kiến trúc đã ký ở D11 Q-3 (§3 ở trên): executor chỉ cấp `tokens`, sink tính `cost`. Không
  có việc nào của AIE-1 còn treo ở mục này.
- **Gateway LLM/embedding thật** — chính thức ngoài scope kit (quyết định R-6,
  `docs/system-architecture.md §6`). Không build trong 3 PR D18-D20.
- **R-2 (daily-note D15, 07/08): "walk_from_dag bị từ chối vì tốn thêm 1 dòng import `studio_kb`,
  vi phạm R-2" — R-2 hiện ĐANG GÃY ở 2/3 script.** (`engine#26`, F-1, phát hiện bởi `dholmes0207`.)
  Đếm thật tại `bfa19cc`:

  | Script | Dòng import `studio_kb` | R-2 (≤1 dòng) |
  |---|---|---|
  | `scripts/run_real_batch.py` | 1 (có annotation `R-2: 1 dòng duy nhất`) | ✅ |
  | `scripts/run_golden_batch.py` | 2 (annotation ghi "1 dòng duy nhất" nhưng đếm ra 2) | ❌ |
  | `scripts/measure_chunk_embed.py` | 2 (không annotation) | ❌ |

  `lint-imports` báo `0 broken` vì `.importlinter` `root_packages` không quét `scripts/` — CI xanh ở
  đây là **guard mù đúng chỗ gãy**, không phải bằng chứng tuân thủ. Không tự sửa import trong PR
  evidence-pack này (đổi `src`-adjacent script ngày gate là rủi ro không cần thiết, và không phải
  scope PR docs-only) — ghi lại làm honest-TODO có chủ: **AIE-1**, hạn **sau Gate-2** (không chặn
  DoD nào của `kit#126` — R-2 là kỷ luật nội bộ scripts, không phải AC).

---

## 6. Gitlink — vì sao evidence-pack này có thể "tàng hình" trong fresh clone

**Việc đã biết, đúng lớp lỗi `kit#73` (`engine#26`, F-2, phát hiện bởi `dholmes0207`):** PR này thêm
1 commit mới vào `main` của `agentcore-studio-engine`, đẩy tip qua khỏi `bfa19cc`. Con trỏ submodule
`packages/engine` trong `agentcore-studio-kit` **vẫn ghim `bfa19cc`** cho tới khi có PR bump riêng —
`git clone --recursive` kit ngay sau khi PR này merge sẽ ra cây engine **không có**
`docs/evidence-d20.md`, và `fresh-clone gate` sẽ cap `O3.1` xuống B cho cả nhóm dù file này đã tồn
tại trên `main` của engine.

**Sửa sau round-2 review (R-1, dholmes0207) — đường vá trước đây SAI, đã kiểm lại bằng lệnh thật:**
không có quy ước "commit thẳng, không qua PR" nào tồn tại trong kit (`grep -r
"gitlink-bookkeeping"` chỉ khớp chính câu tự trích dẫn cũ của mục này). 5 lần bump gitlink gần nhất
trên `main` (`#155`, `#154`, `#151`, `#150`, `#148`) **đều qua PR**, không có lần nào commit thẳng.

**Việc cần làm ngay sau merge PR này (không phải phase riêng, xem thêm plan
`260812-1133-d18-d20-aie1-engine-daily-prs` §Note cuối):** bump gitlink `packages/engine` trong kit
lên đúng SHA merge-commit mới, theo đúng đường 5 lần trước đã dùng — nhánh `chore/d20-bump-engine-
evidence` (hoặc tương đương), commit `chore(gitlink): bump packages/engine ...`, mở PR vào
`agentcore-studio-kit`, review + merge như các lần trước, **không** commit thẳng `main`.

---

*Neo state: `packages/engine@bfa19cc` · `apps/studio@79b8f0e` (SHA lúc chạy §4, xác nhận lại bằng
`git -C apps/studio rev-parse HEAD` tại thời điểm đọc — SHA đổi nếu `apps/studio` có PR mới sau khi
trang này viết) · Postgres qua `docker-compose.test.yml`.*
