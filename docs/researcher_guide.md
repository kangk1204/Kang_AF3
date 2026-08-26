> 이 문서는 저장소 `docs/researcher_guide.md` 다. 한글 제목은 "실험 연구자용 요약".
> 실험을 하는 사람이 이 도구로 후보를 고르는 데 필요한 것만 담았다.
> 설치 옵션, 성능 근거, 측정 원자료는 최상위 [README.md](../README.md) 에 있다.

# 실험 연구자용 요약

## 이 도구가 하는 일

AlphaFold 3 를 여러 서열에 대해 한 번에 돌리고, 결과를 표 하나와 그림으로 정리한다.
수백에서 수천 건 중에서 **실험할 수십 건을 고르는 데** 쓴다.

구조 예측 자체는 AlphaFold 3 가 한다. 이 저장소는 실행과 정리만 맡는다. AF3 가 낸
숫자를 고치거나 보정하지 않는다. 집계 표의 값은 AF3 가 쓴 원본 JSON 과 일치한다.

## 이 도구가 하지 않는 일

- 결합 여부를 판정하지 않는다. 신뢰도 점수와 순위까지만 낸다.
- 실험 검증을 대신하지 않는다. 예측이 맞는지는 실험으로 확인해야 한다.
- 친화도(Kd, IC50)를 예측하지 않는다. AF3 에 그런 출력이 없다.

## 시작 전에 준비할 것

| 항목 | 내용 |
|---|---|
| 장비 | NVIDIA GPU 1개, Ubuntu 22.04 / 24.04 / 26.04 |
| 디스크 | 설치 중 약 1TB 여유가 필요하다. 끝난 뒤 압축본을 지우면 **약 627GiB** 로 줄어든다 |
| 모델 가중치 | 본인이 **직접 내려받는다**. 신청·승인 절차는 없다. 동료에게 복사받으면 약관 위반이다 |
| 시간 | 설치와 DB 내려받기에 수 시간 |

설치가 끝나면 `rm -rf ~/public_databases_full/_zst` 로 압축본 223GiB 를 지울 수 있다.
지운 뒤에도 실행과 검증은 그대로 된다. RNA 전용 DB 3종은 단백질만 다뤄도 지우면 안 된다.

