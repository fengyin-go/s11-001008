"""扩展字段登记/往返/兼容/并发/冲突的端到端验证。

覆盖内容：
1. 原始丢字段问题复现：登记 payload 里的扩展字段必须完整落库并可读回；
2. 默认取值为 0、空串与“未提供/旧数据缺字段”的差别；
3. 旧记录（无版本、无扩展字段）读取时的兼容补齐；
4. 服务端字段（id/status/pending/abnormal/版本链）不可被用户覆盖；
5. 乐观版本控制：旧版本并发提交被拒，且拒绝后不留半份修改；
6. 解析模板为主配置：格式归一只能引用已确认的字段版本，冲突阻断并给出双方来源；
7. 登记入口、列表、详情、版本历史都能完整读回扩展字段。
"""
from __future__ import annotations

from typing import Any

from app.services.format import MODULE as FORMAT_MODULE
from app.services.parser import MODULE as PARSER_MODULE
from app.store import store

PARSER_PAYLOAD: dict[str, Any] = {
    "模板编号": "PARS-EXT-01",
    "目标字段名": "price",
    "XPath表达式": "//span[@class='price']/text()",
    "正则模式": r"[\d.]+",
    "默认取值": "0.00",
    "字段类型": "number",
    "必填标识": True,
    "清洗函数": "trim",
}

FORMAT_PAYLOAD: dict[str, Any] = {
    "规则编号": "FORM-EXT-01",
    "输入字段": "raw_price",
    "输出字段": "price",
    "转换函数": "to_decimal",
    "默认取值": "",
    "异常策略": "置空并告警",
    "格式化模板": "0.00",
    "校验正则": r"^\d+(\.\d+)?$",
}


# ---------------------------------------------------------------------------
# 1. 原始丢字段复现：解析模板与转换规则的扩展字段都必须完整往返
# ---------------------------------------------------------------------------

def test_parser_create_persists_all_extension_fields(client) -> None:
    response = client.post("/api/parser", json={"values": PARSER_PAYLOAD})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    entry = body["entry"]

    for field, value in PARSER_PAYLOAD.items():
        assert entry[field] == value, f"登记响应里字段 {field} 被丢弃"

    entry_id = entry["id"]
    detail = client.get(f"/api/parser/{entry_id}").json()
    for field, value in PARSER_PAYLOAD.items():
        assert detail[field] == value, f"详情接口字段 {field} 没有完整读回"

    listed = client.get("/api/parser").json()["items"]
    row = next(item for item in listed if item["id"] == entry_id)
    for field, value in PARSER_PAYLOAD.items():
        assert row[field] == value, f"列表接口字段 {field} 没有完整读回"

    exported = client.get("/api/parser/export").json()["items"]
    exported_row = next(item for item in exported if item["id"] == entry_id)
    for field, value in PARSER_PAYLOAD.items():
        assert exported_row[field] == value


def test_format_create_persists_all_extension_fields(client) -> None:
    response = client.post("/api/format", json={"values": FORMAT_PAYLOAD})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    entry = body["entry"]

    for field, value in FORMAT_PAYLOAD.items():
        assert entry[field] == value, f"登记响应里字段 {field} 被丢弃"

    entry_id = entry["id"]
    detail = client.get(f"/api/format/{entry_id}").json()
    for field, value in FORMAT_PAYLOAD.items():
        assert detail[field] == value

    listed = client.get("/api/format").json()["items"]
    row = next(item for item in listed if item["id"] == entry_id)
    for field, value in FORMAT_PAYLOAD.items():
        assert row[field] == value


# ---------------------------------------------------------------------------
# 2. 默认取值为 0 / 空串 与 旧数据缺字段的差别
# ---------------------------------------------------------------------------

