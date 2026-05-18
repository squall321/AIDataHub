#!/usr/bin/env node
/**
 * 웹뷰 JS 문법 검증 — tsc/vsce 가 못 잡는 회귀 가드.
 *
 * html.ts 는 거대한 템플릿 리터럴 안에 웹뷰용 JS 를 문자열로 담는다.
 * 그 문자열 내부의 문법 오류(예: 템플릿 리터럴에서 `\n` 이 실제 개행으로
 * 변환돼 따옴표 문자열이 깨짐 — v0.13.0 한글 아포스트로피, codex TOML
 * 사건)는 tsc 도 vsce 도 못 잡고, 런타임에 웹뷰가 통째로 blank 가 된다.
 *
 * 빌드(out/) 의 renderHtml() 결과에서 <script> 본문을 뽑아 new Function
 * 으로 파싱만 시도한다(실행 X). 실패하면 비0 종료 → 패키징 차단.
 */
const path = require('path');
const htmlMod = path.resolve(__dirname, '..', 'out', 'webview', 'html.js');

let renderHtml;
try {
  ({ renderHtml } = require(htmlMod));
} catch (e) {
  console.error('[verify-webview] out/webview/html.js 로드 실패 — npm run build 먼저:', e.message);
  process.exit(2);
}

const html = renderHtml();
const m = html.match(/<script nonce="[^"]*">([\s\S]*?)<\/script>/);
if (!m) {
  console.error('[verify-webview] <script> 블록을 찾지 못함 (html 구조 변경?)');
  process.exit(2);
}
const js = m[1];

try {
  // 실행하지 않고 파싱만 — 문법 오류면 여기서 throw.
  new Function(js); // eslint-disable-line no-new-func
  console.log(`[verify-webview] OK — 웹뷰 JS ${js.length} bytes 문법 정상`);
} catch (e) {
  console.error('[verify-webview] ✗ 웹뷰 JS 문법 오류 — 런타임 blank 원인:');
  console.error('  ' + e.message);
  const ln = (e.stack || '').match(/<anonymous>:(\d+)/);
  if (ln) {
    const lines = js.split('\n');
    const n = parseInt(ln[1], 10);
    for (let i = Math.max(0, n - 2); i < Math.min(lines.length, n + 1); i++) {
      console.error(`  ${i + 1}: ${lines[i]}`);
    }
  }
  process.exit(1);
}
