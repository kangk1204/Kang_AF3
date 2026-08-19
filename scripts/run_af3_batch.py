#!/usr/bin/env python3
"""AlphaFold 3 배치 실행 스크립트.

컨테이너를 한 번만 띄워 폴더 전체를 순회하므로, 파일마다 docker run 을
새로 띄우는 방식보다 빠르다 (실측 4.13배, RTX 5070 Ti, VHH 7건).

완료 판정은 폴더 존재가 아니라 최종 산출물 3종의 존재와 크기로 한다.
AF3 는 추론 전에 <name>_data.json 을 먼저 쓰기 때문에, 폴더만 보면
추론 중 중단된 것을 완료로 오판한다.

점검만 하려면:  python3 run_af3_batch.py --audit
"""
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
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

# AF3 가 추론을 끝냈을 때 결과 폴더 최상위에 남기는 파일들.
# _data.json 은 추론 '전' 에 쓰이므로 완료 근거가 될 수 없다.
REQUIRED_SUFFIXES = (
    ("_ranking_scores.csv",),
    ("_model.cif", "_model.cif.zst"),      # 압축 옵션에 따라 둘 중 하나
    ("_summary_confidences.json",),
)

# JSON 안에서 다른 파일을 상대경로로 가리킬 수 있는 키.
# AF3 는 상대경로를 JSON 파일 위치 기준으로 해석하므로,
# 이런 입력은 임시 폴더로 복사하면 경로가 깨진다.
SIDECAR_KEYS = (
    "mmcifPath", "unpairedMsaPath", "pairedMsaPath",
    "userCCDPath", "path",
)


def sanitised_name(name):
    """AF3가 출력 폴더 이름을 만드는 방식과 동일하게 정규화한다.

    run_alphafold.py 는 출력 폴더를 파일명이 아니라 JSON 의 name 필드로
    만든다 (folding_input.py 의 sanitised_name). 그래서 스킵 검사도
    같은 규칙을 써야 이미 끝난 것을 다시 돌리지 않는다.
    """
    allowed = set(string.ascii_letters + string.digits + "_-.")
    return "".join(c for c in name.replace(" ", "_") if c in allowed)


def read_input(json_file):
    """입력 JSON 을 읽어 (결과폴더이름, 원래이름, 사이드카여부, 오류메시지)."""
    try:
        with open(json_file, encoding="utf-8") as f:
            obj = json.load(f)
    except UnicodeDecodeError:
        return None, None, False, "UTF-8 이 아닙니다 (macOS 껍데기 파일일 수 있음)"
    except json.JSONDecodeError as e:
        return None, None, False, f"JSON 형식 오류 ({e.lineno}행 {e.colno}열: {e.msg})"
    except OSError as e:
        return None, None, False, f"파일을 읽을 수 없습니다 ({e.strerror})"

    if not isinstance(obj, dict):
        return None, None, False, "최상위가 객체(dict)가 아닙니다"
    raw = obj.get("name")
    if raw is None or not str(raw).strip():
        return None, None, False, "name 필드가 비어 있습니다"
    out = sanitised_name(str(raw))
    if not out:
        return (None, str(raw), False,
                f"name={str(raw)!r} 은 정규화하면 빈 문자열이 됩니다. "
                "영문/숫자/밑줄/하이픈/점을 한 자 이상 포함해야 합니다")
    return out, str(raw), has_sidecar(obj), None


