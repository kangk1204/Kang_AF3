#!/usr/bin/env python3
"""docker 를 가로채는 스텁. AF3 컨테이너 실행 대신 AF3 의 관찰된 동작만 흉내낸다.

왜 스텁인가
-----------
검증 호스트에 Docker 가 없고(바이너리 부재, sudo 암호 필요), 있더라도 AF3 실물
1건은 수 분이 걸려 회귀 테스트에 쓸 수 없다. 그러나 이 저장소가 막아야 하는 버그는
대부분 GPU 연산이 아니라 **파일시스템 규약**에서 나온다. 출력 폴더를 어떤 이름으로
만드는가, 어떤 파일을 언제 쓰는가, 잘못된 입력에서 어디서 멈추는가. 이 규약만
충실히 흉내내면 러너의 제어 흐름을 초 단위로 검증할 수 있다.

스텁이 흉내내는 동작과 그 근거
------------------------------
근거는 모두 AF3 소스 commit 97d2023 (~/af3_work/alphafold3) 를 직접 읽어 확인했다.

(1) 출력 폴더 이름은 파일명이 아니라 JSON ``name`` 을 정규화한 값이다.
    run_alphafold.py:1075  output_dir=_OUTPUT_DIR.value / fold_input.sanitised_name()
    folding_input.py:1054  sanitised_name(): 공백 -> 밑줄, [A-Za-z0-9_-.] 만 남긴다.

(2) 출력 폴더가 이미 있고 비어 있지 않으면 그 폴더를 쓰지 않고
    ``<name>_<YYYYmmdd_HHMMSS>`` 형제 폴더를 새로 만든다.
    run_alphafold.py:861-870 (force_output_dir=False 가 기본)
    -> 러너가 미완료 결과를 미리 격리해야 하는 이유가 이것이다.

(3) ``<name>_data.json`` 은 추론 **전** 에 쓰인다.
    run_alphafold.py:880  write_fold_input_json(...) 가 predict_structure 앞에 있다.
    -> 폴더 존재나 _data.json 만으로 완료를 판정하면 추론 중 끊긴 것을 완료로 오인한다.

(4) 최종 산출물 이름 (post_processing.py:121-135, run_alphafold.py:727)
    <name>_model.cif, <name>_confidences.json, <name>_summary_confidences.json,
    <name>_ranking_scores.csv, seed-*_sample-*/ 하위 폴더, TERMS_OF_USE.md
    compress_large_output_files=True 면 .cif/.confidences.json 에 .zst 가 붙는다.

(5) ``--input_dir`` 순회는 제너레이터다. 깨진 JSON 하나에서 ValueError 로 멈추고
    그 뒤 입력은 처리되지 않는다.
    folding_input.py:1570-1584 load_fold_inputs_from_dir -> load_fold_inputs_from_path
    (1562-1567: from_json 실패를 ValueError 로 다시 올린다)

(6) ``._*.json`` 사이드카도 glob('*.json') 에 잡히고, read_text() 가
    UnicodeDecodeError 로 죽는다. folding_input.py:1544 json_path.read_text()

(7) ``--helpfull`` 은 종료코드 1 로 끝나고, 플래그를 ``  --input_dir: 설명`` 또는
    ``  --[no]run_inference: 설명`` 형식으로 나열한다. 실측 확인:
    검증 호스트에서 실행 -> EXIT=1, 295행, '--[no]run_data_pipeline:' 표기.
    -> 러너의 probe_flags 가 종료코드가 아니라 텍스트로 판정하는 것이 맞다.

(8) ``--buckets`` 기본값은 128 부터 시작한다. (run_alphafold.py:14행 도움말 실측)

흉내내지 않는 것 (의도적)
-------------------------
MSA 검색, 실제 추론, VRAM 사용량, ranking score 의 물리적 의미. 이들은 스텁으로
검증할 수 없고, 이 테스트 모음의 대상도 아니다.

환경변수 (테스트가 스텁 동작을 조종하는 손잡이)
------------------------------------------------
AF3_STUB_LOG        호출 내역을 JSON Lines 로 기록할 경로
AF3_STUB_FAIL_AT    N번째 작업에서 _data.json 만 쓰고 중단 (추론 중 끊김 재현)
AF3_STUB_FAIL_NAMES 이 정규화 이름들에서 중단 (쉼표 구분)
AF3_STUB_EXIT       중단 시 종료코드 (기본 1)
AF3_STUB_SLEEP      작업 처리 전 대기 초 (중복 실행 차단 테스트용)
AF3_STUB_COMPRESS   1 이면 .cif 를 .cif.zst 로 쓴다
AF3_STUB_ZERO_SIZE  이 이름들의 최종 산출물을 크기 0 으로 쓴다 (쉼표 구분)
"""

