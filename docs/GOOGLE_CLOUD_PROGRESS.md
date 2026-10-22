# Google Cloud 이전 진행 기록

## 2026-10-21 — 크레딧을 이용한 테스트 배포 준비 시작

- 사용자가 300달러 크레딧을 받았다고 알렸고 예산 알림 설정보다 실제 이전을
  우선하도록 요청했다. 크레딧 사용 테스트 준비를 진행한다. 이는 크레딧 종료 후
  월 5만 원 예산을 초과하는 상시 운영을 승인한 것으로 해석하지 않는다.
- 콘솔 스크린샷에서 확인한 프로젝트는 `active-venture-449808-a5`이며 표시 이름은
  `My First Project`다. 프로젝트 번호는 `290262245390`이다.
- 현재 에이전트 환경에는 Google Cloud 연결 계정/인증 구성이 관측되지 않았고,
  gcloud도 설치돼 있지 않다. 네트워크 허용 범위에도 Google 관리 API가 없다.
  사용자 콘솔 로그인만으로 에이전트가 프로젝트를 제어할 수 있다고 보고하지 않는다.
- 사용자 본인의 Google Cloud Shell을 통한 실행을 준비한다. 비밀번호·토큰·
  서비스 계정 비밀키를 채팅으로 요구하지 않는다. 장기 배포 권한을 에이전트에
  연결하는 별도 설정을 했다고 가정하지 않는다.
- `scripts/gcp_preflight.sh`는 Cloud Shell에서 프로젝트 접근·결제 연결·현재 API를
  확인하는 읽기 전용 스크립트다. 자원 생성/서비스 활성화/결제 전환을 하지 않는다.
  로컬 셸 구문·도움말과 인증 도구 부재 시의 실패 처리는 확인한다. Google에서의
  실제 실행은 Cloud Shell 준비 후 확인해야 한다.
- 다음 사용자 동작: 콘솔 오른쪽 위 `>_` 아이콘으로 Cloud Shell을 열고 첫 실행
  안내를 완료한다. 이후 프로젝트 접근과 결제 연결부터 확인한다. 이 화면 준비가
  서버 생성이나 데이터 이전 완료를 뜻하지 않는다.
- 기존 Railway 환경·DB·카카오 설정을 변경하지 않는다. 실제 Google 자원,
  새 사용 주소, 원본 DB 백업/복원, 6단계 공개 검수는 아직 완료되지 않았다.

## 소스 보존 실행 결과

- 원본 소스 147개 파일을 비밀값·개발 환경·캐시·DB 파일 없이 보관한 아카이브를
  `/workspace/artifacts/coffegroup-gcp/yeongcha-source-before-google-cloud.tar.gz`에
  만들었다. 압축 안의 모든 파일을 원본과 바이트 단위로 비교했다.
- SHA256: `a12ddba80c8390c256d9e4ebf2a224f55b8377e3c2830d5757c7d6298dcebd92`.
- 전체 소스와 6단계 구현을 첫 커밋 `9fe3f44`로 보존하고 사용자 저장소
  `obamapop-hue/coffegroup`의 `codex/google-cloud-migration` 브랜치로
  push에 성공했다. main 브랜치를 덮어쓰거나 강제 push하지 않았다.
- 이 백업은 코드 백업이다. 원격 주문 DB, 비밀값, 현재 로그인 상태의 백업을
  완료했다는 뜻은 아니다. Google 테스트 배포와 실제 데이터 이전도 아직 남아 있다.
- 기초 점검 스크립트의 셸 구문·도움말·잘못된 프로젝트 ID 거부·gcloud 부재 시
  실패 처리를 로컬에서 확인했다. Google Cloud Shell 인증 후 실제 접근 검증을
  이어가야 한다.

## 2026-10-22 — Cloud Shell 사전 확인·이미지 빌드 성공

다음 결과는 사용자 본인이 Cloud Shell에서 실행하고 전달한 출력으로 확인했다.
에이전트가 Google Cloud API를 직접 호출하거나 새 배포를 조회한 결과는 아니다.

- Cloud Shell 연결과 프로젝트 선택이 완료됐다. 모바일 화면 전환 후에도
  `/home/obamapop/yeongcha/scripts/gcp_preflight.sh` 파일이 남아 있음을 확인했다.
- 사전 점검 결과: 프로젝트 `ACTIVE`, 결제 연결 `True`, 기능 목록 조회 성공.
- Cloud Run, Cloud SQL Admin, Artifact Registry, Cloud Build, Secret Manager,
  IAM API 활성화 요청이 성공했다.
