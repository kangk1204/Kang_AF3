# 파이프라인 회귀 테스트 기록

이 문서는 `tests/` 아래 회귀 테스트의 근거와 한계를 적은 것이다. 테스트를
고치거나 추가할 사람이 읽는 것을 전제로 썼다.

## 한 줄 요약

```
python3 tests/run_all.py
```

Docker도 pip install도 필요 없다. 등록 회귀만 빠르게 돌릴 때는
`python3 tests/run_tests.py --strict`를 쓴다. release entry point는 등록 회귀,
명명·파일명 통합, mutation 검증, 통계 self-test, Python AST와 shell syntax를 모두 실행한다.

## 왜 만들었나

코드 리뷰와 검증 과정에서 실제로 발견·재현된 버그가 여러 건 있었다. 그 버그들은
문법 오류가 아니라 **AF3 의 파일시스템 규약을 잘못 가정한 것**이었다. 예를 들어
"출력 폴더가 있으면 끝난 것" 이라는 가정은 사람이 보기에 자연스럽지만, AF3 는
추론을 시작하기 전에 폴더와 `_data.json` 을 먼저 만들기 때문에 틀렸다.

이런 종류의 버그는 다음 세 성질을 갖는다.

1. 조용하다. 스크립트가 죽지 않고 잘못된 답을 낸다.
2. 규모에서만 드러난다. 3건 테스트에서는 안 보이고 2000건에서 터진다.
3. 고쳐도 다시 들어온다. 코드를 정리하다 "이 검사 왜 있지" 하고 지우기 쉽다.

자동 검증이 필요한 이유가 (3) 이다.

## 테스트 목록: 각 테스트가 막는 버그

`python3 tests/run_tests.py --list` 로도 같은 목록을 볼 수 있다.

### 완료 판정 (tests/test_completion.py)

| 테스트 | 막는 버그 |
| --- | --- |
| `test_data_json_only_is_not_complete` | `_data.json` 만 있는 폴더를 완료로 판정해, 추론 중 끊긴 건을 성공으로 집계한다. `--mode data` 에서는 `_data.json` 만으로 완료가 맞으므로 단계별 기준을 구분해 검증한다. 크기 0 산출물도 미완료로 본다. |
| `test_compressed_cif_counts_as_complete` | `--compress_large_output_files` 를 쓰면 `_model.cif.zst` 가 생기는데 `.cif` 만 찾아 정상 완료를 다시 돌린다. |
| `test_incomplete_result_is_quarantined_before_rerun` | 끊긴 결과를 그대로 두고 재실행해, AF3 가 `<이름>_<타임스탬프>` 폴더를 새로 만들어 결과가 흩어진다. |
| `test_output_dir_follows_json_name_not_filename` | 결과 폴더를 JSON 파일명으로 찾는다. AF3 는 `name` 필드를 정규화해 폴더를 만든다. |
| `test_runner_and_af3_agree_on_output_folder_name` | 위와 같은 문제를 실행 경로에서. 러너와 AF3 가 같은 폴더 이름을 쓰는지 본다. |
| `test_exit_code_nonzero_when_jobs_remain` | 미완료가 남았는데 종료코드 0 을 돌려줘, 자동화가 실패를 성공으로 오인한다. |
| `test_single_failure_does_not_block_the_rest` | 한 건 실패가 나머지 전체를 막는다. 2000건 중 3번째에서 멈추면 1997건이 남는다. |
| `test_noninteractive_run_without_yes_does_not_execute` | 비대화형에서 확인 질문을 기다리다 멈추거나, 반대로 확인 없이 GPU 를 돌린다. |

### 입력 검증 (tests/test_inputs.py)