from __future__ import annotations

import json
import os
import string
import sys
import time
from datetime import datetime
from pathlib import Path

# 근거 (7): 실제 --helpfull 출력에서 플래그 표기 형식만 뽑아 옮긴 것.
# 러너의 flag_is_listed() 가 '--<이름>' 또는 '--[no]<이름>' 문자열을 찾으므로
# 표기 형식이 틀리면 테스트가 실제와 다른 것을 검증하게 된다.
HELP_MODERN = """AlphaFold 3 structure prediction script.

flags:

run_alphafold.py:
  --buckets: Strictly increasing order of token sizes for which to cache
    compilations.
    (default:
    '128,256,384,512,768,1024,1280,1536,2048,2560,3072,3584,4096,4608,5120')
  --db_dir: Path to the directory containing the databases.
  --input_dir: Path to the directory containing input JSON files.
  --jax_compilation_cache_dir: Path to a directory for the JAX compilation
    cache.
  --json_path: Path to the input JSON file.
  --model_dir: Path to the model to use for inference.
  --output_dir: Path to a directory where the results will be saved.
  --[no]run_data_pipeline: Whether to run the data pipeline on the fold inputs.
    (default: 'true')
  --[no]run_inference: Whether to run inference on the fold inputs.
    (default: 'true')
  --[no]force_output_dir: If True, do not create a new output directory even if
    the specified one is non-empty.
    (default: 'false')
"""

# 구버전 이미지 재현: --input_dir 과 --jax_compilation_cache_dir 이 없다.
# AF3 초기 공개판에는 --json_path 만 있었다는 전제로 만든 스텁이다(전제 표시).
HELP_LEGACY = """AlphaFold 3 structure prediction script.

flags:

run_alphafold.py:
  --db_dir: Path to the directory containing the databases.
  --json_path: Path to the input JSON file.
  --model_dir: Path to the model to use for inference.
  --output_dir: Path to a directory where the results will be saved.
  --[no]run_data_pipeline: Whether to run the data pipeline on the fold inputs.
  --[no]run_inference: Whether to run inference on the fold inputs.
"""


def sanitised_name(name: str) -> str:
    """근거 (1): folding_input.py:1054-1058 과 같은 규칙."""
    spaceless = name.replace(" ", "_")
    allowed = set(string.ascii_letters + string.digits + "_-.")
    return "".join(ch for ch in spaceless if ch in allowed)


