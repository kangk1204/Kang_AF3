# AlphaFold 3 출력물 고지 (AlphaFold 3 Output Notice)

이 저장소는 AlphaFold 3 로 만든 결과물을 함께 담아 배포한다. 아래 파일들이 그것이다.

| 위치 | 내용 |
|---|---|
| `results_example/` | 신뢰도 집계 CSV, DB 비교, MSA 비교 등 실측 결과 |
| `figures/` | 위 결과로 그린 pLDDT·PAE·요약 그림 |
| `examples/view3d_example.html` | 구조 좌표(mmCIF)와 신뢰도 지표가 들어 있는 뷰어 |

## 고지

**이 결과물은 AlphaFold 3 Output Terms of Use 하에 제공된다.**

- 약관 원문:
  <https://github.com/google-deepmind/alphafold3/blob/main/OUTPUT_TERMS_OF_USE.md>
- 기준 판본: 2024-11-09 (pinned AF3 commit `97d2023` 에 포함된 사본)

약관은 출력물을 공개하거나 배포할 때 **이 약관이 적용된다는 사실과, 출력물에 가한
수정 사항을 눈에 띄게 고지할 것**을 요구한다 (원문 5항). 이 파일이 그 고지다.

## 이 저장소가 출력물에 가한 수정

원본 AF3 출력을 그대로 싣지 않은 부분이 있다. 무엇을 했는지 적는다.

| 파일 | AF3 원본 | 이 저장소가 한 일 |
|---|---|---|
| `results_example/*.csv` | `*_summary_confidences.json` 의 값 | 여러 타깃을 한 표로 모으고, 열 이름을 한국어로 붙이고, `등급`·`경고` 열을 **새로 계산해 추가**했다. `등급` 은 AF3 가 준 값이 아니라 이 저장소가 정한 분류다 (기준은 README 8-3) |
| `figures/*_plddt.png`, `*_pae.png` | `*_confidences.json` 의 잔기별 pLDDT 와 PAE 행렬 | 그림으로 그렸다. 값 자체는 바꾸지 않았다 |
| `figures/confidence_overview.png` 등 요약 그림 | 여러 실행의 요약 지표 | 축·구간·주석을 붙여 비교 그림으로 재구성했다 |
| `examples/view3d_example.html` | `*_model.cif` 와 요약 지표 | mmCIF 를 base64 로 인코딩해 HTML 안에 넣고, 지표 표와 3D 뷰어를 붙였다. 좌표와 B-factor(pLDDT) 값은 원본 그대로다 |

집계 CSV 의 원본 지표(`ranking_score`, `pTM`, `ipTM`, `pLDDT평균`)는 AF3 가 쓴 JSON 과
값이 일치한다. 이 저장소는 원본 지표를 고치거나 보정하지 않는다.

## 이 결과물을 다시 배포할 때

논문, 발표, 다른 저장소 등에 이 결과물이나 그로부터 만든 것을 실을 때도 같은 고지가
따라가야 한다. 이 파일을 함께 두거나, 위 약관 링크와 수정 사항을 명시하면 된다.

출력물 약관의 사용 제한(비영리 한정, 파생 모델 학습 금지 등)은 README 11절과
[docs/license_notes.md](docs/license_notes.md) 에 정리돼 있다. 법적 판단은 약관 원문과
소속 기관을 통해 확인해야 한다.
