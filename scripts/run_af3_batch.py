#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# =========================================================
# USER CONFIG (사용자 설정)
# 여기서 인풋(JSON) 폴더와 아웃풋(결과 저장) 폴더 이름을 지정하세요.
# =========================================================
INPUT_DIR_NAME = "vhh_001_in"
OUTPUT_DIR_NAME = "vhh_001_out"

# 컨테이너를 한 번만 띄우고 폴더 전체를 순회합니다. (속도의 핵심)
# 문제가 생겨 예전처럼 파일마다 따로 돌리고 싶으면 False 로 바꾸세요.
USE_SINGLE_RUN = True

# MSA(서열 검색)를 건너뛰고 추론만 합니다.
# 이전 실행에서 나온 *_data.json 을 인풋으로 줄 때만 True 로 하세요.
SKIP_MSA = False
# =========================================================


def sanitised_name(name):
    """AF3가 출력 폴더 이름을 만드는 방식과 동일하게 정규화한다.

    run_alphafold.py 는 출력 폴더를 파일명이 아니라 JSON 의 name 필드로
    만든다 (folding_input.py 의 sanitised_name). 그래서 스킵 검사도
    같은 규칙을 써야 이미 끝난 것을 다시 돌리지 않는다.
    """
    import string

    allowed = set(string.ascii_letters + string.digits + "_-.")
    return "".join(c for c in name.replace(" ", "_") if c in allowed)


def output_name_of(json_file):
    """JSON 의 name 필드를 읽어 결과 폴더 이름을 알아낸다."""
    try:
        with open(json_file, encoding="utf-8") as f:
            name = json.load(f).get("name")
        if name:
            return sanitised_name(str(name))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        pass
    # name 을 못 읽으면 파일명으로 대신한다 (기존 동작).
    return json_file.stem


def main():
    # 현재 터미널이 열려있는 위치(예: SNAP25 폴더)를 기준으로 경로 자동 설정
    base_dir = Path.cwd()
    input_dir = base_dir / INPUT_DIR_NAME
    output_dir = base_dir / OUTPUT_DIR_NAME
    home_dir = Path.home()

    # 고정 데이터베이스 및 가중치 경로
    db_dir = home_dir / "public_databases"
    model_dir = home_dir / "af3_models"

    # XLA 컴파일 결과를 재사용할 폴더 (없으면 자동 생성)
    cache_dir = home_dir / "af3_cache"

    # 필수 경로 존재 여부 팩트 체크
    if not db_dir.exists():
        print(f"[오류] 데이터베이스 폴더를 찾을 수 없습니다: {db_dir}")
        sys.exit(1)
    if not model_dir.exists():
        print(f"[오류] 가중치(모델) 폴더를 찾을 수 없습니다: {model_dir}")
        sys.exit(1)
    if not input_dir.exists():
        print(f"[오류] 설정하신 인풋 폴더('{INPUT_DIR_NAME}')를 현재 경로에서 찾을 수 없습니다.")
        sys.exit(1)

    # 지정한 아웃풋 폴더가 없으면 자동으로 폴더 생성
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 인풋 폴더 내부의 JSON 파일 목록 수집
    # '._' 로 시작하는 파일은 macOS 가 만든 껍데기 파일이라 읽으면 오류가 난다. 제외한다.
    json_files = sorted(
        f for f in input_dir.glob("*.json") if not f.name.startswith("._")
    )
    if not json_files:
        print(f"[오류] '{input_dir}' 폴더에 JSON 파일이 없습니다.")
        sys.exit(1)

    hidden = [f.name for f in input_dir.glob("._*.json")]
    if hidden:
        print(f"[안내] macOS 껍데기 파일 {len(hidden)}개를 건너뜁니다 (예: {hidden[0]}).")

    # 이미 끝난 것과 남은 것을 가른다
    todo, done = [], []
    for json_file in json_files:
        if (output_dir / output_name_of(json_file)).exists():
            done.append(json_file)
        else:
            todo.append(json_file)

    print(f"[시작] JSON {len(json_files)}개 중 완료 {len(done)}개, 남은 것 {len(todo)}개.")
    if not todo:
        print("[완료] 모두 이미 끝나 있습니다.")
        return

    started = time.time()

    if USE_SINGLE_RUN:
        run_all_at_once(todo, base_dir, input_dir, output_dir, db_dir, model_dir, cache_dir)
    else:
        run_one_by_one(todo, input_dir, output_dir, db_dir, model_dir, cache_dir)

    # 실제로 결과가 나왔는지 확인한다
    ok = sum(1 for f in todo if (output_dir / output_name_of(f)).exists())
    elapsed = time.time() - started
    per = elapsed / ok if ok else 0
    print(f"\n[완료] {ok}/{len(todo)}건 성공. 총 {elapsed/60:.1f}분, 건당 평균 {per:.1f}초.")
    if ok < len(todo):
        print(f"[안내] {len(todo)-ok}건이 남았습니다. 이 스크립트를 다시 실행하면 실패한 것만 재시도합니다.")