| 테스트 | 막는 버그 |
| --- | --- |
| `test_name_collision_is_rejected_before_running` | `A/B` 와 `AB` 가 같은 결과 폴더를 공유해 뒤 건이 앞 건을 덮어쓴다. |
| `test_hangul_only_name_is_rejected` | 한글만인 `name` 이 정규화되면 빈 문자열이 되어 결과가 출력 루트에 쏟아진다. |
| `test_dangerous_names_are_rejected` | `name` 이 `..` 이나 `.af3_incomplete` 면 결과가 출력 폴더 밖으로 나가거나 관리 폴더를 덮어쓴다. |
| `test_broken_json_is_caught_before_running` | 깨진 JSON 하나가 `--input_dir` 순회를 멈춰 뒤 입력이 전부 처리되지 않는다. |
| `test_stub_reproduces_generator_stop_on_broken_json` | 스텁이 실제 AF3 처럼 깨진 JSON 에서 멈추는지 확인. 이 검증이 없으면 "사전 검증이 필요하다" 는 전제가 근거를 잃는다. |
| `test_alphafoldserver_list_json_is_rejected_with_reason` | AlphaFold Server 의 list 형식 JSON 을 넣었을 때 `name` 을 못 찾아 엉뚱하게 동작한다. |
| `test_macos_appledouble_sidecar_is_excluded` | `._*.json` 사이드카가 `glob('*.json')` 에 잡혀 UnicodeDecodeError 로 배치 전체가 죽는다. |
| `test_sidecar_rule_is_consistent_across_scripts` | 사이드카 제외 판정이 세 스크립트에서 서로 달라, 한쪽만 고치면 다른 쪽이 계속 죽는다. |
| `test_legacy_image_without_input_dir_falls_back_to_per_file` | `--input_dir` 이 없는 구버전 이미지에서 배치가 그냥 실패한다. |
| `test_unsupported_flags_are_not_passed_to_legacy_image` | 없는 플래그(`--jax_compilation_cache_dir`)를 붙여 컨테이너가 즉시 종료된다. |
| `test_image_probe_failure_stops_with_reason` | 이미지 확인 자체가 실패했는데 최신 플래그를 추측해 실행한다. |
| `test_mode_requires_matching_flags` | `--mode data` 인데 `--norun_inference` 가 없는 이미지에서 조용히 잘못된 단계를 돌린다. |
| `test_data_mode_does_not_request_gpu` | `--mode data` 에서 GPU 를 요구해 CPU 서버에서 MSA 단계를 못 돌린다. 2단계 분리 전략의 전제가 깨진다. |

### 상태 안전성 (tests/test_state.py)

| 테스트 | 막는 버그 |
| --- | --- |
| `test_foreign_staging_dir_is_preserved` | 고정 이름 staging 폴더를 무조건 `rmtree` 해서 남의 폴더를 날린다. |
| `test_staging_from_other_run_is_not_removed` | 다른 출력 폴더용·다른 호스트·살아 있는 PID·표식 손상 staging 을 지워 동시 실행을 망가뜨린다. |
| `test_own_dead_staging_is_cleaned` | 반대 방향. 종료된 자기 실행의 잔여물을 영구히 쌓아 디스크를 채운다. |
| `test_staging_dir_is_removed_after_run` | staging 이 실행 후에도 남아 다음 실행에서 옛 입력이 섞인다. |
| `test_quarantine_growth_is_bounded_per_job` | 반복 실패가 격리 폴더에 무한히 쌓인다. |
| `test_quarantine_keep_option_is_honoured` | `--quarantine-keep` 을 늘렸을 때 그 개수를 지키지 않는다. |
| `test_quarantine_pruning_only_touches_own_snapshots` | 격리 정리가 소유 표식을 확인하지 않아 연구자가 직접 둔 폴더를 지운다. |
| `test_complete_results_are_never_quarantined` | 정상 완료 결과를 격리로 옮겨 결과를 잃는다. |
| `test_concurrent_run_on_same_output_is_blocked` | 같은 출력 폴더에 두 실행이 붙어 서로의 판정을 뒤집는다. 40시간 배치에서 실수로 두 번 띄우기 쉽다. |
| `test_lock_file_is_not_mistaken_for_a_result` | 잠금 파일이 결과 집계나 완료 판정에 섞인다. |
| `test_pending_list_is_rechecked_after_acquiring_lock` | 잠금 전에 판정한 목록을 그대로 써서, 대기 중 다른 실행이 끝낸 작업을 또 돌린다. |
| `test_each_run_uses_a_unique_staging_dir` | staging 이 다른 실행의 것과 섞여 두 실행이 입력 집합을 다르게 본다. |
| `test_relative_sidecar_files_are_staged` | 상대경로 sidecar(`mmcifPath` 등)를 staging 에 함께 옮기지 않아 컨테이너 안에서 못 찾는다. 입력 폴더 밖을 가리키는 것은 거부해야 한다. |