def test_zero_and_empty_string_defaults_are_preserved(client) -> None:
    payload = dict(PARSER_PAYLOAD, 模板编号="PARS-ZERO-01", 默认取值=0)
    created = client.post("/api/parser", json={"values": payload}).json()["entry"]
    assert created["默认取值"] == 0
    assert client.get(f"/api/parser/{created['id']}").json()["默认取值"] == 0

    format_payload = dict(FORMAT_PAYLOAD, 规则编号="FORM-EMPTY-01", 默认取值="")
    created_format = client.post("/api/format", json={"values": format_payload}).json()["entry"]
    assert created_format["默认取值"] == ""
    assert client.get(f"/api/format/{created_format['id']}").json()["默认取值"] == ""


def test_required_fields_accept_zero_and_reject_blank(client) -> None:
    # 数字 0 是合法的必填值，不能被“真值判断”误判为缺失
    payload = dict(PARSER_PAYLOAD, 模板编号="PARS-ZERO-REQ", 目标字段名="count", XPath表达式=0)
    ok = client.post("/api/parser", json={"values": payload})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    # 空白串仍按缺失处理
    bad = client.post(
        "/api/parser",
        json={"values": dict(PARSER_PAYLOAD, 模板编号="PARS-BLANK", XPath表达式="   ")},
    )
    assert bad.json()["ok"] is False
    assert "XPath表达式" in bad.json()["message"]


