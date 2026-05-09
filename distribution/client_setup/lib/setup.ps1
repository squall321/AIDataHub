# =============================================================================
# 첫 셋업 — config 검증 + 연결 테스트 + 가이드 다운로드 + discover 캐시
# =============================================================================
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host " AI Data Hub 클라이언트 셋업" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# 1. config 검증
$cfg = Get-Config
Write-Host "[1/4] config.ini 검증 OK" -ForegroundColor Green
Write-Host "      base_url : $($cfg.server.base_url)"
$keyMask = $cfg.server.api_key.Substring(0, [Math]::Min(8, $cfg.server.api_key.Length))
Write-Host "      api_key  : $keyMask..."
Write-Host "      size     : $($cfg.model.size)"

# 2. 연결 테스트 (health)
Write-Host ""
Write-Host "[2/4] 서버 연결 테스트..."
$health = Invoke-Aidh -Path "/api/system/health"
Write-Host "      OK: $($health | ConvertTo-Json -Compress)" -ForegroundColor Green

# 3. 모델 사이즈 가이드 다운로드
$size = $cfg.model.size.ToLower()
$validSizes = @("tiny", "small", "medium", "large")
if ($validSizes -notcontains $size) {
    Write-Host "ERROR: model.size 는 tiny/small/medium/large 중 하나여야 한다 (현재: $size)" -ForegroundColor Red
    exit 1
}

$guideDir = Join-Path $script:ROOT_DIR $cfg.output.guide_dir
New-Item -ItemType Directory -Force -Path $guideDir | Out-Null
$guideFile = Join-Path $guideDir ("AGENT_API_GUIDE_" + $size.ToUpper() + ".md")

Write-Host ""
Write-Host "[3/4] 가이드 다운로드 (size=$size)..."
Invoke-Aidh -Path "/api/docs/agent-guide?size=$size" -OutFile $guideFile | Out-Null
$guideSize = (Get-Item $guideFile).Length
Write-Host "      저장: $guideFile ($([math]::Round($guideSize/1KB, 1)) KB)" -ForegroundColor Green

# 4. discover 카탈로그 캐시
Write-Host ""
Write-Host "[4/4] 카탈로그 캐시 (/api/discover)..."
$discover = Invoke-Aidh -Path "/api/discover"
$catalogFile = Join-Path $guideDir "discover_cache.json"
$discover | ConvertTo-Json -Depth 10 | Out-File $catalogFile -Encoding UTF8
Write-Host "      저장: $catalogFile" -ForegroundColor Green

Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host " 셋업 완료" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "사용 명령어 (이 폴더에서):" -ForegroundColor Cyan
Write-Host "  ask.bat        ""AI 도입 현황은?""              ; 자연어 질의" -ForegroundColor Gray
Write-Host "  search.bat     semantic ""stress strain""      ; 의미 검색" -ForegroundColor Gray
Write-Host "  search.bat     fts      ""낙하 시뮬레이션""    ; 전문 검색" -ForegroundColor Gray
Write-Host "  search.bat     tag      ""IGA,NURBS""           ; 태그 필터" -ForegroundColor Gray
Write-Host "  get.bat        DOC-HE-CAE-2026-001001         ; 레코드 조회" -ForegroundColor Gray
Write-Host "  ingest.bat     ""C:\path\to\report.docx""       ; 자료 적재" -ForegroundColor Gray
Write-Host "  show_guide.bat                                 ; 자기 모델 가이드 보기" -ForegroundColor Gray
Write-Host "  related.bat    DOC-HE-CAE-2026-001001         ; 비슷한 레코드" -ForegroundColor Gray
Write-Host ""