def log_event(payload: dict) -> None:
    path = os.environ.get("AF3_STUB_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def parse_docker_args(argv: list[str]) -> dict:
    """docker run 명령을 마운트/이미지/AF3 플래그로 쪼갠다."""
    result = {
        "mounts": [],       # (호스트경로, 컨테이너경로, 옵션)
        "gpus": False,
        "image": None,
        "af3_flags": {},
        "af3_switches": [],
        "raw": list(argv),
    }
    index = 0
    if index < len(argv) and argv[index] == "run":
        index += 1
    while index < len(argv):
        token = argv[index]
        if token == "--rm":
            index += 1
        elif token == "--gpus":
            result["gpus"] = True
            index += 2
        elif token == "-v":
            parts = argv[index + 1].split(":")
            host = parts[0]
            container = parts[1] if len(parts) > 1 else ""
            option = parts[2] if len(parts) > 2 else ""
            result["mounts"].append((host, container, option))
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            result["image"] = token
            index += 1
            break
    # 이미지 뒤는 컨테이너 안에서 실행할 명령: python run_alphafold.py --flag=...
    for token in argv[index:]:
        if token.startswith("--") and "=" in token:
            key, value = token[2:].split("=", 1)
            result["af3_flags"][key] = value
        elif token.startswith("--"):
            result["af3_switches"].append(token[2:])
    return result


def to_host_path(container_path: str, mounts: list) -> Path | None:
    """컨테이너 경로를 마운트 표를 보고 호스트 경로로 되돌린다."""
    best = None
    for host, container, _option in mounts:
        if not container:
            continue
        if container_path == container or container_path.startswith(container + "/"):
            if best is None or len(container) > len(best[1]):
                best = (host, container)
    if best is None:
        return None
    host, container = best
    suffix = container_path[len(container) :].lstrip("/")
    return Path(host) / suffix if suffix else Path(host)


def read_input(json_path: Path) -> dict:
    """근거 (5)(6): read_text() 가 먼저 터지고, 그 다음 json.loads 가 터진다."""
    text = json_path.read_text(encoding="utf-8")  # 비 UTF-8 이면 여기서 죽는다
    raw = json.loads(text)                        # 깨진 JSON 이면 여기서 죽는다
    if isinstance(raw, list):
        raise ValueError(
            f"Failed to load fold job 0 from {json_path}"
            " (AlphaFold Server dialect): not supported by this stub"
        )
    if not isinstance(raw, dict):
        raise ValueError(f"Failed to load input from {json_path} (AlphaFold 3 dialect)")
    return raw


def iter_inputs(parsed: dict):
    """근거 (5): --input_dir 은 제너레이터. 깨진 파일에서 그 자리에서 멈춘다."""
    mounts = parsed["mounts"]
    if "input_dir" in parsed["af3_flags"]:
        host_dir = to_host_path(parsed["af3_flags"]["input_dir"], mounts)
        if host_dir is None or not host_dir.is_dir():
            raise SystemExit(f"stub: input_dir 을 호스트에서 찾지 못했다: {host_dir}")
        # 근거 (5)(6): 숨은 파일도 제외하지 않는다. sorted(glob('*.json')) 그대로.
        for path in sorted(host_dir.glob("*.json")):
            if not path.is_file():
                continue
            yield path, read_input(path)
    elif "json_path" in parsed["af3_flags"]:
        path = to_host_path(parsed["af3_flags"]["json_path"], mounts)
        if path is None or not path.is_file():
            raise SystemExit(f"stub: json_path 을 호스트에서 찾지 못했다: {path}")
        yield path, read_input(path)
    else:
        # 근거: run_alphafold.py:932-936 AssertionError
        raise SystemExit("stub: Exactly one of --json_path or --input_dir must be specified.")


def write_finals(result_dir: Path, name: str, zero: bool) -> None:
    """근거 (4): write_outputs() + post_processing.write_output() 이 만드는 파일."""
    compress = os.environ.get("AF3_STUB_COMPRESS") == "1"
    cif_name = f"{name}_model.cif.zst" if compress else f"{name}_model.cif"
    conf_name = (
        f"{name}_confidences.json.zst" if compress else f"{name}_confidences.json"
    )
    body = "" if zero else "data_placeholder\n"
    (result_dir / cif_name).write_text(body, encoding="utf-8")
    (result_dir / conf_name).write_text(
        "" if zero else json.dumps({"atom_plddts": [80.0, 90.0]}) + "\n",
        encoding="utf-8",
    )
    (result_dir / f"{name}_summary_confidences.json").write_text(
        ""
        if zero
        else json.dumps(
            {
                "ranking_score": 0.83,
                "ptm": 0.81,
                "iptm": None,
                "fraction_disordered": 0.12,
                "has_clash": 0.0,
                "chain_ptm": [0.81],
                "chain_iptm": [None],
                "chain_pair_iptm": [[0.81]],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (result_dir / f"{name}_ranking_scores.csv").write_text(
        "" if zero else "seed,sample,ranking_score\n1,0,0.83\n1,1,0.79\n",
        encoding="utf-8",
    )
    sample_dir = result_dir / "seed-1_sample-0"
    sample_dir.mkdir(exist_ok=True)
    (sample_dir / f"{name}_seed-1_sample-0_model.cif").write_text(
        body, encoding="utf-8"
    )
    (result_dir / "TERMS_OF_USE.md").write_text("stub\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parsed = parse_docker_args(argv)
    image = parsed["image"] or ""

    # --helpfull 처리. 근거 (7): 실제로 종료코드 1 이다.
    if "helpfull" in parsed["af3_switches"] or "--helpfull" in argv:
        log_event({"call": "help", "image": image})
        if "broken" in image:
            # docker 가 이미지를 못 찾은 상황. 근거: docker 관례상 125.
            sys.stderr.write(
                f"Unable to find image '{image}' locally\n"
                f"docker: Error response from daemon: pull access denied for {image}.\n"
            )
            return 125
        # 이미지 이름에 'legacy' 가 들어가면 구버전으로 흉내낸다.
        # (부분일치 함정 주의: 'old' 로 판정하면 'alphafold3' 의 'fold' 가 걸린다.
        #  실제로 이 스텁을 만들 때 그 버그로 테스트 2건이 헛돌았다.)
        sys.stdout.write(HELP_LEGACY if "legacy" in image else HELP_MODERN)
        return 1

    if "broken" in image:
        # 이미지가 없어도 "docker 를 불렀다" 는 사실 자체는 기록한다.
        # 그러지 않으면 '확인 실패 후 추측 실행' 버그를 테스트가 구분할 수 없다
        # (역검증에서 실제로 이 구멍이 드러나 스텁을 고쳤다).
        log_event({"call": "run_attempt", "image": image, "exit": 125})
        sys.stderr.write(f"Unable to find image '{image}' locally\n")
        return 125

    flags = parsed["af3_flags"]
    switches = parsed["af3_switches"]
    run_data = "norun_data_pipeline" not in switches
    run_inference = "norun_inference" not in switches
    mode = "full" if (run_data and run_inference) else ("data" if run_data else "inference")

    output_host = to_host_path(flags.get("output_dir", ""), parsed["mounts"])
    if output_host is None:
        sys.stderr.write("stub: --output_dir 마운트를 찾지 못했다\n")
        return 2
    output_host.mkdir(parents=True, exist_ok=True)

    log_event(
        {
            "call": "run",
            "image": image,
            "mode": mode,
            "gpus": parsed["gpus"],
            "per_file": "json_path" in flags,
            "flags": sorted(flags),
            "switches": sorted(switches),
            "mounts": [[h, c, o] for h, c, o in parsed["mounts"]],
        }
    )

    fail_at = int(os.environ.get("AF3_STUB_FAIL_AT", "0") or 0)
    fail_names = {
        x for x in os.environ.get("AF3_STUB_FAIL_NAMES", "").split(",") if x
    }
    zero_names = {
        x for x in os.environ.get("AF3_STUB_ZERO_SIZE", "").split(",") if x
    }
    abort_code = int(os.environ.get("AF3_STUB_EXIT", "1") or 1)
    sleep_seconds = float(os.environ.get("AF3_STUB_SLEEP", "0") or 0)
    if sleep_seconds:
        time.sleep(sleep_seconds)

    index = 0
    try:
        for json_path, obj in iter_inputs(parsed):
            index += 1
            raw_name = obj.get("name")
            if not isinstance(raw_name, str) or not raw_name:
                # 근거: chains/name 검증 실패는 ValueError 로 순회를 멈춘다.
                raise ValueError(
                    f"Failed to load input from {json_path} (AlphaFold 3 dialect):"
                    " name must be a non-empty string"
                )
            name = sanitised_name(raw_name)
            print(f"Running fold job {raw_name}...")

            result_dir = output_host / name
            # 근거 (2): 비어 있지 않으면 타임스탬프 형제 폴더를 새로 만든다.
            if result_dir.exists() and any(result_dir.iterdir()):
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                result_dir = output_host / f"{name}_{stamp}"
                print(
                    f"Output will be written in {result_dir} since"
                    f" {output_host / name} is non-empty."
                )
            else:
                print(f"Output will be written in {result_dir}")
            result_dir.mkdir(parents=True, exist_ok=True)

            # 근거 (3): _data.json 이 추론보다 먼저 쓰인다.
            (result_dir / f"{name}_data.json").write_text(
                json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"Writing model input JSON to {result_dir / (name + '_data.json')}")

            if index == fail_at or name in fail_names:
                # 추론 도중 끊긴 상태. _data.json 만 남는다.
                sys.stderr.write(f"stub: 의도적 중단 ({name})\n")
                return abort_code

            if run_inference:
                write_finals(result_dir, name, zero=name in zero_names)
            print(f"Fold job {raw_name} done, output written to {result_dir}\n")
    except (ValueError, UnicodeDecodeError) as exc:
        # 근거 (5)(6): 순회가 여기서 멈춘다. 뒤의 입력은 처리되지 않는다.
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1

    print(f"Done running {index} fold jobs.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
