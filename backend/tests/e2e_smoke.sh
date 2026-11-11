#!/usr/bin/env bash
# 端到端冒烟：原始丢字段复现 → 完整往返 → 并发冲突 → 跨模块裁决（真实 HTTP）。
set -u
BASE="${1:-http://127.0.0.1:8011}"
PASS=0
FAIL=0

check() {
  local name="$1" condition="$2"
  if eval "$condition"; then echo "  PASS  $name"; PASS=$((PASS+1)); else echo "  FAIL  $name"; FAIL=$((FAIL+1)); fi
}

echo "== 1. 原始丢字段复现（解析模板登记扩展字段） =="
PARSER_ID=$(curl -sS -X POST "$BASE/api/parser" -H 'Content-Type: application/json' \
  -d '{"values":{"模板编号":"PARS-E2E-01","目标字段名":"price","XPath表达式":"//span",
  "正则模式":"[0-9.]+","默认取值":0,"字段类型":"number","必填标识":true,"清洗函数":"trim",
  "id":9999,"status":"已启用"}}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["entry"]["id"])')
DETAIL=$(curl -sS "$BASE/api/parser/$PARSER_ID")
check "默认取值 0 原样保留" "[ \"\$(echo '$DETAIL' | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"默认取值\"])')\" = '0' ]"
check "必填标识为 true" "echo '$DETAIL' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"必填标识\"] is True'"
check "清洗函数完整读回" "echo '$DETAIL' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"清洗函数\"] == \"trim\"'"
check "服务端字段 id 未被覆盖" "echo '$DETAIL' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"id\"] != 9999'"
check "服务端字段 status 未被覆盖" "echo '$DETAIL' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"status\"] == \"草稿\"'"
check "初始版本 v1" "echo '$DETAIL' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"version\"] == 1'"

echo "== 2. 列表与导出来回读回 =="
check "列表包含扩展字段" "curl -sS '$BASE/api/parser' | python3 -c 'import json,sys; rows=json.load(sys.stdin)[\"items\"]; r=next(x for x in rows if x[\"id\"]==$PARSER_ID); assert r[\"字段类型\"]==\"number\" and r[\"清洗函数\"]==\"trim\"'"
check "导出包含扩展字段" "curl -sS '$BASE/api/parser/export' | python3 -c 'import json,sys; rows=json.load(sys.stdin)[\"items\"]; r=next(x for x in rows if x[\"id\"]==$PARSER_ID); assert r[\"正则模式\"]==\"[0-9.]+\"'"

echo "== 3. 空串默认取值与缺失的差别（格式归一） =="
FMT_ID=$(curl -sS -X POST "$BASE/api/format" -H 'Content-Type: application/json' \
  -d '{"values":{"规则编号":"FORM-E2E-01","输入字段":"raw","输出字段":"other_field",
  "转换函数":"to_decimal","默认取值":"","异常策略":"保留原值"}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["entry"]["id"])')
check "空串默认取值保留" "curl -sS '$BASE/api/format/$FMT_ID' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"默认取值\"] == \"\"'"
check "旧种子记录缺失字段补 None" "curl -sS '$BASE/api/format/1' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"引用解析版本\"] is None and d[\"legacy\"] is True'"

echo "== 4. 乐观并发：旧版本被拒且无半份修改 =="
curl -sS -X PUT "$BASE/api/parser/$PARSER_ID" -H 'Content-Type: application/json' \
  -d '{"values":{"清洗函数":"winner","expected_version":1}}' > /dev/null
STALE=$(curl -sS -o /tmp/stale.json -w '%{http_code}' -X PUT "$BASE/api/parser/$PARSER_ID" \
  -H 'Content-Type: application/json' \
  -d '{"values":{"清洗函数":"loser","默认取值":"9","expected_version":1}}')
check "旧版本提交返回 409" "[ '$STALE' = '409' ]"
check "409 带当前版本号" "python3 -c 'import json; assert json.load(open(\"/tmp/stale.json\"))[\"detail\"][\"current_version\"] == 2'"
check "拒绝后无半份修改" "curl -sS '$BASE/api/parser/$PARSER_ID' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"清洗函数\"]==\"winner\" and d[\"默认取值\"]==0 and d[\"version\"]==2'"
MISSING=$(curl -sS -o /dev/null -w '%{http_code}' -X PUT "$BASE/api/parser/$PARSER_ID" \
  -H 'Content-Type: application/json' -d '{"values":{"清洗函数":"x"}}')