가중치는 설치기가 자동으로 내려받는다(약 1.1GB, `~/af3_models/af3.bin`). 신청서를
내거나 승인을 기다리는 절차는 없다. 대신 약관이 **받아서 쓰는 행위 자체를 동의**로
본다. 그러니 설치 전에
[WEIGHTS_TERMS_OF_USE.md](https://github.com/google-deepmind/alphafold3/blob/main/WEIGHTS_TERMS_OF_USE.md)
를 읽고 세 가지를 확인한다. ① 비영리 연구 목적인가 ② 받은 가중치를 남에게 주지 않을
것인가 ③ AF3 출력으로 다른 구조예측 모델을 학습시키지 않을 것인가. 내려받은 날짜를
적어 두면 나중에 논문 심사나 기관 감사에서 근거가 된다. 자세한 것은
[docs/license_notes.md](license_notes.md) 에 있다.

## 설치

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/kangk1204/Kang_AF3.git ~/af3_work/Kang_AF3
cd ~/af3_work/Kang_AF3
nvidia-smi                                    # GPU 표가 나오는지 먼저 확인
bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms
```

`nvidia-smi` 가 실패하면 설치기를 돌리지 말고 드라이버부터 잡는다. 설치기는 드라이버를
건드리지 않는다.

## 설치 전에 결과물부터 보기

내려받을 것 없이 저장소만 열어도 이 도구가 무엇을 만드는지 볼 수 있다.

| 볼 것 | 파일 |
|---|---|
| 3D 구조 뷰어 | `examples/view3d_example.html` (브라우저로 더블클릭) |
| 신뢰도 집계표 | `results_example/af3_summary.csv` |
| 그림 | `figures/example_complex_plddt.png`, `figures/example_complex_pae.png` |

## 예제 1건으로 확인

MSA 용 데이터베이스를 잘라 둔 사본(overlay)을 먼저 만든다. 같은 예제가 34.8분에서
1분 아래로 줄어든다.

```bash
python3 scripts/af3_db.py reduce \
    --source ~/public_databases_full --output ~/public_databases_reduced

mkdir -p quick_in && cp examples/vhh_monomer.json quick_in/
python3 scripts/run_af3_batch_improved.py --input-dir quick_in --output-dir quick_out \
    --db-dir ~/public_databases_reduced --db-dir ~/public_databases_full --yes

python3 scripts/af3_collect.py quick_out -o quick_summary.csv
~/af3_plot_env/bin/python scripts/af3_visualize.py quick_out -o quick_figures
python3 scripts/af3_view3d.py quick_out --out-dir quick_viewer
```

`quick_summary.csv` 에 한 줄이 생기고 `quick_viewer/index.html` 에서 구조를 돌려 볼 수
있으면 설치가 끝난 것이다.

## 내 서열로 돌리기

FASTA 나 CSV 에서 입력 JSON 을 만든다.

```bash
# 서열마다 따로 예측
python3 scripts/af3_prepare.py --fasta my_seqs.fasta -o my_in --dry-run
python3 scripts/af3_prepare.py --fasta my_seqs.fasta -o my_in

# 모든 서열에 같은 항원을 붙여 복합체로
python3 scripts/af3_prepare.py --fasta my_vhhs.fasta --partner antigen.fasta -o my_in
```

`--dry-run` 은 파일을 만들지 않고 무엇이 만들어질지만 보여 준다. 먼저 이것으로 확인한다.
그다음은 예제와 같다. `--input-dir my_in --output-dir my_out` 으로 바꿔 실행한다.

## 결과 읽는 법

집계 CSV 에서 먼저 볼 열은 넷이다.

| 열 | 뜻 |
|---|---|
| `등급` | 아래 기준으로 자동 분류한 값 |
| `경고` | 확인이 필요한 항목 (`충돌`, `무질서`, `MSA얕음`, `샘플불안`) |
| `ranking_score` | AF3 가 후보를 세우는 값. 클수록 좋다 |
| `MSA_unpaired깊이` | 서열을 몇 개나 찾았는지. 이 값이 결과의 신뢰도를 좌우한다 |

등급 기준은 이렇다.

| 입력 | 등급 | 조건 |
|---|---|---|
| 복합체 | `A_계면신뢰` | ipTM ≥ 0.8 이고 pLDDT평균 ≥ 80 |
| | `B_계면회색` | ipTM ≥ 0.6 |
| | `C_계면실패` | 그 외 |
| 단량체 | `A_높음` | pLDDT평균 ≥ 90 이고 pTM ≥ 0.7 |
| | `B_신뢰` | pLDDT평균 ≥ 80 이고 pTM ≥ 0.5 |
| | `C_보통` | pLDDT평균 ≥ 70 |
| | `D_낮음` | 그 외 |

검토 순서는 이렇게 한다.

1. `D_낮음` 과 `C_계면실패` 를 뺀다.
2. `경고` 에 `충돌` 이 있는 건은 구조를 직접 열어 본다.
3. `샘플불안` 이 있는 건은 시드를 늘려 다시 돌린다.
4. 남은 것을 ipTM(복합체) 또는 pTM(단량체) 내림차순으로 정렬한다.
5. 상위 수십 건만 3D 뷰어로 눈으로 본다.
6. 그중에서 실험할 것을 고른다.

## 믿을 것과 믿지 말 것

**등급은 AF3 가 준 값이 아니라 이 저장소가 정한 분류다.** 원본 지표(ranking_score,
pTM, ipTM, pLDDT)를 같은 표에 함께 실어 두었으니 기준이 마음에 들지 않으면 원본 값으로
직접 다시 자르면 된다.

**overlay 로 얻은 계면 신뢰도(ipTM)를 최종값으로 인용하면 안 된다.** overlay 는 MSA 용
데이터베이스를 잘라 쓴 것이라 찾는 서열 수가 줄고, 그만큼 계면 예측이 흔들린다. 실측에서
같은 복합체가 overlay 0.85 / 전체 DB 0.90 으로 낮게 나온 경우도 있었고, 반대로 overlay
0.59 / 전체 DB 0.20 으로 **높게** 나온 경우도 있었다. 방향이 일정하지 않다.

**`MSA_unpaired깊이` 를 함께 본다.** 이 값이 수백 이상이면 overlay 결과가 전체 DB 와
거의 같았다. 한 자리에서 열 자리 수준이면 계면 지표를 그대로 믿을 수 없다. 그런 건은
거르는 데만 쓰고, 남긴 후보는 전체 DB 로 다시 돌린다.

대조한 복합체 7건에서 등급이 뒤집힌 적은 없다. 거르는 용도로 overlay 를 쓰는 근거가
이것이다. 다만 7건은 규칙을 세우기에 적은 수다.

## 시간과 용량

RTX 3080 Ti, 32 논리코어, 데이터베이스는 회전 디스크(HDD)에 둔 장비에서 잰 값이다.

| 작업 | overlay | 전체 DB |
|---|---|---|
| 단량체 116잔기 1건 | 52.6초 | 34.8분 |
| 복합체 2사슬 277잔기 | — | 69.8분 |
| 복합체 3사슬 763잔기 | 138.9초 | 106.9분 |

전체 DB 로 돌릴 때는 **사슬 수 × 35분** 으로 잡으면 된다. 사슬 길이는 거의 영향이 없다.
73잔기와 350잔기가 둘 다 35분이었다. 걸리는 시간의 대부분은 데이터베이스를 디스크에서
읽는 시간이라, 데이터베이스를 SSD 에 두면 이 표는 크게 달라진다.

overlay 사본은 만드는 데 16.7초, 용량 1.9GB 다. 전체 DB 를 대신하지 않는다. 템플릿은
전체 DB 에서 가져오므로 `--db-dir` 을 두 번 준다.

2000건 규모로 환산하면 overlay 로 거른 뒤 상위만 전체 DB 로 다시 돌리는 방식이 약 24시간,
전량을 전체 DB 로 돌리면 약 1,017시간이다.

## 자주 막히는 곳

| 증상 | 원인 | 대응 |
|---|---|---|
| `FAIL source DB directory does not exist` | `--full` 없이 설치했다 | `bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms` |
| 종료코드 2, "다른 AF3 실행이 GPU 를 쓰고 있다" | AF3 는 GPU 메모리를 거의 전부 잡는다 | 앞 작업이 끝난 뒤 시작한다. `docker ps` 로 확인 |
| 로그가 몇 분씩 멈춘 것처럼 보인다 | MSA 검색 중이다 | 정상이다. `docker ps` 로 살아 있는지만 본다 |
| 뷰어에 구조가 안 나온다 | 인터넷에서 3D 라이브러리를 받아야 한다 | 사내망이면 `--lib embed` 로 다시 만든다 |
| 결과 파일이 root 소유라 못 지운다 | 예전 버전으로 돌렸다 | `sudo chown -R $USER:$USER <결과폴더>` 후 최신 버전으로 받는다 |

중간에 멈춰야 하면 Ctrl-C 를 쓴다. 컨테이너는 러너가 정리한다. 같은 명령을 다시 실행하면
끝난 것은 건너뛰고 남은 것만 이어서 한다.

**한 번에 하나만 돌린다.** 같은 GPU 에서 둘을 겹치면 뒤에 시작한 쪽이 죽는다.

## 더 볼 문서

| 문서 | 언제 보나 |
|---|---|
| [README.md](../README.md) | 설치 옵션, 입력 유형별 예제, 측정 근거 전체 |
| [docs/operations_guide.md](operations_guide.md) | 대량 실행 운영, 모니터링 |
| [docs/commands.md](commands.md) | 복사해 쓰는 명령 모음 |
| [docs/license_notes.md](license_notes.md) | 가중치 약관, 논문에 쓸 때 확인할 것 |