### 집계·시각화 (tests/test_reporting.py)

| 테스트 | 막는 버그 |
| --- | --- |
| `test_managed_dirs_are_excluded_from_collection` | `.af3_incomplete/` 격리 보존본과 잠금 파일이 집계에 섞여 실패 결과가 완료 건으로 세어진다. |
| `test_managed_dirs_are_excluded_from_visualization` | 시각화가 관리용 폴더를 타깃으로 잡아 격리된 실패 결과의 플롯을 그린다. |
| `test_incomplete_targets_are_reported_not_hidden` | 집계가 미완성 폴더를 조용히 빼먹고 완료 건수만 보고한다. |
| `test_empty_collection_exits_nonzero` | 완료 결과가 하나도 없는데 종료코드 0 을 돌려줘 자동화가 빈 CSV 를 정상으로 오인한다. |
| `test_top_selection_warns_on_mixed_conditions` | 상위 N건 선정이 여러 조건을 섞어 같은 타깃을 중복 선정한다. |
| `test_timestamp_suffix_folders_are_not_separate_targets` | AF3 타임스탬프 접미사 폴더가 별개 타깃으로 집계돼 같은 VHH 가 두 번 세어지는 회귀를 막는다. |
| `test_timestamp_suffix_folders_are_normalized_in_visualization` | 같은 타임스탬프 접미사 문제를 시각화 쪽에서 막는다. |
| `test_batch_finds_timestamp_suffix_result_dirs` | 반대 방향. `af3_batch.py` 가 타임스탬프 접미사 폴더를 못 찾아 이미 끝난 건을 다시 돌린다. |

## docker 스텁의 설계 근거

`tests/fake_docker.py` 가 `docker` 를 가로챈다. `tests/harness.py` 가 PATH 앞에
가짜 `sudo` 와 `docker` 를 놓는 방식이다. 가짜 `sudo`는 기본적으로 실패하고 명시적으로
허용한 테스트에서만 동작하므로, 러너가 암호형 sudo에 몰래 의존하는 회귀도 드러난다.

### 왜 스텁인가

검증 호스트에 Docker 가 없다(바이너리 부재, sudo 암호 필요). 있더라도 AF3 실물
1건은 수 분이 걸려 회귀 테스트에 쓸 수 없다. 그런데 이 저장소가 막아야 하는
버그는 대부분 GPU 연산이 아니라 **파일시스템 규약**에서 나온다. 출력 폴더를 어떤
이름으로 만드는가, 어떤 파일을 언제 쓰는가, 잘못된 입력에서 어디서 멈추는가.
이 규약만 충실히 흉내내면 러너의 제어 흐름을 초 단위로 검증할 수 있다.

**스텁의 가치는 충실도에 전부 달려 있다.** 스텁이 AF3 와 다르게 행동하면 테스트가
통과해도 실제로는 깨진다. 그래서 아래 항목은 모두 AF3 소스(commit `97d2023`,
`~/af3_work/alphafold3`)를 직접 읽어 근거를 확인했다. 스텁 파일 안에도 같은
근거를 주석으로 남겨 두었다.

