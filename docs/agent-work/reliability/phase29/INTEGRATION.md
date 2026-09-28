# Phase 29 (T29I) - integration, version, and one scoped backup

Status: **correction cycle applied, re-frozen, and stopped before commit for
Astra's final acceptance.** Astra's first review of this packet returned
REQUEST_CHANGES with a narrow scope: redact only the real-board identifiers the
leak scan identified in two phase-5 documents, reconcile the `README.md` version
marker with `pyproject.toml`, and refresh the packet. Astra extended this task's
`write_paths` to cover `README.md` and both phase-5 documents, and accepted the
48 patch-format whitespace findings plus the single cosmetic trailing space in
`pcb_world/agent/zone_coverage.py` for this backup. Section 3 records what the
redaction changed and what it preserved.

Nothing is committed or pushed. No executable source, test, engine patch, pinned
binary, T30R input, frozen board, project, rule or accepted pointer was touched,
and the private board workspace was not read.

Owner: task T29I. Owned write paths: `HISTORY.md`, `CHANGELOG.md`,
`pyproject.toml`, `README.md`, this file, and - after Astra's scope extension -
`docs/agent-work/reliability/phase5-kicad/RESULT.md` and
`docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md`. Everything else in the
allowlist is another task's accepted output, staged exactly as it stands.

## 1. What this task is

The fork carries one large uncommitted public tree: the accepted agent
reliability layer (`pcb_world/agent/`, the engine interface edits, the methods
and reliability tools, the engine patch series, the agent and engine tests, the
phase documents and the two journals). Phases 1-29 were never frozen into a
commit, so nothing is on GitHub yet. T29I inventories that public set, checks it,
journals it, and prepares exactly one backup commit.

Two things this task does not do. It does not read or stage anything from the
private board workspace, which lives outside this repository and holds every
board byte, net name, coordinate, item identifier and raw report. And it does not
touch a frozen input: the accepted T29C/T29CV documents, the manifest, the
accepted pointer, the frozen boards and the pinned binaries are staged as their
own tasks left them. The only accepted documents this task edits are the two
phase-5 files in section 3, and only inside the scope Astra approved.

## 2. The checks, on the exact staged set

| command | exit | result |
|---|---:|---|
| `bash tools/reliability/check_phase.sh --strict --expected-native 146` | 0 | **640 unit passed** (12.9 s), **146 native passed** (88.3 s), 0 skips, `patches` group reproduces the engine tree byte for byte |
| `python tools/reliability/check_engine_patches.py` (inside the group above) | 0 | five patches apply to pin `7a31e0c9`, 9 files byte-equal, wire copies identical (`f787f5be…`) |
| `git diff --check` | 0 | clean |
| `git diff --cached --check` (after staging) | 2 | exactly 49 findings: 48 inside the five engine patch files, one cosmetic trailing space in `pcb_world/agent/zone_coverage.py`; no new finding, and Astra accepted both for this backup (finding F2) |
| merge-conflict marker scan over the allowlist | - | none |
| duplicate top-level definition scan over `pcb_world/agent/*.py` | - | none |
| secret scan (`sk-…`, `api_key = "…"`, PEM private keys, `.env*`) | - | none; the only match is the tracked `.env.example` |
| private-board identifier scan, staged bytes | - | **clean** - see the re-scan paragraph below and section 3 |

The strict run is the one recorded in the staged
`docs/agent-work/reliability/evidence/phase_check.json` (`strict: true`,
`expected_native_tests: 146`, all three groups at exit 0, `problems: []`). The
correction cycle changed documentation, the README marker and two phase-5
documents, so the harness was not run a third time: every staged source, test and
patch path still carries the same SHA-256 it had when that run passed, which
section 6 verifies path by path.

The staged bytes were re-scanned by reading every file from the **index** rather
than the working tree, which is the set that would be committed. That re-scan
finds no secret, no coordinate-like decimal and no real-board identifier. The
remaining token classes are all accounted for: the private workspace path in 9
files (16 occurrences, section 7), component pad references only in
`tests/agent/test_terminals.py` and `tests/agent/test_native_terminals.py`, one
supply net name only in a synthetic CLI-report fixture
(`tests/agent/test_cli_gate.py`), and UUID-shaped tokens that are all either
synthetic test values or named constants - `pcb_world/agent/drc_gate.py`'s nil
UUID and `pcb_world/agent/kicad_metadata.py`'s normalization namespace.

One path changed during the cycle that this task did not write:
`docs/agent-work/reliability/PARALLELISM_LEDGER.json` now records the extended
T29I `write_paths` Astra authorised (this page, the two phase-5 documents and
`README.md`). It is staged in that state, unedited by this task.

## 3. Finding F1, resolved - the two phase-5 documents redacted

The first packet found real-board identifiers in two documents phase 5 wrote
before the phases began leak-scanning their own reports, and held both out of the
backup. Astra approved a narrow correction instead. Eleven identifier occurrences
across seven lines are now gone, each replaced by the plain description the same
documents already use for withdrawn material. Line numbers did not move, because
every replacement kept its line count.