check "缺 expected_version 返回 400" "[ '$MISSING' = '400' ]"

echo "== 5. 解析主配置确认 + 归一引用已确认版本 =="
# 用一条全新的模板验证确认流程，避免与前面并发用例的 v2 相互干扰
MASTER_ID=$(curl -sS -X POST "$BASE/api/parser" -H 'Content-Type: application/json' \
  -d '{"values":{"模板编号":"PARS-E2E-MASTER","目标字段名":"qty","XPath表达式":"//q",
  "默认取值":"1","清洗函数":"same_fn","必填标识":true}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["entry"]["id"])')
curl -sS -X POST "$BASE/api/parser/$MASTER_ID/actions" -H 'Content-Type: application/json' \
  -d '{"values":{"action":"提交校验"}}' > /dev/null
curl -sS -X POST "$BASE/api/parser/$MASTER_ID/actions" -H 'Content-Type: application/json' \
  -d '{"values":{"action":"启用模板"}}' > /dev/null
check "启用后 confirmed_version=1" "curl -sS '$BASE/api/parser/$MASTER_ID' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"confirmed_version\"] == 1'"
BAD_REF=$(curl -sS -o /tmp/badref.json -w '%{http_code}' -X POST "$BASE/api/format" -H 'Content-Type: application/json' \
  -d '{"values":{"规则编号":"FORM-E2E-BAD","输入字段":"raw","输出字段":"qty",
  "解析模板编号":"PARS-E2E-MASTER","引用解析版本":99}}')
check "引用不存在版本返回 409" "[ '$BAD_REF' = '409' ]"

echo "== 6. 冲突裁决：阻断确认并呈现双方来源 =="
CONF_ID=$(curl -sS -X POST "$BASE/api/format" -H 'Content-Type: application/json' \
  -d '{"values":{"规则编号":"FORM-E2E-CONF","输入字段":"raw","输出字段":"qty",
  "转换函数":"upper","默认取值":"9.99","异常策略":"置空并告警",
  "解析模板编号":"PARS-E2E-MASTER","引用解析版本":1}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["entry"]["id"])')
CONF_STATUS=$(curl -sS -o /tmp/conflict.json -w '%{http_code}' -X POST \
  "$BASE/api/format/$CONF_ID/actions" -H 'Content-Type: application/json' \
  -d '{"values":{"action":"提交校验"}}')
check "冲突确认返回 409" "[ '$CONF_STATUS' = '409' ]"
check "冲突体含双方来源" "python3 -c 'import json; d=json.load(open(\"/tmp/conflict.json\"))[\"detail\"][\"conflict\"]; assert d[\"parser_source\"][\"模板编号\"]==\"PARS-E2E-MASTER\" and d[\"format_source\"][\"规则编号\"]==\"FORM-E2E-CONF\" and len(d[\"reasons\"])>=2'"
check "阻断后仍是草稿" "curl -sS '$BASE/api/format/$CONF_ID' | python3 -c 'import json,sys; assert json.load(sys.stdin)[\"status\"] == \"草稿\"'"

echo "== 7. 版本历史可追溯 =="
# PARS-E2E-01 只被编辑过（v1→v2）未启用，两个版本都是草稿，但快照内容必须各自保留
check "版本历史含 v1/v2 快照且内容可追溯" "curl -sS '$BASE/api/parser/$PARSER_ID/revisions' | python3 -c 'import json,sys; revs=json.load(sys.stdin)[\"items\"]; assert [r[\"version\"] for r in revs]==[1,2]; assert revs[0][\"snapshot\"][\"清洗函数\"]==\"trim\" and revs[1][\"snapshot\"][\"清洗函数\"]==\"winner\" and revs[1][\"snapshot\"][\"默认取值\"]==0'"
check "主配置模板的已确认版本钉在 v1" "curl -sS '$BASE/api/parser/$MASTER_ID/revisions' | python3 -c 'import json,sys; revs=json.load(sys.stdin)[\"items\"]; assert revs[0][\"confirmed\"] is True'"

echo
echo "RESULT: PASS=$PASS FAIL=$FAIL"
exit "$FAIL"