| 흉내내는 동작 | 근거 (AF3 소스) |
| --- | --- |
| 출력 폴더 이름 = JSON `name` 정규화값 | `run_alphafold.py:1075` `output_dir=_OUTPUT_DIR.value / fold_input.sanitised_name()` |
| 정규화 규칙: 공백→밑줄, `[A-Za-z0-9_-.]` 만 | `folding_input.py:1054-1058` |
| 폴더가 비어 있지 않으면 `<이름>_<타임스탬프>` 형제 폴더를 새로 만든다 | `run_alphafold.py:861-870` (`force_output_dir` 기본 False) |
| `_data.json` 이 추론 **전** 에 쓰인다 | `run_alphafold.py:880` `write_fold_input_json` 이 `predict_structure` 앞 |
| 최종 산출물 이름 | `post_processing.py:121-123` (`_model.cif`, `_confidences.json`, `_summary_confidences.json`), `run_alphafold.py:727` (`_ranking_scores.csv`) |
| 압축 시 `.zst` 접미사 | `post_processing.py:126-131` |
| `seed-<N>_sample-<M>/` 하위 폴더 | `run_alphafold.py:683-691` |
| `--input_dir` 순회는 제너레이터. 깨진 JSON 에서 그 자리에서 멈춘다 | `folding_input.py:1570-1584` → `1541-1567` (ValueError 재발생) |
| `._*.json` 도 `glob('*.json')` 에 잡히고 `read_text()` 가 죽는다 | `folding_input.py:1544, 1580` |
| `--helpfull` 은 종료코드 1, `--[no]run_inference` 형식으로 나열 | 검증 호스트 실측: 종료코드 1, 295행, `--[no]run_data_pipeline:` 표기 확인 |
| `--buckets` 기본값이 128 부터 시작 | `--helpfull` 실측 출력 |

### 스텁이 흉내내지 않는 것 (의도적)

MSA 검색, 실제 추론 연산, VRAM 사용량, ranking score 의 물리적 의미. 이들은
스텁으로 검증할 수 없고 이 테스트 모음의 대상도 아니다. GPU·MSA 성능은
`docs/benchmark_report.md` 의 실측 쪽 소관이다.

### 구버전 이미지 흉내에 대한 단서 (추정 표시)

`HELP_LEGACY` 는 `--input_dir` 과 `--jax_compilation_cache_dir` 이 없는 이미지를
흉내낸다. AF3 초기 공개판에 `--json_path` 만 있었다는 **전제로 만든 것이며, 실제
구버전 이미지로 대조하지는 않았다.** 다만 이 스텁이 검증하는 것은 "없는 플래그를
만났을 때 러너가 무엇을 하는가" 이고, 그 동작은 전제가 맞든 틀리든 옳아야 한다.

### 스텁 동작 조종 손잡이

| 환경변수 | 용도 |
| --- | --- |
| `AF3_STUB_LOG` | 호출 내역을 JSON Lines 로 기록 (테스트가 마운트·플래그를 확인) |
| `AF3_STUB_FAIL_AT` | N번째 작업에서 `_data.json` 만 쓰고 중단 (추론 중 끊김 재현) |
| `AF3_STUB_FAIL_NAMES` | 이 이름들에서 중단. 파일별 재시도에서도 계속 실패시킬 때 쓴다 |
| `AF3_STUB_EXIT` | 중단 시 종료코드 |
| `AF3_STUB_SLEEP` | 작업 처리 전 대기 (중복 실행 차단 테스트) |
| `AF3_STUB_COMPRESS` | `.cif.zst` 로 쓴다 |
| `AF3_STUB_ZERO_SIZE` | 산출물을 크기 0 으로 쓴다 (디스크 꽉 찬 상황) |

`AF3_STUB_FAIL_AT` 은 **스텁 호출마다** 센다. 파일별 재시도로 넘어가면 카운터가
초기화되므로 재시도까지 실패시키려면 `AF3_STUB_FAIL_NAMES` 를 써야 한다. 이 함정에
한 번 걸려 종료코드 테스트가 헛돌았다.

## 역검증: 버그 재주입

```
python3 tests/verify_tests_catch_bugs.py
```