| document | lines | what the row used to carry | what it carries now |
|---|---|---|---|
| `phase5-kicad/RESULT.md` | 251 | a component pad reference | "one pad" |
| `phase5-kicad/RESULT.md` | 260 | the same reference in the withdrawal note | "the pad attribution" |
| `phase5-kicad/CHECKPOINT.md` | 511 | two supply net names | "3 on one supply net, 1 on another" |
| `phase5-kicad/CHECKPOINT.md` | 512 | a pad reference, three supply net names, one KiCad-generated net name | "one pad", "three supply nets", "one further net" |
| `phase5-kicad/CHECKPOINT.md` | 685-686 | three supply net names | "the last three are supply nets" |
| `phase5-kicad/CHECKPOINT.md` | 703 | the same pad reference | "one pad" |

**Every historical finding, withdrawal notice and aggregate count is preserved
unchanged.** The four isolated-copper identities still carry their 3 + 1 split;
the 33 split relations still carry their 20 + 12 + 1 breakdown; the fourteen
relations lost on five nets still name the same net indices; and the GND 34 -> 44
measurement, the native and CLI finding counts, the zone counts and both
documents' withdrawn-status headers are untouched. A pattern scan of both files
for the pad reference, the supply net names and the generated net name now
returns nothing.

Both documents are staged, so the reference to `phase5-kicad/RESULT.md` in
`patches/engine/README.md` resolves and the public record keeps its phase-5
account.

## 3a. Finding F2, accepted as it stands - and 48 patch-format findings

`git diff --check` over the tracked modifications is clean, and it is the check
the phase gate and every earlier phase used. `git diff --cached --check` over the
staged set reports exactly two classes, and Astra accepted both for this backup:

* **48 findings inside the five `patches/engine/*.patch` files** (6, 28, 9, 3 and
  2 by patch, all "trailing whitespace" or "new blank line at EOF"). These are
  unified-diff files: a line beginning `+ ` is the instruction to add a line
  containing a single space, which is the upstream source content the patch
  reproduces. The warning is about the patch's payload, not about whitespace this
  repository authored. Rewriting those files would invalidate the hashes phase 28
  and 29 recorded for them and the byte-equality the patch group proves, so they
  stay exactly as their acceptance froze them.
* **One trailing space in an authored source file**, `pcb_world/agent/zone_coverage.py`
  line 1040 (`key, net, True, "none", (), ` - a space after the comma, inside a
  multi-line call). Astra accepted it for this backup, and this task left it
  exactly where it is rather than reach into another task's accepted module.

No new finding of either class appeared during the correction cycle.

## 4. The allowlist

The staged set is exactly the paths below - 170 files plus this page. It is the
whole public harness contribution and nothing else: no private board evidence, no
build output, no virtual environment, no crash log, no cache. Sizes and SHA-256
digests are of the staged bytes, and the list is what
`git status --porcelain=v1 --untracked-files=all` reports after the correction
cycle. The table was generated, not transcribed: each row is the staged blob's
byte length and digest, and it is reproducible by re-deriving the same manifest
from the index and comparing it row for row.