- 서울 `asia-northeast3`의 Docker 저장소 `yeongcha` 생성이 성공했다.
- 기본 빌드 계정 `290262245390-compute@developer.gserviceaccount.com`에
  이 프로젝트의 `roles/cloudbuild.builds.builder` 역할 부여가 성공했다.
  이후 웹 실행용 계정은 별도로 만들어 빌드 권한을 그대로 사용하지 않도록 한다.
- 빌드 ID: `26adb1a8-2a37-4143-be48-8afebe25154b`.
- 빌드 생성 시각: `2026-10-22T07:57:32+00:00` (한국 시간 16:57:32).
- 빌드 결과: `STATUS: SUCCESS`, `DURATION: 34S`. 이미지 업로드까지 완료됐다.
- 이미지: `asia-northeast3-docker.pkg.dev/active-venture-449808-a5/yeongcha/app:gcp-initial`.
- 고정 이미지 digest: `sha256:07b198bd40efc71d2084e7ab5fe537c4f928d83cefdef4a9fcf3e309fb2d8d22`.
  실제 배포에서는 이 digest로 검증한 이미지를 지정한다.
- 로그의 legacy builder 안내와 pip root 경고는 빌드 실패가 아니었다.
  이 출력만으로 웹 시작·DB 연결·카카오 로그인·기한 처리가 검증된 것은 아니다.

다음 작업은 PostgreSQL 18을 보존하는 대상 DB 구성 확인과 생성, 웹 실행 계정,
비밀값 전달, DB 구조 적용, Cloud Run 테스트 배포다. 원본 Railway 데이터와
최종 사용 주소는 아직 전환하지 않았다.

## 2026-10-22 — PostgreSQL 서버·DB·웹 실행 계정 준비

사용자가 전달한 Cloud Shell 출력으로 다음을 확인했다.

- Cloud SQL `yeongcha-db`: 서울 `asia-northeast3`, `POSTGRES_18`, `state: RUNNABLE`.
  생성 요청은 Enterprise / db-f1-micro / zonal / SSD 기본 10GB와
  매일 UTC 18:00 백업으로 보냈다. 실제 설정 전체 재조회와 복구 시험은 남아 있다.
- 생성 작업 `9ab039fb-29f7-45a0-9648-91b300000042`는 오류 필드 없이 DONE.
  한국 시간 17:10:06~17:20:48에 생성됐다.
- 연결 이름은 `active-venture-449808-a5:asia-northeast3:yeongcha-db`.
- DB 서버 안에 `yeongcha` 데이터베이스 생성 성공.
- 웹 실행용 계정 `yeongcha-runtime@active-venture-449808-a5.iam.gserviceaccount.com`
  생성 성공 및 프로젝트 `roles/cloudsql.client` 연결 권한 적용 성공.

다음 실행 파일은 `scripts/gcp_prepare_database.py`다. Cloud Shell에서 DB 준비 상태,
DB 존재, 웹 실행 계정, 기존 DB 사용자와 비밀값을 조회한 뒤 다음을 수행한다.

1. 새 암호를 자동 생성해 서울 지역 Secret Manager의 `yeongcha-database-url`
   비밀값 버전 1에 접속 URL을 저장한다. 화면·Git·로컬 파일에 암호를 쓰지 않는다.
2. 해당 암호로 DB 사용자 `yeongcha_app`을 만든다. 기존 사용자 암호를 재설정하지 않는다.
3. 해당 비밀값 하나에만 웹 실행 계정의 `secretAccessor`를 부여한다.

비밀값을 먼저 저장하므로 사용자 생성이 중단돼도 다음 실행에서 같은 암호를
재사용한다. 기존 사용자가 있는데 비밀값이 없거나, 저장된 값이 다른 DB를
가리키거나, 상태/권한 조회에 실패하면 임의 수정하지 않고 중단한다.
Secret Manager 버전 1이 제거/비활성화된 경우도 실패를 숨겨 새 암호를 만들지 않는다.

로컬 검증: 외부 서비스 모델을 이용한 안전성 테스트 6개(반복 실행·중단 후 재개·
기존 사용자 보호·다른 대상 비밀값 거부·미준비/권한 거부 시 쓰기 금지·오류 출력
암호 제거) 통과. 셸 도움말과 형식 검사, 설치된 django-environ의 소켓 URL 해석도
확인했다. 실행은 `python3 ~/yeongcha/scripts/gcp_prepare_database.py`다.
이 검증은 실제 Google Cloud 실행/DB 비밀번호 인증 성공을 대신하지 않는다.
사용자 실행 결과를 확인한 후 웹 배포에서 실제 접속을 검사한다.