통과하는 테스트는 두 이유로 통과한다. (a) 코드가 옳아서, (b) 테스트가 아무것도
확인하지 않아서. 둘을 구분하는 유일한 방법은 버그를 일부러 다시 넣고 테스트가
실패하는지 보는 것이다. **이 검증을 하지 않은 테스트는 통과해도 의미가 없다.**

`scripts/` 를 임시 폴더에 복사해 문자열 치환으로 옛 버그를 되살린 뒤, 해당
테스트만 돌려 실패하는지 확인한다. 원본 저장소는 건드리지 않는다.

### 결과: 재주입 47건, 47건 모두 테스트가 잡았다

아래 표는 `tests/verify_tests_catch_bugs.py` 의 `INJECTIONS` 그대로다.
건수가 어긋나면 `test_testing_notes_matches_the_registered_mutations` 가 잡는다.

| 재주입한 버그 | 잡은 테스트 |
| --- | --- |
| 완료판정을 폴더 존재로 되돌린다 | `test_data_json_only_is_not_complete`, `test_exit_code_nonzero_when_jobs_remain` |
| 완료판정에서 크기 0 검사를 뺀다 | `test_data_json_only_is_not_complete` |
| 결과 폴더를 JSON name 대신 파일명으로 찾는다 | `test_output_dir_follows_json_name_not_filename`, `test_runner_and_af3_agree_on_output_folder_name` |
| 이름 충돌·빈 이름 검사를 없앤다 | `test_name_collision_is_rejected_before_running` |
| 빈 정규화 이름을 통과시킨다 | `test_hangul_only_name_is_rejected` |
| 깨진 JSON 사전 검증을 없앤다 | `test_broken_json_is_caught_before_running` |
| macOS 사이드카 제외를 없앤다 | `test_macos_appledouble_sidecar_is_excluded` |
| 미완료가 남아도 종료코드 0 을 돌려준다 | `test_exit_code_nonzero_when_jobs_remain` |
| staging 소유 표식 확인을 없앤다 | `test_foreign_staging_dir_is_preserved` |
| 격리 개수 제한을 없앤다 | `test_quarantine_growth_is_bounded_per_job`, `test_quarantine_keep_option_is_honoured` |
| 중복 실행 잠금을 없앤다 | `test_concurrent_run_on_same_output_is_blocked` |
| 구버전 이미지 대비 전환을 없앤다 | `test_legacy_image_without_input_dir_falls_back_to_per_file` |
| 이미지 확인 실패를 무시하고 최신 플래그를 추측한다 | `test_image_probe_failure_stops_with_reason` |
| 집계에서 숨은/관리 폴더 제외를 없앤다 | `test_managed_dirs_are_excluded_from_collection` |
| 집계 결과가 없어도 0 을 돌려준다 | `test_empty_collection_exits_nonzero` |
| 시각화에서 숨은/관리 폴더 제외를 없앤다 | `test_managed_dirs_are_excluded_from_visualization` |
| 타임스탬프 접미사 결과 폴더 탐색을 없앤다 | `test_batch_finds_timestamp_suffix_result_dirs` |
| 이미지 능력 검증을 errexit 의존 형태로 되돌린다 | `test_installer_image_capability_gate_fails_on_every_check` |
| legacy 러너가 단계와 무관하게 가중치를 요구하게 되돌린다 | `test_legacy_preflight_requires_only_what_the_stage_uses` |
| 뷰어 템플릿을 순차 치환으로 되돌린다 | `test_viewer_page_placeholders_survive_target_names_that_look_like_placeholders` |
| af3.bin 크기 핀의 우회 수단을 없앤다 | `test_model_size_pin_is_overridable_and_says_so` |
| staging 파일/폴더 충돌 검사를 무력화한다 | `test_staging_detects_file_directory_conflicts_without_pairwise_scan` |
| 배치 경로의 격리 실패를 다시 무방비로 둔다 | `test_batch_run_survives_one_unquarantinable_result` |
| prepare --report 가 다시 symlink 를 따라가게 한다 | `test_prepare_report_does_not_follow_a_symlinked_destination` |
| prepare 의 비-폴더 출력 경로 검사를 없앤다 | `test_prepare_rejects_a_non_directory_output_path_with_a_readable_error` |
| 컨테이너에 이름을 붙이지 않는다 | `test_runner_names_containers_and_reports_orphans` |
| 고아 컨테이너 판정을 없앤다 | `test_runner_names_containers_and_reports_orphans` |
| 종료 시 컨테이너 정리를 없앤다 | `test_runner_names_containers_and_reports_orphans` |
| CSP 에서 'unsafe-eval' 을 다시 뺀다 | `test_viewer_csp_allows_what_the_molstar_engine_needs` |
| CSP 위반 기록을 없앤다 | `test_viewer_failure_message_names_csp_when_csp_is_the_cause` |
| 시점 초기화를 화면 맞춤으로 되돌린다 | `test_reset_button_restores_the_first_view_not_just_the_framing` |
| 템플릿 주석에 자리표시자를 적는다 | `test_each_template_placeholder_appears_exactly_once` |
| preferred 러너에서 --user 를 뺀다 | `test_runner_writes_results_as_the_invoking_user` |
| legacy 러너에서 --user 를 뺀다 | `test_legacy_runner_writes_results_as_the_invoking_user` |
| 마운트를 /root 아래로 되돌린다 | `test_container_mounts_are_reachable_by_a_non_root_user` |
| 캐시 권한 점검에서 하위 폴더를 빼먹는다 | `test_unwritable_jax_cache_is_detected_and_explained` |
| legacy 러너의 캐시 권한 점검을 없앤다 | `test_legacy_runner_also_detects_an_unwritable_cache` |
| GPU 선점 점검을 없앤다 | `test_busy_gpu_is_refused_before_starting_a_container` |
| GPU 여유 메모리 하한 점검을 없앤다 | `test_busy_gpu_is_refused_before_starting_a_container` |
| 요약 산점도를 다시 pTM 으로 되돌린다 | `test_summary_scatter_uses_the_interface_metric_for_complexes` |
| 래퍼의 스레드 설정 검증을 없앤다 | `test_wrapper_rejects_non_numeric_thread_settings` |
| 래퍼의 작업 이름 검증을 없앤다 | `test_wrapper_rejects_names_that_escape_the_work_directory` |
| 완료 판정에서 provenance 비교를 없앤다 | `test_changed_input_is_not_mistaken_for_a_finished_result` |
| 잠금 뒤 재확인만 provenance 를 무시한다 | `test_changed_input_is_not_mistaken_for_a_finished_result` |
| legacy MSA 보관소를 크기 비교로 되돌린다 | `test_legacy_msa_store_does_not_keep_a_stale_result` |
| legacy 잠금을 work-dir 로 되돌린다 | `test_legacy_lock_protects_the_output_directory` |
| nvidia-smi 종료코드 검사를 없앤다 | `test_environment_check_fails_when_nvidia_smi_cannot_run` |