def docker_base(db_dir, model_dir, output_dir, cache_dir, in_mount):
    """모든 실행에 공통인 docker 옵션."""
    return [
        "sudo", "docker", "run", "--rm", "--gpus", "all",
        "-v", f"{db_dir}:/root/public_databases",
        "-v", f"{model_dir}:/root/af3_models",
        "-v", f"{in_mount}:/root/af3_in",
        "-v", f"{output_dir}:/root/af3_out",
        "-v", f"{cache_dir}:/root/af3_cache",
        "alphafold3",
        "python", "run_alphafold.py",
        "--model_dir=/root/af3_models",
        "--db_dir=/root/public_databases",
        "--output_dir=/root/af3_out",
        # 컴파일 결과를 다음 실행에서도 재사용한다
        "--jax_compilation_cache_dir=/root/af3_cache",
    ]


def run_all_at_once(todo, base_dir, input_dir, output_dir, db_dir, model_dir, cache_dir):
    """컨테이너를 한 번만 띄워 남은 JSON 전체를 순회한다.

    --input_dir 은 폴더에 있는 JSON 을 전부 처리하므로, 남은 것만 골라
    임시 폴더에 복사해서 그 폴더를 넘긴다. 이렇게 하면 이미 끝난 것을
    다시 돌리지 않으면서도 컨테이너 기동과 가중치 로딩을 1회로 줄인다.
    """
    stage_dir = base_dir / f".{INPUT_DIR_NAME}_todo"
    shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True)
    for f in todo:
        shutil.copy2(f, stage_dir / f.name)

    cmd = docker_base(db_dir, model_dir, output_dir, cache_dir, stage_dir)
    cmd.append("--input_dir=/root/af3_in")
    if SKIP_MSA:
        cmd.append("--norun_data_pipeline")

    print(f"[실행] 컨테이너 1회 기동으로 {len(todo)}건을 순회합니다.")
    print("[안내] 첫 1~2건은 컴파일 때문에 느리고, 그 다음부터 빨라집니다.\n")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("\n[경고] 순회 중 중단됐습니다. 끝난 것은 남아 있으니 다시 실행하면 이어서 합니다.")
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 멈췄습니다. 다시 실행하면 이어서 합니다.")
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def run_one_by_one(todo, input_dir, output_dir, db_dir, model_dir, cache_dir):
    """예전 방식. 파일마다 컨테이너를 새로 띄운다 (느리다)."""
    print("[안내] 파일마다 컨테이너를 새로 띄우는 방식입니다. 건당 고정 비용이 반복됩니다.\n")
    for idx, json_file in enumerate(todo, 1):
        print(f"[{idx}/{len(todo)}] 연산 중: {json_file.name}")
        cmd = docker_base(db_dir, model_dir, output_dir, cache_dir, input_dir)
        cmd.append(f"--json_path=/root/af3_in/{json_file.name}")
        if SKIP_MSA:
            cmd.append("--norun_data_pipeline")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"[경고] {json_file.name} 연산 중 오류가 발생했습니다. 다음 파일로 넘어갑니다.")
        except KeyboardInterrupt:
            print("\n[중단] 사용자가 멈췄습니다.")
            return


if __name__ == "__main__":
    main()
