# AlphaFold 3 출력물 고지 (AlphaFold 3 Output Notice)

이 저장소는 AlphaFold 3 로 만든 결과물을 함께 담아 배포한다. 아래 파일들이 그것이다.

| 위치 | 내용 |
|---|---|
| `results_example/` | confidence-derived 집계·비교 CSV와 별도의 timing/resource/projection 기록 |
| `figures/` | AF3-derived pLDDT·PAE·구조 그림과 별도의 runtime benchmark 그림 |
| `examples/view3d_example.html` | 좌표(mmCIF)를 내장하고 신뢰도 지표를 덧붙인 수정 뷰어 |

## 고지

**이 결과물은 AlphaFold 3 Output Terms of Use 하에 제공된다.**

- 약관 원문:
  <https://github.com/google-deepmind/alphafold3/blob/main/OUTPUT_TERMS_OF_USE.md>
- 기준 판본: 2024-11-09 (pinned AF3 commit `97d2023` 에 포함된 사본)
- 동봉 사본: [OUTPUT_TERMS_OF_USE.md](OUTPUT_TERMS_OF_USE.md)
- 필수 원문 고지: [LEGALLY_BINDING_TERMS_OF_USE.txt](LEGALLY_BINDING_TERMS_OF_USE.txt)

약관은 출력물을 공개하거나 배포할 때 **이 약관이 적용된다는 사실과, 출력물에 가한
수정 사항을 눈에 띄게 고지할 것**을 요구한다 (원문 5항). 이 파일이 그 고지다.

## 이 저장소가 출력물에 가한 수정

원본 AF3 출력을 그대로 싣지 않은 부분이 있다. 무엇을 했는지 적는다.

| 파일 | AF3 원본 | 이 저장소가 한 일 |
|---|---|---|
| `results_example/af3_summary.csv`, `visualize_table.csv`, confidence 비교 CSV | AF3 JSON의 confidence arrays/scalars | 여러 타깃을 표로 모으고 한국어 열, 경고와 local heuristic 등급을 추가했다. `pLDDT평균`은 `atom_plddts`에서 이 저장소가 계산한 **atom-weighted mean**이며 AF3 원본 scalar가 아니다. 기준과 한계는 README 8-4에 있다 |
| `ab_benchmark.csv`, `msa_threads.csv`, `msa_throughput.csv`, `sort_effect.csv`, `stage2_timing.csv` | 실행 로그와 host 계측 | 실행 구간·조건·자원 사용을 구조화한 runtime record다. AF3 confidence 출력값을 모은 표가 아니다 |
| `projection_2000.csv` | 직접 측정값과 연구자 보고값 | 2,000건 소요시간을 산술 투영했다. end-to-end 실측과 projection을 구분한다 |
| `figures/*_plddt.png`, `*_pae.png`, confidence/structure 그림 | AF3 confidence JSON·mmCIF | residue/atom 집계, 색, 축, 범례와 주석을 추가했다. local grade와 cutoff는 AF3가 정의하거나 보정한 값이 아니다 |
| `figures/*benchmark*.png`, `baseline_gpu5070ti.png`, `msa_threads_scaling.png` | runtime CSV | 측정값을 축·단위·주석과 함께 재표현했다. builder와 입력 hash는 `ARTIFACT_MANIFEST.json`에 기록한다 |
| `figures/view3d_screenshot.png` | 생성 viewer의 화면 | viewer UI를 캡처한 역사적 screenshot이다. 원본 browser/session capture가 없어 byte 재현 불가로 표시한다 |
| `examples/view3d_example.html` | `*_model.cif` 와 요약 지표 | mmCIF를 base64로 넣고 계산 지표·색·설명을 추가했다. 좌표/B-factor를 재작성하지 않았지만 HTML 전체는 수정 표현이다 |

`ranking_score`, `pTM`, `ipTM`은 AF3 JSON scalar를 그대로 전사한다. `pLDDT평균`,
등급, 경고, sample range와 projection은 이 저장소가 계산한 파생값이다. 특히 atom-weighted
global pLDDT 등급과 within-run diffusion-sample range cutoff는 calibration되지 않은 local
heuristic이며 결합, affinity, native 정확도 또는 재현성 불확실성의 척도가 아니다.

## 이 결과물을 다시 배포할 때

논문, 발표, 다른 저장소 등에 이 결과물이나 그로부터 만든 것을 실을 때도 같은 고지가
따라가야 한다. `OUTPUT_TERMS_OF_USE.md`, `LEGALLY_BINDING_TERMS_OF_USE.txt`, 이 수정 고지를
함께 배포하고 원 약관의 조건을 직접 확인한다. 생성기는 이 세 파일을 출력 폴더에 복사한다.

출력물 약관의 사용 제한(비영리 한정, 파생 모델 학습 금지 등)은 README 11절과
[docs/license_notes.md](docs/license_notes.md) 에 정리돼 있다. 법적 판단은 약관 원문과
소속 기관을 통해 확인해야 한다.

## 필수 인용

Abramson J, et al. Accurate structure prediction of biomolecular interactions
with AlphaFold 3. *Nature*. 2024. <https://doi.org/10.1038/s41586-024-07487-w>.