def test_legacy_rows_missing_extension_fields_are_backfilled_without_mutation(client) -> None:
    # 手工塞一条“旧格式”记录：只有三个必填字段，没有版本链，没有扩展字段。
    legacy_row = {
        "id": 9001,
        "status": "草稿",
        "pending": True,
        "abnormal": False,
        "模板编号": "PARS-LEGACY-01",
        "目标字段名": "legacy_price",
        "XPath表达式": "//old",
    }
    store.rows(PARSER_MODULE).append(legacy_row)

    detail = client.get("/api/parser/9001").json()
    # 缺失的扩展字段按声明的默认值补齐
    assert detail["正则模式"] == ""
    assert detail["默认取值"] is None
    assert detail["字段类型"] == "string"
    assert detail["必填标识"] is False
    assert detail["清洗函数"] == ""
    assert detail["version"] == 1
    assert detail["legacy"] is True
    # 补齐只发生在视图层，仓库里的旧记录保持原样，便于追溯
    assert "version" not in legacy_row
    assert "默认取值" not in legacy_row

    listed = client.get("/api/parser").json()["items"]
    listed_row = next(item for item in listed if item["id"] == 9001)
    assert listed_row["默认取值"] is None
    assert listed_row["legacy"] is True

    # 旧记录可以正常编辑，编辑后升级为受版本治理的新记录
    updated = client.put(
        "/api/parser/9001",
        json={"values": {"默认取值": "N/A", "expected_version": 1}},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["entry"]["version"] == 2
    assert body["entry"]["默认取值"] == "N/A"
    assert body["entry"]["legacy"] is False


def test_format_legacy_rows_are_backfilled(client) -> None:
    legacy_row = {
        "id": 9101,
        "status": "草稿",
        "pending": True,
        "abnormal": False,
        "规则编号": "FORM-LEGACY-01",
        "输入字段": "in",
        "输出字段": "out",
    }
    store.rows(FORMAT_MODULE).append(legacy_row)

    detail = client.get("/api/format/9101").json()
    assert detail["转换函数"] == ""
    assert detail["默认取值"] is None
    assert detail["异常策略"] == "保留原值"
    assert detail["格式化模板"] == ""
    assert detail["校验正则"] == ""
    assert detail["version"] == 1
    assert detail["legacy"] is True


def test_legacy_first_edit_keeps_v1_snapshot_in_history(client) -> None:
    legacy_row = {
        "id": 9002,
        "status": "草稿",
        "pending": True,
        "abnormal": False,
        "模板编号": "PARS-LEGACY-02",
        "目标字段名": "legacy_title",
        "XPath表达式": "//title",
        "清洗函数": "old_fn",
    }
    store.rows(PARSER_MODULE).append(legacy_row)

    updated = client.put(
        "/api/parser/9002",
        json={"values": {"清洗函数": "new_fn", "expected_version": 1}},
    ).json()["entry"]
    assert updated["version"] == 2
    assert updated["legacy"] is False

    revisions = client.get("/api/parser/9002/revisions").json()["items"]
    assert [rev["version"] for rev in revisions] == [1, 2]
    assert revisions[0]["note"] == "旧记录补齐"
    assert revisions[0]["snapshot"]["清洗函数"] == "old_fn"
    assert revisions[1]["snapshot"]["清洗函数"] == "new_fn"


def test_legacy_enabled_parser_counts_as_confirmed_v1_for_format(client) -> None:
    # 旧格式、且已启用的解析模板（无版本链）应视同 v1 已确认，可被归一引用
    legacy_enabled = {
        "id": 9003,
        "status": "已启用",
        "pending": False,
        "abnormal": False,
        "模板编号": "PARS-LEGACY-ON",
        "目标字段名": "legacy_aligned",
        "XPath表达式": "//v",
        "默认取值": "0",
        "清洗函数": "same_fn",
        "必填标识": False,
    }
    store.rows(PARSER_MODULE).append(legacy_enabled)

    payload = dict(
        FORMAT_PAYLOAD,
        规则编号="FORM-LEGACY-REF",
        输出字段="legacy_aligned",
        默认取值="0",
        转换函数="same_fn",
        解析模板编号="PARS-LEGACY-ON",
        引用解析版本=1,
    )
    created = client.post("/api/format", json={"values": payload})
    assert created.status_code == 200
    format_id = created.json()["entry"]["id"]

    # 隐式裁决路径同样能识别旧记录：对齐的规则可正常提交校验
    ok = client.post(
        f"/api/format/{format_id}/actions", json={"values": {"action": "提交校验"}}
    )
    assert ok.status_code == 200

    # 旧的未启用模板仍然不能被引用
    legacy_draft = dict(legacy_enabled, id=9004, 模板编号="PARS-LEGACY-OFF", status="草稿", pending=True)
    store.rows(PARSER_MODULE).append(legacy_draft)
    blocked = client.post(
        "/api/format",
        json={"values": dict(payload, 规则编号="FORM-LEGACY-REF-2", 解析模板编号="PARS-LEGACY-OFF")},
    )
    assert blocked.status_code == 409
    assert "未启用" in blocked.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# 3. 服务端维护字段不可被用户覆盖
# ---------------------------------------------------------------------------

def test_server_managed_fields_are_protected_on_create(client) -> None:
    payload = dict(
        PARSER_PAYLOAD,
        模板编号="PARS-PROT-01",
        id=9999,
        status="已启用",
        pending=False,
        abnormal=True,
        version=77,
        confirmed_version=77,
        revisions=[],
        legacy=True,
    )
    entry = client.post("/api/parser", json={"values": payload}).json()["entry"]
    assert entry["id"] != 9999
    assert entry["status"] == "草稿"
    assert entry["pending"] is True
    assert entry["abnormal"] is False
    assert entry["version"] == 1
    assert entry["confirmed_version"] is None
    assert entry["legacy"] is False
    assert isinstance(entry["revisions"], list) and len(entry["revisions"]) == 1


def test_server_managed_fields_are_protected_on_update(client) -> None:
    entry = client.post("/api/parser", json={"values": dict(PARSER_PAYLOAD, 模板编号="PARS-PROT-02")}).json()["entry"]
    response = client.put(
        f"/api/parser/{entry['id']}",
        json={"values": {
            "清洗函数": "upper",
            "id": 12345,
            "status": "已停用",
            "pending": False,
            "abnormal": True,
            "version": 99,
            "confirmed_version": 99,
            "revisions": [],
            "expected_version": entry["version"],
        }},
    )
    assert response.status_code == 200
    updated = response.json()["entry"]
    assert updated["id"] == entry["id"]
    assert updated["status"] == "草稿"
    assert updated["pending"] is True
    assert updated["abnormal"] is False
    assert updated["version"] == 2
    assert updated["confirmed_version"] is None
    assert len(updated["revisions"]) == 2
    assert updated["清洗函数"] == "upper"


def test_update_requires_expected_version(client) -> None:
    entry = client.post("/api/parser", json={"values": PARSER_PAYLOAD}).json()["entry"]
    response = client.put(f"/api/parser/{entry['id']}", json={"values": {"清洗函数": "trim2"}})
    assert response.status_code == 400
    assert "expected_version" in response.json()["detail"]
    # 拒绝后数据原样不动，没有半份修改
    detail = client.get(f"/api/parser/{entry['id']}").json()
    assert detail["清洗函数"] == PARSER_PAYLOAD["清洗函数"]
    assert detail["version"] == 1


# ---------------------------------------------------------------------------
# 4. 乐观并发：旧版本提交被拒绝，两个模块都不留半份修改
# ---------------------------------------------------------------------------

def test_stale_update_is_rejected_and_leaves_no_partial_change(client) -> None:
    entry = client.post("/api/parser", json={"values": PARSER_PAYLOAD}).json()["entry"]
    entry_id = entry["id"]

    # 第一笔提交成功，版本推进到 2
    ok = client.put(
        f"/api/parser/{entry_id}",
        json={"values": {"清洗函数": "first_win", "expected_version": 1}},
    )
    assert ok.status_code == 200
    assert ok.json()["entry"]["version"] == 2

    # 第二笔仍拿着版本 1，必须拒绝
    stale = client.put(
        f"/api/parser/{entry_id}",
        json={"values": {"清洗函数": "stale_loser", "默认取值": "9", "expected_version": 1}},
    )
    assert stale.status_code == 409
    stale_body = stale.json()["detail"]
    assert stale_body["ok"] is False
    assert stale_body["current_version"] == 2

    detail = client.get(f"/api/parser/{entry_id}").json()
    assert detail["version"] == 2
    assert detail["清洗函数"] == "first_win"
    # 同一请求里带的其他字段也不能落库
    assert detail["默认取值"] == PARSER_PAYLOAD["默认取值"]


def test_stale_format_update_is_rejected_and_atomic(client) -> None:
    entry = client.post("/api/format", json={"values": FORMAT_PAYLOAD}).json()["entry"]
    entry_id = entry["id"]

    client.put(
        f"/api/format/{entry_id}",
        json={"values": {"转换函数": "winner", "expected_version": 1}},
    )
    stale = client.put(
        f"/api/format/{entry_id}",
        json={"values": {"转换函数": "loser", "异常策略": "丢弃", "expected_version": 1}},
    )
    assert stale.status_code == 409
    detail = client.get(f"/api/format/{entry_id}").json()
    assert detail["version"] == 2
    assert detail["转换函数"] == "winner"
    assert detail["异常策略"] == FORMAT_PAYLOAD["异常策略"]


def test_stale_action_is_rejected(client) -> None:
    entry = client.post("/api/parser", json={"values": PARSER_PAYLOAD}).json()["entry"]
    entry_id = entry["id"]
    client.put(
        f"/api/parser/{entry_id}",
        json={"values": {"清洗函数": "v2", "expected_version": 1}},
    )
    stale = client.post(
        f"/api/parser/{entry_id}/actions",
        json={"values": {"action": "提交校验", "expected_version": 1}},
    )
    assert stale.status_code == 409
    assert client.get(f"/api/parser/{entry_id}").json()["status"] == "草稿"


# ---------------------------------------------------------------------------
# 5. 解析模板为主配置：版本可追溯，归一只能引用已确认的字段版本
# ---------------------------------------------------------------------------

def _enable_parser(client, payload: dict[str, Any]) -> dict[str, Any]:
    entry = client.post("/api/parser", json={"values": payload}).json()["entry"]
    client.post(f"/api/parser/{entry['id']}/actions", json={"values": {"action": "提交校验"}})
    enabled = client.post(
        f"/api/parser/{entry['id']}/actions", json={"values": {"action": "启用模板"}}
    ).json()["entry"]
    return enabled


def test_parser_revisions_keep_full_history(client) -> None:
    enabled = _enable_parser(client, dict(PARSER_PAYLOAD, 模板编号="PARS-HIST-01"))
    entry_id = enabled["id"]
    assert enabled["version"] == 1
    assert enabled["confirmed_version"] == 1

    client.put(
        f"/api/parser/{entry_id}",
        json={"values": {"清洗函数": "lower", "expected_version": 1}},
    )
    detail = client.get(f"/api/parser/{entry_id}").json()
    assert detail["version"] == 2
    # 已确认版本仍指向 v1，新草稿 v2 尚未确认
    assert detail["confirmed_version"] == 1

    revisions = client.get(f"/api/parser/{entry_id}/revisions").json()["items"]
    assert [rev["version"] for rev in revisions] == [1, 2]
    assert revisions[0]["snapshot"]["清洗函数"] == "trim"
    assert revisions[1]["snapshot"]["清洗函数"] == "lower"
    assert revisions[0]["confirmed"] is True
    assert revisions[1]["confirmed"] is False


def test_format_must_reference_existing_confirmed_parser_version(client) -> None:
    # 未启用的解析模板没有已确认版本，引用应被阻断
    draft = client.post("/api/parser", json={"values": dict(PARSER_PAYLOAD, 模板编号="PARS-REF-DRAFT")}).json()["entry"]
    payload = dict(
        FORMAT_PAYLOAD,
        规则编号="FORM-REF-01",
        解析模板编号=draft["模板编号"],
        引用解析版本=draft["version"],
    )
    response = client.post("/api/format", json={"values": payload})
    assert response.status_code == 409
    assert "已确认" in response.json()["detail"]["message"]

    # 启用后 v1 成为已确认版本，引用 v1 可以登记
    enabled = _enable_parser(client, dict(PARSER_PAYLOAD, 模板编号="PARS-REF-OK"))
    ok_payload = dict(
        FORMAT_PAYLOAD,
        规则编号="FORM-REF-02",
        解析模板编号=enabled["模板编号"],
        引用解析版本=1,
    )
    ok = client.post("/api/format", json={"values": ok_payload})
    assert ok.status_code == 200
    assert ok.json()["entry"]["引用解析版本"] == 1

    # 不存在的版本号同样阻断
    bad = client.post(
        "/api/format",
        json={"values": dict(ok_payload, 规则编号="FORM-REF-03", 引用解析版本=99)},
    )
    assert bad.status_code == 409
    assert "不存在" in bad.json()["detail"]["message"]


def test_conflicting_format_confirmation_is_blocked_with_both_sources(client) -> None:
    parser_entry = _enable_parser(
        client,
        dict(PARSER_PAYLOAD, 模板编号="PARS-CONF-01", 默认取值="0.00", 清洗函数="trim", 必填标识=True),
    )
    # 归一对同一目标字段给出不同的默认取值与清洗/转换函数，且异常策略允许置空
    format_payload = dict(
        FORMAT_PAYLOAD,
        规则编号="FORM-CONF-01",
        输出字段=parser_entry["目标字段名"],
        默认取值="9.99",
        转换函数="upper",
        异常策略="置空并告警",
        解析模板编号=parser_entry["模板编号"],
        引用解析版本=1,
    )
    # 草稿允许保存，但提交校验时必须阻断并呈现双方来源
    draft = client.post("/api/format", json={"values": format_payload})
    assert draft.status_code == 200
    format_id = draft.json()["entry"]["id"]

    blocked = client.post(
        f"/api/format/{format_id}/actions", json={"values": {"action": "提交校验"}}
    )
    assert blocked.status_code == 409
    body = blocked.json()["detail"]
    assert body["ok"] is False
    conflict = body["conflict"]
    assert conflict["target_field"] == "price"
    assert conflict["parser_source"]["模板编号"] == "PARS-CONF-01"
    assert conflict["parser_source"]["version"] == 1
    assert conflict["format_source"]["规则编号"] == "FORM-CONF-01"
    reasons = "；".join(conflict["reasons"])
    assert "默认取值" in reasons
    assert "函数" in reasons or "清洗" in reasons
    assert "必填" in reasons

    # 阻断后规则仍停留在草稿，没有产生任何状态半份修改
    detail = client.get(f"/api/format/{format_id}").json()
    assert detail["status"] == "草稿"
    assert detail["pending"] is True


def test_aligned_format_can_be_confirmed_and_parser_remains_master(client) -> None:
    parser_entry = _enable_parser(
        client,
        dict(PARSER_PAYLOAD, 模板编号="PARS-ALIGN-01", 默认取值="0.00", 清洗函数="same_fn"),
    )
    format_payload = dict(
        FORMAT_PAYLOAD,
        规则编号="FORM-ALIGN-01",
        输出字段=parser_entry["目标字段名"],
        默认取值="0.00",
        转换函数="same_fn",
        异常策略="保留原值",
        解析模板编号=parser_entry["模板编号"],
        引用解析版本=1,
    )
    draft = client.post("/api/format", json={"values": format_payload}).json()["entry"]
    confirmed = client.post(
        f"/api/format/{draft['id']}/actions", json={"values": {"action": "提交校验"}}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["entry"]["status"] == "待校验"

    # 解析模板再出新版本：归一丝毫不受影响，仍钉在已确认的 v1 上，不会被覆盖
    client.put(
        f"/api/parser/{parser_entry['id']}",
        json={"values": {"清洗函数": "changed_fn", "expected_version": 1}},
    )
    format_detail = client.get(f"/api/format/{draft['id']}").json()
    assert format_detail["引用解析版本"] == 1
    assert format_detail["转换函数"] == "same_fn"

    revisions = client.get(f"/api/parser/{parser_entry['id']}/revisions").json()["items"]
    assert len(revisions) == 2
    assert revisions[0]["snapshot"]["清洗函数"] == "same_fn"


def test_format_update_validates_reference_and_can_remove_it(client) -> None:
    enabled = _enable_parser(client, dict(PARSER_PAYLOAD, 模板编号="PARS-UPD-01"))
    payload = dict(
        FORMAT_PAYLOAD,
        规则编号="FORM-UPD-01",
        解析模板编号=enabled["模板编号"],
        引用解析版本=1,
    )
    entry = client.post("/api/format", json={"values": payload}).json()["entry"]
    assert entry["引用解析版本"] == 1

    # 编辑时改成未确认的版本必须拒绝，且不留半份修改
    stale_ref = client.put(
        f"/api/format/{entry['id']}",
        json={"values": {"转换函数": "bad_ref", "引用解析版本": 5, "expected_version": 1}},
    )
    assert stale_ref.status_code == 409
    detail = client.get(f"/api/format/{entry['id']}").json()
    assert detail["转换函数"] == FORMAT_PAYLOAD["转换函数"]
    assert detail["引用解析版本"] == 1
    assert detail["version"] == 1

    # 可以正常编辑已引用规则的其他扩展字段
    ok = client.put(
        f"/api/format/{entry['id']}",
        json={"values": {"格式化模板": "0.00", "expected_version": 1}},
    )
    assert ok.status_code == 200
    assert ok.json()["entry"]["格式化模板"] == "0.00"
    assert ok.json()["entry"]["version"] == 2

    # 可以解除引用：两个引用字段显式置空后不再参与校验
    removed = client.put(
        f"/api/format/{entry['id']}",
        json={"values": {"解析模板编号": None, "引用解析版本": None, "expected_version": 2}},
    )
    assert removed.status_code == 200
    body = removed.json()["entry"]
    assert body["解析模板编号"] is None
    assert body["引用解析版本"] is None


def test_unknown_fields_in_payload_are_ignored_not_persisted(client) -> None:
    payload = dict(PARSER_PAYLOAD, 模板编号="PARS-STRANGER", 自定义后门字段="x", hack=True)
    entry = client.post("/api/parser", json={"values": payload}).json()["entry"]
    assert "自定义后门字段" not in entry
    assert "hack" not in entry
    assert "自定义后门字段" not in client.get(f"/api/parser/{entry['id']}").json()
