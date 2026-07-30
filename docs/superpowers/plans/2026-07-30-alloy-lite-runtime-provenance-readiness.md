# Alloy Lite Runtime Provenance Readiness Plan

Файл плана: `docs/superpowers/plans/2026-07-30-alloy-lite-runtime-provenance-readiness.md`.
Он создан в новом session-owned worktree; старый worktree другой сессии остаётся read-only.

**Цель:** гарантировать, что Vexa Lite запускает именно тот исходный код или опубликованный digest, который заявлен, и обнаруживает любое расхождение между checkout, image и container.

**Архитектура:** `source-identity.sh` остаётся единственным владельцем fingerprint исходников; `provenance.sh` — единственным владельцем lifecycle и проверки runtime; Makefile предоставляет только публичные команды. Никакого дублирования fingerprint/status-логики.

## Definition of Ready

Ветку можно считать готовой, только когда одновременно выполнено следующее:

1. `ALLOY_LITE_PROVENANCE=1` включается только явно; unset/`0`/прочие значения сохраняют старый `make lite`.
2. `.env` не может незаметно активировать режим.
3. `lite-dev` связывает чистый текущий commit с точным image ID.
4. `lite-published` запускает RepoDigest, а не mutable tag.
5. `lite-status` достоверно различает `MATCH`, `STALE`, `LEGACY`, `UNHEALTHY`.
6. Изменение исходника делает текущий runtime `STALE`.
7. PostgreSQL, MinIO, Whisper и volumes не пересоздаются при замене app-container.
8. Focused tests, mutation-контроль, Lite gate и полный Linux gate зелёные.
9. Реальный Docker witness выполнен на финальном SHA, а не на предыдущем fixture.
10. Worktree чистый, `main` является предком feature-ветки, evidence честно разделяет unit, runtime и product claims.

## Этап 0 — безопасно перенести работу на актуальный `main`

Старый worktree не редактировать. Создать session-owned worktree от текущего `main`:

```powershell
git -C F:\vexa worktree add `
  F:\vexa-alloy-lite-provenance-ready `
  -b agent/alloy-lite-runtime-provenance-ready-20260730 `
  main
```

Перенести только 13 известных файлов:

- `Makefile`
- `deploy/lite/Makefile`
- `deploy/lite/README.md`
- `deploy/lite/tests/README.md`
- `docs/ALLOY-CUSTOMIZATIONS.md`
- `scripts/gates.mjs`
- `scripts/gates.test.mjs`
- `deploy/lite/bin/provenance.sh`
- `deploy/lite/bin/source-identity.sh`
- `deploy/lite/tests/test_lite_provenance.py`
- `deploy/lite/tests/test_source_identity.py`
- changelog fragment;
- evidence-файл.

Для семи tracked-файлов использовать patch от старого `HEAD`; шесть untracked-файлов добавить через `apply_patch`. Затем сравнить SHA-256 всех 13 файлов между старым и новым worktree.

**Выход:** новая ветка содержит актуальный `main` и идентичный provenance-diff. Старый worktree сохраняется до первого проверенного коммита.

## Этап 1 — закрыть PNPM/cache hygiene

Изменить:

- `.gitignore`
- `.dockerignore`
- `deploy/lite/tests/test_source_identity.py`

Добавить `.pnpm-store/` в оба ignore-файла. Добавить тест:

```text
test_pnpm_store_is_ignored_and_does_not_change_identity
```

Тест создаёт `.pnpm-store/v11/cache-entry`, после чего fingerprint и `dirty` должны остаться такими же, как у чистого fixture.

Проверить:

```powershell
git check-ignore -v .pnpm-store\v11\probe
pnpm store path
```

Ожидание: путь PNPM — `F:\.pnpm-store\v11`, проектный store игнорируется.

## Этап 2 — усилить контракт source identity

Файлы:

- `deploy/lite/bin/source-identity.sh`
- `deploy/lite/tests/test_source_identity.py`

Добавить characterization/regression-тесты:

- `test_staged_and_unstaged_worktree_bytes_drive_fingerprint`
- `test_deleted_tracked_file_changes_identity`
- `test_symlink_target_changes_fingerprint`
- `test_unmerged_index_is_rejected`
- `test_wsl_windows_pointer_preserves_full_identity`

WSL-тест должен сравнивать не только revision, но также `dirty` и полный fingerprint между обычным и Windows-created worktree.

Если тест уже проходит — реализацию не переписывать. Если падает — исправлять только `source-identity.sh`, без второго вычислителя или абстракции.

Проверка:

```powershell
wsl.exe -d Ubuntu --cd /mnt/f/vexa-alloy-lite-provenance-ready -- `
  python3 deploy/lite/tests/test_source_identity.py
```

## Этап 3 — закрыть lifecycle и status edge cases

Файлы:

- `deploy/lite/bin/provenance.sh`
- `deploy/lite/Makefile`
- `deploy/lite/tests/test_lite_provenance.py`

Добавить тесты:

- invalid opt-in: `0`, `true`, `yes` сохраняют legacy path;
- exact `1` требует непустой `APP_IMAGE`;
- неверные OCI labels запрещают запуск собранного image;
- отсутствие RepoDigest останавливает published-path до `up`;
- несовпадение expected image и container image даёт `STALE`;
- отсутствующий legacy-container даёт документированный `LEGACY`;
- stopped/unhealthy container даёт `UNHEALTHY`;
- положительный JSON `MATCH` содержит image ID, container ID, mode и health;
- source drift между двумя fingerprint-read запрещает `up`.

Также выполнить:

```powershell
wsl.exe -d Ubuntu --cd /mnt/f/vexa-alloy-lite-provenance-ready -- `
  bash -n deploy/lite/bin/source-identity.sh

