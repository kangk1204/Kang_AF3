# Releases

## v0.1.2

공동연구자에게 전달하기 전 최종 감사에서 확인된 실행·설치 경계를 보강한 버전입니다.

- 공식 AF3가 같은 서열의 사슬 ID를 묶어 쓰거나 MSA 기본값을 채워도 정상 결과를 올바르게 인식합니다.
- Docker 이미지는 검사한 immutable digest로 실행하고 계산 컨테이너의 외부 네트워크를 차단합니다.
- DB seal·overlay 동시 생성, 결과 폴더 symlink, 잘못된 sidecar 경로를 fail-closed로 처리합니다.
- 대량 배치 watchdog은 현재 대상만 확인하며, installer download와 clean-Ubuntu probe에는 중단 한계를 둡니다.
- Quick Start를 초보 wet-lab 연구자용 공손체로 다듬고, 실제 Table → Mol* 화면을 클릭 가능한 이미지로 보여 줍니다.
- AF3 결과 이미지의 source hash와 crop 좌표를 manifest에 기록해 같은 바이트로 재생성할 수 있습니다.
- 구조 confidence는 결합·affinity·epitope·native 정확도·SOTA의 실험 검증이 아니라는 경계를 유지합니다.

## v0.1.1

첫 공개 미리보기 버전입니다.

- Quick Start를 설치 → 예제 1건 → 내 서열 → 결과 확인 순서로 정리했습니다.
- 다양한 입력 유형(multi-FASTA, homomer, 공통 항원/파트너, ligand, 3사슬 JSON)을 README에서 다시 찾기 쉽게 정리했습니다.
- 결과 예시는 전체 Table, 개별 Mol* View, 2D 그림을 한 흐름으로 보여 줍니다.
- 브라우저에서 바로 열 수 있는 예제 결과물과 release note 연결을 추가했습니다.