### 역검증이 실제로 잡아낸 것 (이 트랙에서 고친 테스트 3건)

역검증은 형식적 절차가 아니었다. 첫 실행에서 **내가 쓴 테스트 3건이 버그를 놓쳤다.**

1. `test_image_probe_failure_stops_with_reason` — 스텁이 이미지를 못 찾을 때
   호출 기록을 남기지 않아, "추측 실행" 을 해도 테스트가 구분할 수 없었다.
   → 스텁에 `run_attempt` 기록을 추가하고 테스트가 그것까지 확인하게 고쳤다.
2. `test_managed_dirs_are_excluded_from_collection` — 중첩형 격리 구조만 심어서,
   숨은 폴더 제외 규칙을 지워도 결과가 같았다(그냥 "미완성" 으로 분류됐다).
   → 결과 파일을 직접 담은 평평한 숨은 폴더를 함께 심도록 고쳤다.
3. `test_managed_dirs_are_excluded_from_visualization` — 같은 이유.
   → 같은 방식으로 고쳤다.

또 한 건은 역검증 자체의 구멍이었다. `check_equal` 에 인자를 하나 더 넘긴 오타로
테스트가 `TypeError` 로 **항상** 실패하고 있었는데, 역검증은 그것을 "버그를 잡았다"
로 집계했다. → 역검증에 **기준선 확인**을 넣었다. 버그를 넣지 않은 사본에서 그
테스트가 통과하는지 먼저 확인하고, 통과하지 않으면 "기준선 실패" 로 보고한다.
이 때문에 역검증 시간이 12초에서 22초로 늘었지만, 없으면 안 되는 검사다.