def has_sidecar(obj):
    """JSON 안에 상대경로로 다른 파일을 가리키는 항목이 있는지 본다."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in SIDECAR_KEYS and isinstance(v, str) and v:
                    if not os.path.isabs(v):
                        return True
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def is_complete(result_dir, out_name):
    """결과 폴더가 '추론까지 끝난' 상태인지 최종 산출물로 판정한다."""
    if not result_dir.is_dir():
        return False
    for group in REQUIRED_SUFFIXES:
        for suffix in group:
            p = result_dir / f"{out_name}{suffix}"
            if p.is_file() and p.stat().st_size > 0:
                break
        else:
            return False
    return True


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

    # --- 입력 사전 검증 -------------------------------------------------
    # AF3 는 잘못된 JSON 을 만나면 그 자리에서 멈춘다. 폴더 전체를 넘기기 전에
    # 먼저 걸러내지 않으면 뒤에 있던 정상 입력까지 처리되지 않는다.
    bad, sidecar, by_out = [], [], {}
    entries = []
    for f in json_files:
        out_name, raw, side, err = read_input(f)
        if err:
            bad.append((f, err))
            continue
        if side:
            sidecar.append(f)
        by_out.setdefault(out_name, []).append(f)
        entries.append((f, out_name, raw))

    if bad:
        print(f"\n[오류] 읽을 수 없는 입력 {len(bad)}개를 발견했습니다. 먼저 고쳐 주세요.")
        for f, err in bad[:10]:
            print(f"   - {f.name}: {err}")
        if len(bad) > 10:
            print(f"   ... 그 외 {len(bad)-10}개")
        print("   (이 파일들을 폴더에서 빼거나 고친 뒤 다시 실행하세요.)")
        sys.exit(2)

    dup = {k: v for k, v in by_out.items() if len(v) > 1}
    if dup:
        print(f"\n[오류] 결과 폴더 이름이 겹치는 입력이 있습니다. 서로 덮어씁니다.")
        for out_name, group in list(dup.items())[:5]:
            print(f"   - '{out_name}' <- {', '.join(f.name for f in group)}")
        print("   각 JSON 의 name 필드를 서로 다르게 고친 뒤 다시 실행하세요.")
        sys.exit(2)

    # --- 완료 여부 판정 (최종 산출물 기준) ------------------------------
    todo, done, partial = [], [], []
    for f, out_name, _ in entries:
        rdir = output_dir / out_name
        if is_complete(rdir, out_name):
            done.append(f)
        else:
            if rdir.is_dir():
                partial.append((f, out_name))
            todo.append((f, out_name))

    print(f"\n[시작] JSON {len(entries)}개 중 완료 {len(done)}개, 남은 것 {len(todo)}개.")
    if partial:
        print(f"[안내] 폴더는 있으나 결과물이 없는 것 {len(partial)}개를 다시 돌립니다"
              f" (예: {partial[0][1]}). 추론 중 중단된 건입니다.")

    if "--audit" in sys.argv:
        print("\n[점검] --audit 이므로 실행하지 않고 끝냅니다.")
        if partial:
            for _, out_name in partial:
                print(f"   미완료: {output_dir.name}/{out_name}")
        sys.exit(1 if partial else 0)

    if not todo:
        print("[완료] 모두 이미 끝나 있습니다.")
        return

    if sidecar:
        names = ", ".join(f.name for f in sidecar[:3])
        print(f"[안내] 상대경로로 다른 파일을 참조하는 입력 {len(sidecar)}개가 있어"
              f" 파일별 실행으로 진행합니다 ({names}).")

    started = time.time()

    # 사이드카 입력이 있으면 임시 폴더 복사가 경로를 깨므로 파일별로 돌린다.
    if USE_SINGLE_RUN and not sidecar:
        run_all_at_once(todo, base_dir, input_dir, output_dir, db_dir, model_dir, cache_dir)
    else:
        run_one_by_one(todo, input_dir, output_dir, db_dir, model_dir, cache_dir)

    # 실제로 최종 산출물이 나왔는지 확인한다
    ok = sum(1 for _, out_name in todo if is_complete(output_dir / out_name, out_name))
    elapsed = time.time() - started
    per = elapsed / ok if ok else 0
    print(f"\n[완료] {ok}/{len(todo)}건 성공. 총 {elapsed/60:.1f}분, 건당 평균 {per:.1f}초.")
    if ok < len(todo):
        print(f"[안내] {len(todo)-ok}건이 남았습니다. 이 스크립트를 다시 실행하면 실패한 것만 재시도합니다.")
        sys.exit(1)   # 자동화에서 실패를 성공으로 오인하지 않도록


def docker_base(db_dir, model_dir, output_dir, cache_dir, in_mount, use_cache=True):
    """모든 실행에 공통인 docker 옵션."""
    cmd = [
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
    ]
    if use_cache:
        # 컴파일 결과를 다음 실행에서도 재사용한다 (구버전 이미지는 이 플래그를 모른다)
        cmd.append("--jax_compilation_cache_dir=/root/af3_cache")
    return cmd


def run_all_at_once(todo, base_dir, input_dir, output_dir, db_dir, model_dir, cache_dir):
    """컨테이너를 한 번만 띄워 남은 JSON 전체를 순회한다.

    --input_dir 은 폴더에 있는 JSON 을 전부 처리하므로, 남은 것만 골라
    임시 폴더에 복사해서 그 폴더를 넘긴다. 이렇게 하면 이미 끝난 것을
    다시 돌리지 않으면서도 컨테이너 기동과 가중치 로딩을 1회로 줄인다.
    """
    # 임시 폴더는 매 실행마다 고유한 이름으로 만든다. 고정 이름을 지우면
    # 동시에 돌리는 다른 실행이나 같은 이름의 기존 폴더를 날릴 수 있다.
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{INPUT_DIR_NAME}_todo_", dir=str(base_dir)))
    for f, _ in todo:
        shutil.copy2(f, stage_dir / f.name)

    supported = check_flags(db_dir, model_dir, output_dir, cache_dir, stage_dir)

    cmd = docker_base(db_dir, model_dir, output_dir, cache_dir, stage_dir,
                      use_cache="jax_compilation_cache_dir" in supported)
    if "input_dir" not in supported:
        # 도커 이미지의 AF3가 오래된 버전이다. 예전 방식으로 돌린다.
        shutil.rmtree(stage_dir, ignore_errors=True)
        print("[안내] 이 도커 이미지의 AlphaFold 3는 --input_dir 을 지원하지 않습니다.")
        print("       예전 방식(파일마다 실행)으로 진행합니다. 속도 개선은 제한됩니다.")
        print("       개선을 온전히 받으려면 AF3 이미지를 최신 버전으로 다시 빌드하세요.\n")
        run_one_by_one(todo, input_dir, output_dir, db_dir, model_dir, cache_dir,
                       use_cache="jax_compilation_cache_dir" in supported)
        return

    cmd.append("--input_dir=/root/af3_in")
    if SKIP_MSA:
        cmd.append("--norun_data_pipeline")

    print(f"[실행] 컨테이너 1회 기동으로 {len(todo)}건을 순회합니다.")
    print("[안내] 첫 1~2건은 컴파일 때문에 느리고, 그 다음부터 빨라집니다.\n")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # 순회가 중간에 멈췄다. 남은 것을 파일별로 돌려 문제 있는 한 건만
        # 건너뛰고 나머지를 살린다.
        left = [(f, n) for f, n in todo
                if not is_complete(output_dir / n, n)]
        print(f"\n[경고] 순회가 중단됐습니다. 남은 {len(left)}건을 파일별로 다시 시도합니다.")
        print("       한 건에서 막혀도 나머지는 계속 진행됩니다.\n")
        shutil.rmtree(stage_dir, ignore_errors=True)
        if left:
            run_one_by_one(left, input_dir, output_dir, db_dir, model_dir, cache_dir,
                           use_cache="jax_compilation_cache_dir" in supported)
        return
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 멈췄습니다. 다시 실행하면 이어서 합니다.")
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def check_flags(db_dir, model_dir, output_dir, cache_dir, in_mount):
    """도커 이미지의 AF3가 어떤 플래그를 지원하는지 --help 로 확인한다.

    이미지가 오래된 버전이면 --input_dir 을 모르고, 그때는 실행이 통째로
    실패한다. 미리 확인해서 예전 방식으로 자동 전환하기 위한 것이다.
    """
    cmd = docker_base(db_dir, model_dir, output_dir, cache_dir, in_mount,
                      use_cache=False)
    cmd = [c for c in cmd if not c.startswith(("--model_dir", "--db_dir", "--output_dir"))]
    cmd.append("--help")
    ALL = {"input_dir", "jax_compilation_cache_dir"}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("[경고] 도커 이미지 확인이 300초 안에 끝나지 않았습니다.")
        print("       최신 이미지로 가정하고 진행합니다. 실패하면 아래 명령으로 직접 확인하세요.")
        print("       sudo docker run --rm alphafold3 python run_alphafold.py --help | head -40")
        return ALL
    except OSError as e:
        print(f"[오류] docker 를 실행할 수 없습니다: {e}")
        print("       docker 설치와 sudo 권한을 확인하세요.")
        sys.exit(1)

    text = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 or not text.strip():
        # 확인 자체가 실패했다. 원인을 보여주고 최신 이미지로 가정한다.
        print(f"[경고] 도커 이미지 확인이 실패했습니다 (종료코드 {p.returncode}).")
        for line in [l for l in text.splitlines() if l.strip()][-5:]:
            print(f"       {line}")
        print("       최신 이미지로 가정하고 진행합니다.")
        return ALL
    return {f for f in ALL if "--" + f in text}


def run_one_by_one(todo, input_dir, output_dir, db_dir, model_dir, cache_dir,
                   use_cache=None):
    """예전 방식. 파일마다 컨테이너를 새로 띄운다 (느리다)."""
    print("[안내] 파일마다 컨테이너를 새로 띄우는 방식입니다. 건당 고정 비용이 반복됩니다.\n")
    if use_cache is None:
        use_cache = "jax_compilation_cache_dir" in check_flags(
            db_dir, model_dir, output_dir, cache_dir, input_dir)
    for idx, item in enumerate(todo, 1):
        json_file = item[0] if isinstance(item, tuple) else item
        print(f"[{idx}/{len(todo)}] 연산 중: {json_file.name}")
        cmd = docker_base(db_dir, model_dir, output_dir, cache_dir, input_dir,
                          use_cache=use_cache)
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