wsl.exe -d Ubuntu --cd /mnt/f/vexa-alloy-lite-provenance-ready -- `
  bash -n deploy/lite/bin/provenance.sh
```

Существующий `green-on-empty` для полностью отсутствующего `deploy/lite/Makefile` не менять в этой работе: это прежний gate-контракт и не является дефектом provenance.

## Этап 4 — привести gate и документацию к фактическим гарантиям

Файлы:

- `scripts/gates.mjs`
- `scripts/gates.test.mjs`
- `deploy/lite/tests/README.md`
- `deploy/lite/README.md`
- `docs/ALLOY-CUSTOMIZATIONS.md`
- changelog fragment;
- evidence.

Действия:

1. Убедиться, что `gate:lite-makefile` запускает оба новых Python-файла после их добавления в Git.
2. Сохранить mutation-контроль, ломающий provenance label.
3. Исправить README: не писать «все nonzero verdicts», пока каждый заявленный случай не проверен.
4. Зафиксировать точные default/enabled/rollback значения.
5. Не утверждать, что unit/fake-Docker тесты доказывают настоящий runtime.
6. Заморозить tracked evidence до финальной сборки.

Финальный live-readback нельзя дописывать в fingerprinted tree после сборки: это немедленно сделает runtime `STALE`. Его нужно записать в observation bundle задачи/PR с указанием финального SHA и сырого вывода.

## Этап 5 — focused candidate gate и первый коммит

Запустить:

```powershell
git diff --check

wsl.exe -d Ubuntu --cd /mnt/f/vexa-alloy-lite-provenance-ready -- `
  python3 deploy/lite/tests/test_source_identity.py

wsl.exe -d Ubuntu --cd /mnt/f/vexa-alloy-lite-provenance-ready -- `
  python3 deploy/lite/tests/test_lite_provenance.py

node scripts/gates.mjs lite-makefile
node --test scripts/gates.test.mjs
```

После green:

- stage только проверенные пути, не `git add -A`;
- проверить `git diff --cached --check`;
- проверить отсутствие секретов и посторонних файлов;
- сделать связные commits без AI-attribution.

Рекомендуемое разделение:

1. `feat(lite): bind source identity to build inputs`
2. `feat(lite): bind Lite runtime to immutable images`
3. `test(lite): enforce runtime provenance contracts`
4. `docs(lite): document runtime provenance workflow`

После первого проверенного checkpoint старый provenance-worktree можно удалить только после сравнения файлов и ancestry.

## Этап 6 — real-Docker witness финального SHA

Перед запуском записать:

- app/PostgreSQL/MinIO/Whisper container IDs;
- image IDs;
- volumes и mountpoints;
- текущий результат `lite-status`.

Проверить published-path:

```bash
make lite-published IMAGE_TAG=v012
make lite-status FORMAT=json
```

Ожидание: `MATCH`, mode `published`, expected image — RepoDigest.

Затем проверить финальный local candidate:

```bash
make lite-dev
make lite-status FORMAT=json
make -C deploy/lite test
make -C deploy/lite stt-smoke
```

Ожидание:

- source revision равен финальному commit;
- `dirty=false`;
- image labels равны source identity;
- expected/actual image ID совпадают;
- health — `healthy`;
- три front door зелёные;
- WAV smoke возвращает распознаваемый текст;
- sidecar IDs и volumes не изменились.

Для реакции на исходники:

1. Временно добавить один non-ignored untracked probe-файл.
2. `lite-status` должен вернуть `STALE`.
3. Удалить probe.
4. `lite-status` должен снова вернуть `MATCH` без rebuild.

Любое изменение sidecar ID, volume, source во время build или неожиданный verdict — стоп и отдельный Expected → Actual → Verdict разбор.

## Этап 7 — полный Linux gate

Только после focused green и real-Docker witness:

```bash
node scripts/gates.mjs all
```

Запускать в Ubuntu/WSL с Git-visible worktree. Если `.git` содержит Windows-path pointer, перед gate выставить переведённые `GIT_DIR` и `GIT_WORK_TREE`; `db-budget` обязан видеть настоящий `git ls-files`.

Критерий выхода: exit code 0 и все 35 групп green. Частичный вывод, Windows-environment failures или работающий процесс не считать успешным gate.

## Этап 8 — review, merge и cleanup

Перед merge:

```powershell
git status --short
git diff --check
git merge-base --is-ancestor main agent/alloy-lite-runtime-provenance-ready-20260730
```

Требования:

- feature-worktree чистый;
- `main` — предок feature;
- runtime witness относится к exact feature SHA;
- observation bundle перечисляет, что проверено реальным Docker, что fake-Docker, а что не проверялось;
- push выполняется только по отдельной команде.

После подтверждения:

```powershell
git -C F:\vexa merge --ff-only `
  agent/alloy-lite-runtime-provenance-ready-20260730
```

Затем повторить scoped post-merge gate, проверить ancestry и только после этого удалить feature-worktree и ветку.

## Не входит в эту ветку

- исправление Google Meet audio transport;
- новые STT-алгоритмы;
- multilingual human acceptance;
- декларативное пересоздание sidecar-контейнеров;
- массовый рефакторинг Lite Makefile;
- изменения API, Terminal UI или архитектурной CALM-схемы.

Для provenance-ветки достаточен реальный Lite/Docker witness. Google Meet остаётся отдельным продуктовым барьером и не должен искусственно блокировать готовность механизма source → image → container.

Исполнение ведётся последовательно в одной активной feature-ветке, без параллельных писателей.
Актуальные факты и границы доказательств фиксируются в evidence-файле.