## 깨끗한 우분투 사전 점검 (수동)

`tests/run_all.py` 는 실제 Docker 를 쓰지 않는다. 그런데 설치기의 사전 점검은 "이 컴퓨터가
어떤 상태인가" 를 보는 코드라 스텁으로는 검증할 수 없다. 그래서 그것만 따로 둔다.

    bash tests/verify_clean_ubuntu_preflight.sh

깨끗한 `ubuntu:24.04` 와 `debian:12` 컨테이너를 띄워, 초보자가 실제로 하는 실수마다
설치기가 멈추고 이유를 말하는지 본다. 2026-08-26 실측 6/6 통과.

| 상황 | 기대 |
|---|---|
| root 로 실행 | 종료코드 2, `run this installer as your normal user` |
| 필수 명령 없음 | 종료코드 1, 없는 명령 이름을 말한다 |
| 우분투가 아님 | 종료코드 1, `supported distribution is Ubuntu` |
| 지원하지 않는 우분투 판 | 종료코드 1, 지원 목록 3개를 말한다 |
| GPU 드라이버 없음 | 종료코드 1, `nvidia-smi` 를 먼저 고치라고 한다 |
| `--dry-run` | 종료코드 0, 아무것도 바꾸지 않는다 |

**확인하지 못하는 것.** 실제 apt 설치, Docker 저장소 구성, 가중치와 850GB DB 내려받기는
여기서 하지 않는다. GPU 가 붙은 진짜 우분투 장비가 있어야 한다. 그 구간은 지금까지
아무도 처음부터 돌려 본 적이 없다.

## 현재 저장소 버전의 release gate

등록된 테스트는 `--strict`에서 known failure 없이 전부 통과해야 한다. 별도 통합 suite와
mutation 47건도 전부 통과해야 한다. `.github/workflows/tests.yml`은 Python 3.9, 3.12,
3.14에서 `tests/run_all.py`를 실행하고, 3.12 lane은 `requirements.txt`를 설치해
matplotlib 그림 생성 경로도 실행한다. 실제 Docker/AF3/GPU smoke는 수동 gate다.

## 테스트를 추가하는 방법

`tests/test_*.py` 중 맞는 파일을 열고 함수를 하나 쓴다.

```python
@regression(
    item="1",                       # 과제 항목 번호 (없으면 짧은 주제 이름)
    prevents="이 테스트가 막는 실제 버그를 한 줄로. 왜 중요한지까지.",
)
def test_무엇을_확인하는지():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, "실행이 실패했다", proc.stdout[-800:])
    finally:
        workspace.cleanup()
```

새 모듈을 만들면 `tests/run_tests.py` 의 `TEST_MODULES` 에 이름을 넣어라.

### 지킬 것

- **`prevents` 를 반드시 채워라.** 이것이 이 테스트 모음의 가치다. 1년 뒤에
  "이 검사 왜 있지" 하고 지우려는 사람이 읽는 문장이다.
- **실패 메시지에 실제 값을 담아라.** `check`/`check_equal`/`check_in` 의 마지막
  인자에 관측값을 넣는다. "실패했다" 만 나오면 초보자는 손을 못 쓴다.
- **역검증 목록에 등록하라.** `tests/verify_tests_catch_bugs.py` 의 `INJECTIONS`
  에 되살릴 버그와 잡아야 할 테스트 이름을 넣고 돌려 보라. 잡지 못하면 그
  테스트는 아직 아무것도 확인하지 않고 있다.