| path | bytes | sha256 |
|---|---:|---|
| `.gitignore` | 3631 | `6ffe0f1c2380fd51c0e818901336915b6a120418e9671dfa5b6c62097004f4a0` |
| `CHANGELOG.md` | 129078 | `27387f60dc26bdfc0c0e9dc6efeba281ff64b499081a1dcdbb3bdd1e49018f3c` |
| `HISTORY.md` | 194527 | `188bda9e06e568617ec894ae9d98acfa6dec9e7cef58010c0f89eb5156804d60` |
| `README.md` | 5130 | `4f66cb24f1dc0364de45ac0a7123d2811e48a1f912349b0fe658d06961a14a5c` |
| `docs/agent-work/reliability/CHECKPOINT.md` | 10859 | `022435485510e83f244ef915dfcae473119e8d47614acd483d8f6b046b978b03` |
| `docs/agent-work/reliability/PARALLELISM_LEDGER.json` | 114692 | `ff1d8979f7d27646c37263a66e0fa8b14057638b38c53fe5ff33ec57fa70fdab` |
| `docs/agent-work/reliability/PLAN.md` | 20176 | `46cbb910ff145b7703c3a70a45adbda7786bca3f108901267f8d213489671e61` |
| `docs/agent-work/reliability/README.md` | 5917 | `512f6f7ee62ad03135038899fc0710ba7e209f2f586aa7cb4f0bba53b09ce152` |
| `docs/agent-work/reliability/RESULT.md` | 17689 | `ea109e0081de588a1893526d91689972dec6fca4fec2083fa8b1e6a5dac17f1b` |
| `docs/agent-work/reliability/evidence/phase_check.json` | 5400 | `38040a0f6cb645e6f59bb41903fd5796913df675e5c87c43d2c30e3851f6ae25` |
| `docs/agent-work/reliability/phase10/PLAN.md` | 4534 | `afca6df6f713d78e27814d7aebc40b3dc826f56724b97c224266c805ff6d4002` |
| `docs/agent-work/reliability/phase10/RESULT.md` | 6873 | `6c90a1af8b475a7c3afe6fe9b11871111e222475327fd0cc5ec465517fd95580` |
| `docs/agent-work/reliability/phase11/PLAN.md` | 6160 | `eeefaeeda0fe3b8fcc2fd16dc10446eda163e95ba1fc2d169dd0fca1969a056a` |
| `docs/agent-work/reliability/phase11/RESULT.md` | 19824 | `35a0caa43cab6e14758d596d26a1f0e2044523a88c560c892cf2c66df876cd3e` |
| `docs/agent-work/reliability/phase12/CHECKPOINT.md` | 1452 | `b910346e95806eb5a26212805267aa985e0301ac9f8d677a4bf75697117f57ac` |
| `docs/agent-work/reliability/phase12/PLAN.md` | 4675 | `d8cd7f7142ce3d0142c8c796a76d354a082c4b4333d75b9bdc212dc8d86ba9f6` |
| `docs/agent-work/reliability/phase12/RESULT.md` | 10307 | `15bb2517631f7c009da1a6c5439680a1216f5523e8e3a4ff386f82cc0338e836` |
| `docs/agent-work/reliability/phase13/PLAN.md` | 3163 | `78c5ddb2f7af177b9d8b0b938bdf8a92468c872f7db75b0229b6340a80b40d9e` |
| `docs/agent-work/reliability/phase13/RESULT.md` | 6798 | `1a5a1d242c8aa480625c7ee4f51d9fcf43bd50f090def93b596208b7fbc27a82` |
| `docs/agent-work/reliability/phase14/PLAN.md` | 3761 | `a90fceb46e3f3e68b72654672f0dc0551084f7f0fdc330c7ada1dd34f8ee6b5f` |
| `docs/agent-work/reliability/phase14/RESULT.md` | 9196 | `7984b3919c8eafcea492d2d730bef9e22f7e7612283ca8a1ef59f105091fba60` |
| `docs/agent-work/reliability/phase15/PLAN.md` | 2701 | `23345d4cc4b8ffdc5fb7cd2c6ab36ea110a6be3ece986f9c54e85f0a4e8d77ef` |
| `docs/agent-work/reliability/phase15/RESULT.md` | 9190 | `824f7537d6a99c7e79001ee6cdabe86c21ed3af1ac401dfab7e871c7cb36a59f` |
| `docs/agent-work/reliability/phase16/PLAN.md` | 2877 | `27aa7d7221326ab72ff2d44ffffe9ae9664fe762215fe1ad8168b5239f60d8aa` |
| `docs/agent-work/reliability/phase16/RESULT.md` | 7807 | `834c8f627d436b86de001d199343061443a29c4e5b60aabe94477e1e1b7c4bee` |
| `docs/agent-work/reliability/phase17/PLAN.md` | 3453 | `cb71920e722ab43e972f53fabf0bebc3f6ec17d9c24de644f747d12871ed6de7` |
| `docs/agent-work/reliability/phase17/RESULT.md` | 8909 | `595d6899705a05c3d156bc95021f17723b6003611c64c8821a070abbbc6a2c7d` |
| `docs/agent-work/reliability/phase18/PLAN.md` | 1970 | `4b75ddfe63aee1ba5312e1df6ad9ee4eb8e248405ff173dd6f6ffede5f9e8376` |
| `docs/agent-work/reliability/phase18/RESULT.md` | 12888 | `b98758da03d2a44f9fd9cfaed2d44d9ec0665a3a40ba9f1469f0ee5776994e46` |
| `docs/agent-work/reliability/phase19/PLAN.md` | 2546 | `777f2910b094a4a5d2b6e3dba1132d056bc41744aec0bd88bf22b3b8bfc15f56` |
| `docs/agent-work/reliability/phase19/RESULT.md` | 12539 | `ddb0bd145624f732af3496ae17ff85b4fa9571a99a01a60b78a3dac22778f4c8` |
| `docs/agent-work/reliability/phase2/CHECKPOINT.md` | 4097 | `30455377eed59e692f39e84527bc9a9786b735c5add639be2f1f67ba6327b03d` |
| `docs/agent-work/reliability/phase2/PLAN.md` | 4521 | `113bd11f1cf140b39a217198e73f91e51995fd1ca7eeb8accdaf62cf8b0ae4a8` |
| `docs/agent-work/reliability/phase2/RESULT.md` | 13184 | `6ddb7485ce331c261ecd3b511e280114373a17885da913d7362d45343d88dfc7` |
| `docs/agent-work/reliability/phase20/PLAN.md` | 2234 | `0137ff4734e60a466e7dde6af9b8260b5da1b544e8303ef53652a06ead46f9e8` |
| `docs/agent-work/reliability/phase20/RESULT.md` | 14105 | `ee7251220ac3d9eaf7b6f382502411012b2b0d135485d22f5ab97cfe1d4a9f6d` |
| `docs/agent-work/reliability/phase21/PLAN.md` | 2297 | `01df29d2abc588294d27202439fe1a5793a4ed9cee9bf51c8a82382552368825` |
| `docs/agent-work/reliability/phase21/RESULT.md` | 12889 | `49ba8055be9cd668cd3ccf6151b2c18b3c73455cd8f025ae463abf4e2dc8fcaa` |
| `docs/agent-work/reliability/phase22/PLAN.md` | 2162 | `70a7a19552b71fbbb150c44eafb18e23d9052d85b9a10df42c37f1a1fc12b827` |
| `docs/agent-work/reliability/phase22/RESULT.md` | 13443 | `2451a85b8079e84a6a9c13d8d242cdbbd8133967aec778b5a5eb10bb82a67954` |
| `docs/agent-work/reliability/phase23/PLAN.md` | 2152 | `eec933fbea5ab69a5d7525c9cf5d32859cf2e30ca514de406ca616613106df99` |
| `docs/agent-work/reliability/phase23/RESULT.md` | 12640 | `b68b34a5d2cb929600a08bd5363034c92331272ec84379dc892e20b48e0fc92b` |
| `docs/agent-work/reliability/phase24/PLAN.md` | 2222 | `39a08af3733f44ca7f5aea86c46b6dce0c055ed5dadfaa4f2b8f8c29d38db992` |
| `docs/agent-work/reliability/phase24/RESULT.md` | 21216 | `a26c8da1e04dd39dfacf5b6ccd9f4912c9388087e6d51c815fd08f6ba46c1c22` |
| `docs/agent-work/reliability/phase25/PLAN.md` | 3039 | `83a62e90b7059ce51cc76baa8c8ec28e3e27e7c61eb273e7365492dfde1d6fac` |
| `docs/agent-work/reliability/phase25/RESULT.md` | 19809 | `fc2832320f85468b0023018191e16710c69722004be2dad9d5b2f9343a45182e` |
| `docs/agent-work/reliability/phase26/CLI_ATTRIBUTION.md` | 7972 | `b9ff098830df43c2ab2aae8fb47d63fd478a9a8f9b75ce8f81159e9f7e0b2f7f` |
| `docs/agent-work/reliability/phase26/PLAN.md` | 6000 | `5e3d7b666911aaba23245c5e139f30151d4feb8765d7fe73d779b43cb8b3934a` |
| `docs/agent-work/reliability/phase26/RESULT.md` | 17731 | `7b206f137bd3ad2f480e33559a60a1b28a3ec172c528df1b309c97e002a72dbc` |
| `docs/agent-work/reliability/phase27/SHORTING.md` | 14241 | `9b461e9d634dc49fddeab6ca1b4d80971c39191b50e6adc6a9f6c224dfbe9271` |
| `docs/agent-work/reliability/phase27/VERIFIER.md` | 22967 | `9f5d7d6eeeff8d01be04f6a291ecae104c5d54369b3e6b5b17438f4e8b5b97c2` |
| `docs/agent-work/reliability/phase28/PLAN.md` | 4459 | `9a98eebc5a007d55f37f5232a72009257dc87bb24dfe53825fb07b867ffe6f42` |
| `docs/agent-work/reliability/phase28/REPLAY.md` | 6899 | `cce3372c1a8241cc72fada47a7359e42e2437b753ed44afa6d2daf403d6ca647` |
| `docs/agent-work/reliability/phase28/REPLAY_REVIEW.md` | 8566 | `74b2337415b133800bd00ebb0c236534cc4f8381d581c2dd1bde7a40ac4fa72a` |
| `docs/agent-work/reliability/phase28/RESULT.md` | 13060 | `498bf142967cc217530dd32ba21db25cf6edf4c25c8e8f87940c9af5dffb287f` |
| `docs/agent-work/reliability/phase29/EVIDENCE_VALIDATION.md` | 9368 | `f9b570520bed3aad3ffd68099b342580fdcdfa8b0831f329494b0464eebe351b` |
| `docs/agent-work/reliability/phase29/EVIDENCE_VALIDATION_REVIEW.md` | 9410 | `f99ce9b9889e33b213cfcb9c72dd26b55336f49013f8ed8b6452cb8141958243` |
| `docs/agent-work/reliability/phase29/NETLESS_ORDER.md` | 15033 | `b8b38566f4d5b0ff6a730586013c18e520e341f8b845417e1be69167d353729f` |
| `docs/agent-work/reliability/phase29/PROJECT_POLICY.md` | 12848 | `be07aa3903143b721eb01a5d75cce6333fe2d5506ea33b289635c7e36095778a` |
| `docs/agent-work/reliability/phase29/REBASELINE_REFRESH.md` | 30220 | `5435a97664338184f8f956957de66a53e5c27b167cc57b84423fa6470f9786a5` |
| `docs/agent-work/reliability/phase29/REBASELINE_REPLAY.md` | 12240 | `a4613dbd3155a94b940d44764cbdb4b148718c4286728ad219269ab2cf7ae08d` |
| `docs/agent-work/reliability/phase29/REBASELINE_REVIEW.md` | 6608 | `4ffee34bb1a2ab679755ebad2344c6c3287bcd9bb99b9632a78159dc0b88d693` |
| `docs/agent-work/reliability/phase29/REPAIR.md` | 11569 | `0df6b425e56c926a2ffdac0c2e4a52c0f48cbc488aefc3c6a1207b41d1098e69` |
| `docs/agent-work/reliability/phase29/REPAIR_REVIEW.md` | 16749 | `5e77445009894f3bd80aa0655a829eae1a5671d2ffec2ac1ce9efd09b653bcf8` |
| `docs/agent-work/reliability/phase3/CHECKPOINT.md` | 4242 | `78eb399795e968804ac5f984f3e44c6427eebecf708b2d47e751c1edcd20ba26` |
| `docs/agent-work/reliability/phase3/PLAN.md` | 4064 | `917b4c52c4ae860bdf9d1387d983f1391428c614a27693293ec663a78fbe26ab` |
| `docs/agent-work/reliability/phase3/RESULT.md` | 14593 | `23ab640335ad0efbee6df563ddc527cb57c53fb2e591b23a8970501fa1fef63b` |
| `docs/agent-work/reliability/phase4-recovery/CHECKPOINT.md` | 4211 | `75dd4f3b317fe4702b29694a891d9dfbbfe67f201d034106d0a1edc6c57f452d` |
| `docs/agent-work/reliability/phase4-recovery/PLAN.md` | 4293 | `492a276446c8e81c0b5426fdc994fefba78a8e98e877d5b73288545cfe7c25ce` |
| `docs/agent-work/reliability/phase4-recovery/RESULT.md` | 6447 | `9d973ec9a46003d967d2c0c08bedea7019cc3cc6f62802b46e6952357761806c` |
| `docs/agent-work/reliability/phase5-kicad/CHECKPOINT.md` | 45514 | `90c48d3943d0343a2ff2581b75b1c5c43f3c8c9b386cc0b29beeeaea4d302b6b` |
| `docs/agent-work/reliability/phase5-kicad/PLAN.md` | 3595 | `744b274920bdb3a48a1d5fbd961ede1de3a045227db398418c7f72585dd55845` |
| `docs/agent-work/reliability/phase5-kicad/RESULT.md` | 24347 | `79c9671280c997efd2564805c0a06e2208aed6e3fe6968004d1ebd5b2049d82f` |
| `docs/agent-work/reliability/phase8/DECISION.md` | 14678 | `770742a871d192397b9e45c37501a4c207c8b0fd1769340f05882f28495715ec` |
| `docs/agent-work/reliability/phase9/PLAN.md` | 4896 | `704a5dc94288dc4b4f8b1fb69b79add165e519f41c140d8cd554c22bfb19ec1a` |
| `docs/agent-work/reliability/phase9/RESULT.md` | 6179 | `df32aa1d899aec14ec6e1bf4d415a84788fbb961a79dbb24ff81ae886aaf71fa` |
| `docs/agent-work/reliability/phase9/TAXONOMY.md` | 6548 | `a6d1db580def7dc69ca9db8aba9b582674651346036fb334652add805580d993` |
| `methods/llm_agent/policy/structured_agent.py` | 4614 | `245e659e3b211c058260c3660c8734b5b610e791d9deb9410ec0841aab05692a` |
| `methods/llm_agent/tools/__init__.py` | 634 | `9c9aeea458711527ebd46f4a8899b79dcda2d4cf4f3cdd677632e7d4b1e79e55` |
| `methods/llm_agent/tools/pcbworld_tools.py` | 3196 | `437cd180e5b08d1bef4eae1522dbd3339eefb5fc75736647f519dc651173a7fb` |
| `patches/engine/0001-routing-rule-context.patch` | 11205 | `fcbcdc4f1b8dc91437f1fca80da64ab418e3a5e52d59edf73e1491b3128b5874` |
| `patches/engine/0002-phase5-integrity-and-copper-fill.patch` | 34865 | `7dc152d273ea299fc1b96e4256c71354db08d4a5ed1bd89324c6d656852876d3` |
| `patches/engine/0003-zone-point-query.patch` | 25002 | `64136e30ab1ff6fa3471dbe1f0cb8bfece0ebcb1c3ed0d876279e4c0d761d8db` |
| `patches/engine/0004-reporter-cache-canonical-order.patch` | 61846 | `c8a319719928161f0d4bd1646ba23677099aaf9c9a54f1124e57c3d68ae34a63` |
| `patches/engine/0005-netless-first-pair-release.patch` | 6932 | `5bcb3e944eed24ce7813921aec505721566682aab723ad542a0dd5432375eeb2` |
| `patches/engine/README.md` | 13414 | `a794980e8f0f5adbc6f33810aa60a6f9fedb03bf4ab15f37e89722e038822222` |
| `pcb_world/agent/__init__.py` | 3557 | `0c8e9beff47136b82c45d68eddbd8d96e725f9eac05db83a0a16ad07595a8e40` |
| `pcb_world/agent/actions.py` | 27820 | `a74f524285acad0a6315905ce89f1d64eb868390f6ac55268294c7f05cc26db1` |
| `pcb_world/agent/artifacts.py` | 27226 | `636f2245ffb684d4d0f48cdafe50b571f9976712f5a193c670321a17134ae33a` |
| `pcb_world/agent/cli_gate.py` | 66113 | `e3b69e28562c36b649b7bacfccd9a932bd39c7cafbd251f662484cb0fe16a200` |
| `pcb_world/agent/drc_gate.py` | 72379 | `61d085f99e88fa5f5d8f718ba5222165a5b7b1eb22588eae767e6b59c0774bac` |
| `pcb_world/agent/final_gates.py` | 17597 | `a4271b2dd4524e3bc3601ccbfda6f71078a42c5231050baf7a1e2eb10f535441` |
| `pcb_world/agent/kicad_metadata.py` | 35253 | `5b6e8d5b02a993549bd0daf2b5e66843f9f84849ff84719b3fbda2dcdb6ceef5` |
| `pcb_world/agent/observations.py` | 93588 | `3d84f092d07d44ba28a60b0ac9b283128b965928d74374a5bedc2b8abcf1eb6a` |
| `pcb_world/agent/reference_baseline.py` | 7032 | `33f85e4716d95dac72ebb43798dd7e847e3ab8348889ecec7d211bc79c5907ee` |
| `pcb_world/agent/rules.py` | 18513 | `9cb8cde4453f943849f15f9ce6fab7ad1e6fe0125d4e2c00bc8e206232748e73` |
| `pcb_world/agent/runner.py` | 218941 | `36b0408f6addbebc78347f0e533574c8ff9b72d95ddc86e0b2ca60d294cd35a1` |
| `pcb_world/agent/scheduler.py` | 67687 | `b7e2484ae5010dbca487b731fd7dac07763b0e8bd45344858efa80af0b31a5a5` |
| `pcb_world/agent/serialized_metadata.py` | 15056 | `3eb9a3071d99cc0e0729b4b82635c05e30199b48e7d73512c07ff7f769a6e231` |
| `pcb_world/agent/session.py` | 93193 | `e3efb4d6e526cc29abda5dbcffcd154326af596a89984cdbd5fe6be15e5620f9` |
| `pcb_world/agent/state.py` | 12859 | `ac5cce39c3dc58fa352b88a19f47be56aadd2ba17c76c9566525ae614806abaa` |
| `pcb_world/agent/terminals.py` | 12762 | `055fd18d299fae9b7bd5203417dbdb3374dfb0eea5a231e01b4731d752746ac0` |
| `pcb_world/agent/tool_api.py` | 10558 | `37b9fd175851c021a11cec24536f9b4e50c1c4b301d58a076c082e6d7f4b44ae` |
| `pcb_world/agent/zone_coverage.py` | 48056 | `cbd34cf6534a2f6908e72c2f29aaff353393822b4757299a9abf0c76e6056614` |
| `pcb_world/engine/containers.py` | 3889 | `64c400e2fb508202959a600215fb078a3826f9ec8ac75125c621d6d9cea1a1fe` |
| `pcb_world/engine/kicad_engine.py` | 67851 | `b4aa74c9a561d98c9aef0c8dcf84cff670a10718ee053e14d2496282b48a1051` |
| `pcb_world/engine/router_client.py` | 25507 | `e470dd3048addfe9df0ac8b85754c179ccfed41f88f01b1e1b8577df956d6038` |
| `pcb_world/engine/wire.py` | 12432 | `f787f5be4e1f7ff7f1280466bcec4e4db7a69aac796c70521c6ac4a1c50907f6` |
| `pyproject.toml` | 2930 | `9764cbbd5544831515caa07d6ab9d26bc6e2a69a6e443440e642b7b3d424e45f` |
| `tests/agent/__init__.py` | 355 | `82f67c705949cb91275255669f5937347870412ca54d939cbe030e39bf1de7b2` |
| `tests/agent/conftest.py` | 4637 | `b92e59d06e5861227c9639c73cf4d36a51929d788886fa5491f9501f5f3e9fef` |
| `tests/agent/fake_engine.py` | 21614 | `404e866a9bfbfa21f665f8046a3d7dad1087788de052d8da0ea7f9ba0ef290e4` |
| `tests/agent/synthetic_boards.py` | 11964 | `22c57fcfb24f34501128a305d76b2e91193e205960cd12bfb15ae285398e1acb` |
| `tests/agent/test_artifact_store.py` | 10551 | `5b20207a53699b96a1d8bec998d8c613754940c2c9de9ae6933f74fb56fb842c` |
| `tests/agent/test_cli_gate.py` | 49364 | `8559250578de1d24578753c5b34d3bf4652ee3cfcef79c567f19a3d2f8bc9969` |
| `tests/agent/test_coverage_and_clearance.py` | 34785 | `ae15295b774bc2b12f931665a3947b8db098597fa81e3f728128221a337d6716` |
| `tests/agent/test_drc_classification.py` | 12540 | `b9cbec5ff9db83cee5934da23fd6cda4bc1ca47de56cb049b3e48a7498934c08` |
| `tests/agent/test_drc_inventory_identity.py` | 20080 | `a5b06456f2cb615dcdaa1752f90bb667a60507b7ec2a8dcb1c6a0e92bb80f574` |
| `tests/agent/test_evidence_validation.py` | 30451 | `890f1437b6fb8e898fcdd62e3433baad2b21f95d68a7c5e49e1d3a3eb8f6370f` |
| `tests/agent/test_failure_taxonomy.py` | 23522 | `d6bb2e285ab861aa4a4736171ed4e3fedc16c8ff292901941a90468274d689f3` |
| `tests/agent/test_fault_injection.py` | 28353 | `91dff523382e8b76a7a60e514aedfd8163d672dcf823d2486f1548150283f5c9` |
| `tests/agent/test_final_gates.py` | 11190 | `85f634b84ed482de3a9debe55808a04b3f2690a879772da3c6a52416be329ce4` |
| `tests/agent/test_free_via_search.py` | 12666 | `6506ff5f2cfe54e5382d4a993a40a8131aaf5ed6d0f57660479e46bf973a65ce` |
| `tests/agent/test_gate_integrity.py` | 5748 | `811950635a80d1f729945fb477703351fb62bd3261ae44f11bbe012267977bf6` |
| `tests/agent/test_integration_gaps.py` | 9908 | `e22539b43456cfc2519ffe29c47e87d0cca07e6a013934671ad2bfcdb3e0876f` |
| `tests/agent/test_kicad_metadata.py` | 12991 | `90d8409deba04ae460c33e3ea6e49780b3b126788d817f8e93f0d28321ff2e03` |
| `tests/agent/test_modes_and_validation.py` | 14361 | `6e8f00650f03d186aa97da81466626990649ce9b0cb8c71c345f68b1b7b638f1` |
| `tests/agent/test_native_component_graph.py` | 22060 | `02455537537c29eb1082b2b5891ccb399a036ec69a9e097ce7c54eb102045f7e` |
| `tests/agent/test_native_contract.py` | 13513 | `2e5b6a4ec1dbcb3cb0ad59cdeeaeff44328f80a067de27ce680fd16162f357c2` |
| `tests/agent/test_native_drc_enum.py` | 5632 | `0256df9e9cb9cfd26cee38cc0c9aad8ebe0e184ea5c0cda2bc5e1f59278b7f97` |
| `tests/agent/test_native_drc_incremental.py` | 13075 | `b27d9a73d4cd890110ee0c41b2b437f174f9db82b2a513988bafd5596267d2ad` |
| `tests/agent/test_native_fill.py` | 8808 | `f58fbbf4360b2ef112f3473d424026d5755a9f6c1f015f6b7541ad4c5eeb4aa7` |
| `tests/agent/test_native_final_gates.py` | 11836 | `3d2b873ab40e7deda68bab446ef8717029e03d1c610b2ed7926bbfbee4def508` |
| `tests/agent/test_native_item_inventory.py` | 8185 | `db326981b34eed15363af3c48d0095fc26866dea0340f44785f63427b36ffd58` |
| `tests/agent/test_native_layers.py` | 6073 | `e6487fbc8cb992f8be76d94bcae913a390e6848afac3f15bff2e97fc27b12e07` |
| `tests/agent/test_native_pour_endpoints.py` | 12835 | `db1efd3d8fa0f47b73723aae8fdc317dee05f59de629249e8e5299b124144cfa` |
| `tests/agent/test_native_rules.py` | 8594 | `5d80da162a556226caf0dbb6c2ce83c84ca1abbc7a146bafcd2f4a28ca97e33c` |
| `tests/agent/test_native_runner.py` | 10391 | `5e25996e9f95b8f00dbd7236464686b49bf73cd745d7fadb4d248168fdc5944a` |
| `tests/agent/test_native_terminals.py` | 3109 | `6c22213fee73ab9a7353a0ee2d01dcf1104385ad3a80e50948e882294e93db7b` |
| `tests/agent/test_native_via_sizes.py` | 19137 | `7eec5063b37700f1c3680634ce0edf804fbd537f2744e6431256074eb28c254a` |
| `tests/agent/test_owned_engine_deadline.py` | 6733 | `3993927d1f07fea554f1aaa99e5db4e0fe26260ec59d998ba3a93db3357b6a5a` |
| `tests/agent/test_phase3_integrity.py` | 30344 | `9e1876935b39a56bcbf5ae2d4ca30a8a3fc062fc10e9eb487a7be9c9eecdbbdf` |
| `tests/agent/test_planner_client.py` | 14052 | `3a46d9608a6a4c606062e1c2e7a968a83ebdc4de8c2f163f0e9023ed273d8439` |
| `tests/agent/test_reference_baseline.py` | 5042 | `fb63ffdf9baaa3bdaf861906363fc31f2017dfdbfdabb436a60c187a3434f6b8` |
| `tests/agent/test_runner_scripted.py` | 40881 | `de5ed8069789cdd39c66393995fe96b22b755b713f221ea9f0608e61acc7b579` |
| `tests/agent/test_scheduler_unit.py` | 20667 | `6e9e2cb9227bf926ac33c58194c392dd76dc5f0a9d7ef5f6d679d9d8f55815c8` |
| `tests/agent/test_serialized_metadata.py` | 7235 | `96469add4b02e59dcd7cdb187ef43689f3119b890e25438c679247ed68249a1b` |
| `tests/agent/test_state_and_rules.py` | 17073 | `ac3b7ba0fd4ffda2a78a28835d1da91d017ce085b9a0ad4bfab7044d12186ad4` |
| `tests/agent/test_terminals.py` | 6444 | `4ace2b71b57c12b7293d52930b8401b779b1dc57c68a2919a8bb8bad2e0065fb` |
| `tests/agent/test_tool_api.py` | 5155 | `7ffbace645c84fcb46e819ceaa579f9390a87dcd5df3ebf9fcb23310fb04cb03` |
| `tests/agent/test_transaction_unit.py` | 18186 | `572907de47c178bc8e8a11e037824f43242f8c10ddf425483eca3316f6201a16` |
| `tests/agent/test_zone_point_query.py` | 33977 | `20e4bc7094c24ec62861b35ef58d40974ff9e428ca4cd578f8ffc3c345515f93` |
| `tests/agent/test_zone_prefilter_unit.py` | 29986 | `2ae9bdcc2ce3c0f98e70132cc3921619f03df47d299b7f43b3d80cdf4f31b7ad` |
| `tests/engine/__init__.py` | 76 | `333104b1ddc7e6d0c1a01287fe5df5445d8397c16c2c02d1afba4ebdc5a50a53` |
| `tests/engine/reporter_fixture.py` | 18045 | `2d4129f4c557e0cb80f0f7ea3162c8eff4233b1211c9740b00c203e1223825f1` |
| `tests/engine/repro_netless_first_pads.py` | 4854 | `a41b7b19ef87cf4e3ecf06a586c434d8302b0c5d325dc9441fb7c5b8d70c4ed9` |
| `tests/engine/test_phase_gate_groups.py` | 2888 | `33faf8e1063cd303edff94b773911df98d3bee0c183a953cc5ebaa2fc40973f6` |
| `tests/engine/test_reporter_cache_canonical_order.py` | 16103 | `23b3634b5ffeefcccecb904eaad45edbb7057f17abd0f3cce3761c9691093d8f` |
| `tests/test_engine_api/test_router_provenance.py` | 23560 | `efcadba20ec6329d377cd68336470a3cda3b78848ae99d3fc6969fa543f1b78b` |
| `tools/reliability/check_engine_patches.py` | 6612 | `397b03b98d3379267030ec2cf3579c82ec1a609c8fa576658a18f4617719839d` |
| `tools/reliability/check_phase.py` | 14095 | `146e5db192a7f21b2bf793360548133466270a056c847d201641c536490a9d3f` |
| `tools/reliability/check_phase.sh` | 786 | `1c64f2fd52ac4d52b00e194c7d198ff06db6768357a2bd9921c032f30a385717` |
| `tools/reliability/demo_structured_actions.py` | 3426 | `53bafef222cc5b4d66669587faacca170267a805578f8bbc42f90129247ba630` |
| `tools/reliability/drc_incremental_differential.py` | 23563 | `c883366cb19f09eb952810e5258bd40a211042fd61a3138da12a8582a4759517` |
| `tools/reliability/failure_taxonomy.py` | 56808 | `5a1f3f83bbb64555911871798fd050f81d0ab59af9f0dfc32f79496835a08053` |
| `tools/reliability/profile_attempt_cost.py` | 12032 | `f399884ab5430e886ef3d00f0d9882ccac1bdb38b78653e95f7617c57c9ac17e` |
| `tools/reliability/route_agent.py` | 11094 | `617f8b8090e0bc63c5ae9bbb876f37968120f3161d801d7cd8ebf0152b0e7ae2` |
| `tools/reliability/run_final_gates.py` | 6053 | `a7fa8aa929f9b9b0efa20d77da6fc9dc446557055796e1f891d3f0ee2015ecb5` |
| `tools/reliability/screen_zone_openings.py` | 5442 | `4f0910064563d1def40d0f103c76bb71609d8e96cd369e372d2eb7955c544131` |
| `tools/reliability/verify_saved_artifact.py` | 16917 | `577cbfb5e55b3e5b779071b0ee2b09efa0b4462bdba0438acd8b52b3c9fd2b24` |

