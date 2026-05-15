/**
 * 빌드 타임 주입 기본값.
 *
 * 커밋된 값은 빈 문자열이다. ``setup.sh`` 가 vsix 패키징 직전 감지한
 * 서버 URL (``http://<HOST_IP>:<API_PORT>``) 을 여기에 주입했다가
 * 패키징 후 원복한다. 따라서:
 *   - 서버에서 setup.sh 로 빌드한 vsix → 그 서버 URL 이 기본값
 *     (대시보드에서 받아 설치하면 바로 연결됨)
 *   - 수동 `npm run package` / git clone 빌드 → 빈 문자열
 *     (welcome 화면에서 사용자가 직접 입력)
 *
 * 이 파일은 트래킹되지만 setup.sh 가 로컬에서만 잠깐 덮어쓰고 원복하므로
 * 저장소 상태는 항상 깨끗하게 유지된다.
 */
export const BUILD_DEFAULT_BASE_URL = '';