- **`Workspace().cleanup()` 을 `finally` 에 두어라.** 임시 폴더가 쌓인다.
- **스텁을 고쳤으면 근거를 주석에 남겨라.** 근거 없는 스텁 동작은 언제든 실제와
  어긋날 수 있고, 그러면 테스트 전체가 헛돈다.

### 쓸 수 있는 도구 (tests/harness.py)

| 이름 | 용도 |
| --- | --- |
| `Workspace()` | `<name>_in`/`<name>_out` 관례의 임시 작업 폴더 |
| `.write_json(파일명, obj)` | 입력 JSON 쓰기. `raw_text=` 로 깨진 JSON 재현 |
| `.write_bytes(파일명, data)` | 비 UTF-8 파일 (사이드카 재현) |
| `.monomer(name)` | VHH 단량체 입력 JSON 최소형 |
| `.make_result(이름, stage=)` | AF3 결과 폴더를 손으로 만든다 (`data`/`partial`/`full`/`zero`) |
| `.stub_calls()` | 스텁이 기록한 docker 호출 내역 |
| `run_script(스크립트, 인자, ws)` | 가짜 docker 가 놓인 PATH 로 스크립트 실행 |
| `default_args(ws, *추가)` | 경로를 임시 폴더로 돌린 표준 인자 묶음 |
| `load_module("af3_collect.py")` | 스크립트를 모듈로 불러 함수 단위 검증 |
| `check`, `check_equal`, `check_in`, `check_not_in` | 실패 메시지가 친절한 단정 |

## pytest에 대하여

이 저장소의 정식 진입점은 custom registry와 standalone integration을 모두 아는
`tests/run_all.py`다. pytest는 설치 의존성을 추가하고 standalone suite를 자동으로 같은
의미로 실행하지 않으므로 release 판정에 사용하지 않는다.

## 실행 환경

CI matrix와 현재 검증 호스트의 정확한 결과는 workflow와 최신 release log를 기준으로 한다.
핵심 테스트는 외부 Python package와 Docker를 요구하지 않는다. matplotlib이 있는 3.12
lane에서는 선택적 그림 생성까지 추가로 검증한다.

## 알려진 한계 (테스트가 덮지 않는 것)

정직하게 적어 둔다. 아래는 이 테스트 모음이 **보장하지 않는다.**

- **AF3 실물 동작.** 스텁은 파일시스템 규약만 흉내낸다. AF3 자체가 업데이트되어
  출력 파일 이름이나 폴더 규칙이 바뀌면 스텁이 먼저 낡는다. AF3 를 올릴 때는
  `fake_docker.py` 의 근거 표를 소스와 다시 대조하라.
- **GPU·MSA 성능.** 건당 시간, VRAM, MSA 처리율은 실측 소관이다.
- **신뢰도 지표의 정확성.** 스텁은 고정된 가짜 점수를 쓴다. `af3_collect.py` 의
  등급 기준이나 pLDDT 계산이 과학적으로 맞는지는 검증하지 않는다.
- **경합 상황의 완전한 재현.** `test_pending_list_is_rechecked_after_acquiring_lock`
  은 실제 경합이 아니라 잠금 안쪽 재판정이 일어나는지만 본다. 진짜 경합은
  타이밍에 의존해 확정적으로 만들 수 없다.
- **실제 배포판별 Docker 설치와 NVIDIA Container Toolkit.** stub은 CLI 계약만 본다.
- **`af3run.sh`의 모든 대화형 조합.** 핵심 collect/file-name 호환만 본다.
- **여러 호스트가 공유 스토리지를 함께 쓰는 상황.** `scan_stage_dirs` 의
  hostname 판정은 단위 수준으로만 검증했다. 실제 NFS 환경에서 `flock` 동작은
  검증하지 않았다.
- **Windows.** `fcntl` 을 쓰므로 러너 자체가 Linux/macOS 전용이다.