## 5. Version

`pyproject.toml` moves from `1.0.1` to `1.1.0`, and `README.md` line 7's
`<!--VERSION-->` marker moves to `v1.1.0` with it, so the marker and the package
version agree. The comment above the version names the marker as the single
source of truth, which is why the correction cycle reconciled both rather than
leaving the file the first packet had flagged. `README.md` carries no other
version string, and nothing else in it changed.

## 6. Staged diff

* **Paths.** 171 staged paths: the 170 files tabled in section 4, plus this
  page.
* **Staged diff over those 170 files.** `170 files changed, 63258 insertions(+),
  31 deletions(-)`, no binary files, matching `git diff --cached --numstat` over the
  allowlist.
* **Staged bytes over those 170 files.** 3,187,243 bytes, equal to the sum of the
  byte column in section 4.
* **Manifest.** Section 4's digests are SHA-256 over the staged bytes and were
  re-checked against the index (`git rev-parse :<path>` -> `git cat-file blob`)
  after staging: 170 of 170 match, no path missing, no path extra.
* **The strict run still covers the staged code.** Every staged source, test and
  patch path has the same SHA-256 it had when
  `bash tools/reliability/check_phase.sh --strict --expected-native 146` exited 0
  (36 source, 50 test and 6 patch paths, 0 changed). The only paths that differ
  from the state that run measured are documentation and metadata:
  `HISTORY.md`, `CHANGELOG.md`, `README.md`, the two phase-5 documents, and the
  ledger's authorised write-path extension. The staged evidence file is that
  run's own output.
* **Not embedded, and why.** This page's own digest and the index tree hash
  (`git write-tree`) are captured in the T29I packet immediately after the final
  `git add` and are reproducible from the staging; a file cannot contain the hash
  of the tree that contains it.

## 7. Observation O2 - the private workspace path in published documents

Nine staged documents name the private board workspace by absolute path - 16
occurrences, starting at phase 2 - and two further staged documents name the
board project without the path (`docs/agent-work/reliability/PLAN.md` and
`docs/agent-work/reliability/PARALLELISM_LEDGER.json`). All of them name the
workspace, never its contents - the pattern every phase used to say where the
private evidence lives - and they carry aggregates only. This scan reports it for
completeness rather than as a finding: the path is part of the reviewed public
record from phase 2 onward, it exposes no board geometry, net name, identifier or
measurement, and this task does not own those documents.

The accepted convention for a board reference in public text is a digest prefix
(`6c4f8ab81b83…`), which cannot be reversed into geometry and is what the
verification bindings need. Net names, component pad references, item UUIDs and
coordinates are the classes that stay private.

## 8. What the backup would be

One commit on `feat/agent-reliability-actions`, staged with the exact allowlist
above, with the detailed message the change deserves, pushed to the existing fork
`JaredReabow/PCBWorld` - the repository's `AGENTS.md` requires every version to be
backed up to GitHub, and this is that backup. No force-push, no rebase of
published history, no tag. If the remote or the credential fails, the local
commit is kept and the exact blocker is reported instead of retried
destructively.

## 9. Limits

Staging is not acceptance: this page records what was staged and checked, not a
judgement that the layer is correct, which is Astra's. The strict harness proves
the staged source against the pinned native build on this host; it says nothing
about the private board, which was not opened. The leak scan is a pattern scan
over the allowlist, and it cannot prove the absence of a private fact that has no
distinctive token. The two phase-5 documents were corrected only where the scan
could name a real-board identifier, so this page claims a clean staged set under
those patterns, not that every phase-5 sentence has been re-derived from its raw
evidence.
