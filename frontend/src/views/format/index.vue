<template>
  <section class="page" data-module="format">
    <header class="page-head">
      <div>
        <h2>格式归一管理</h2>
        <p class="page-desc">维护格式转换规则的输入、执行约束与结果记录。归一只能引用解析模板已确认的字段版本；与主配置冲突时确认会被阻断并展示双方来源。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreateWithRefs">登记转换规则</button>
        <button class="btn" type="button" @click="exportRows">导出格式归一清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <ConflictPanel :conflict="editor.state.conflict" />

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>解析引用</th>
          <th>版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ displayValue(row[column]) }}</td>
          <td>
            <span v-if="row.解析模板编号" class="version-text">
              {{ row.解析模板编号 }} · v{{ row.引用解析版本 }}
            </span>
            <span v-else>—</span>
          </td>
          <td>
            <span class="version-text">v{{ row.version ?? 1 }}</span>
            <span v-if="row.legacy" class="tag legacy">旧</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button class="link" type="button" @click="openEditWithRefs(row)">编辑</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">暂无格式归一数据，可先登记转换规则</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条格式归一记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <FieldEditorModal
      module-title="转换规则"
      :title="editor.title.value"
      :fields="fieldDefs"
      :state="editor.state"
      :message="editor.message.value"
      :message-kind="editor.messageKind.value"
      @close="editor.close()"
      @submit="onSubmit"
    />

    <EntryDetailDrawer
      :open="detailOpen"
      module-title="转换规则"
      code-field="规则编号"
      :fields="fieldDefs"
      :detail="detail"
      :revisions="revisions"
      @close="detailOpen = false"
    />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import ConflictPanel from '@/components/ConflictPanel.vue'
import EntryDetailDrawer from '@/components/EntryDetailDrawer.vue'
import FieldEditorModal from '@/components/FieldEditorModal.vue'
import { request } from '@/api/client'
import { useFieldEditor, type FieldDef } from '@/composables/useFieldEditor'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/format'
const columns = ["规则编号", "输入字段", "输出字段", "转换函数", "默认取值", "异常策略", "格式化模板", "校验正则"]
const actions = ["提交校验", "启用规则", "停用规则"]

const fieldDefs: FieldDef[] = [
  { key: '规则编号', label: '规则编号', required: true, placeholder: '如 FORM-0004', defaultValue: '' },
  { key: '输入字段', label: '输入字段', required: true, placeholder: '如 raw_price', defaultValue: '' },
  { key: '输出字段', label: '输出字段', required: true, placeholder: '需与解析模板目标字段一致', defaultValue: '' },
  { key: '转换函数', label: '转换函数', placeholder: '如 to_decimal；需与解析清洗函数口径一致', defaultValue: '' },
  { key: '默认取值', label: '默认取值', placeholder: '允许 0 或空串；需与解析模板一致', defaultValue: null },
  { key: '异常策略', label: '异常策略', type: 'select', options: ['保留原值', '置空并告警', '整条丢弃'], defaultValue: '保留原值' },
  { key: '格式化模板', label: '格式化模板', placeholder: '如 0.00', defaultValue: '' },
  { key: '校验正则', label: '校验正则', placeholder: '留空表示不做格式校验', defaultValue: '' },
  { key: '解析模板编号', label: '引用解析模板（主配置，仅可选择已确认版本）', type: 'select', options: ['__none__'], defaultValue: '__none__' },
  { key: '引用解析版本', label: '引用解析版本号（随上方引用自动派生）', type: 'number', readonly: true, defaultValue: null },
]

const stats = ref([
  { label: '转换规则总数', value: 0 },
  { label: '已启用规则', value: 0 },
  { label: '待校验规则', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const confirmedParserOptions = ref<Array<{ code: string; version: number; label: string }>>([])

const editor = useFieldEditor(ENDPOINT, fieldDefs)

const detailOpen = ref(false)
const detail = ref<Record<string, unknown>>({})
const revisions = ref<Array<{ version: number; confirmed: boolean; note: string; snapshot: Record<string, unknown> }>>([])

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (value === '') return '（空串）'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

/** 拉取所有已确认过的解析模板版本，供登记/编辑时引用；未确认版本不会出现在选项里。 */
async function loadConfirmedParserOptions() {
  const response = await request('/api/parser?size=200')
  if (!response.ok) {
    confirmedParserOptions.value = []
    return
  }
  const payload = await response.json()
  const options: Array<{ code: string; version: number; label: string }> = []
  for (const item of payload.items ?? []) {
    const confirmed = Number(item.confirmed_version ?? 0)
    if (confirmed > 0) {
      options.push({
        code: String(item['模板编号']),
        version: confirmed,
        label: `${item['模板编号']}（字段 ${item['目标字段名']} · 已确认 v${confirmed}）`,
      })
    }
  }
  confirmedParserOptions.value = options
  // 选项值为「模板编号@版本」，保证模板与确认版本成对选择、不会错配
  const refField = fieldDefs.find((field) => field.key === '解析模板编号')
  if (refField) {
    const values = options.map((item) => `${item.code}@${item.version}`)
    refField.options = ['__none__', ...values]
    refField.optionLabels = {
      __none__: '不引用解析模板（仅按输出字段隐式裁决）',
      ...Object.fromEntries(values.map((value) => [value, optionLabel(value)])),
    }
  }
}

function optionLabel(value: unknown): string {
  if (!value || value === '__none__') return '不引用解析模板（仅按输出字段隐式裁决）'
  const matched = confirmedParserOptions.value.find(
    (item) => `${item.code}@${item.version}` === String(value),
  )
  return matched?.label ?? String(value)
}

async function openCreateWithRefs() {
  await loadConfirmedParserOptions()
  editor.openCreate()
  editor.state.form['解析模板编号'] = '__none__'
}

async function openEditWithRefs(row: Row) {
  await loadConfirmedParserOptions()
  await editor.openEdit(row)
  const code = editor.state.form['解析模板编号']
  const version = editor.state.form['引用解析版本']
  editor.state.form['解析模板编号'] = code && version ? `${code}@${version}` : '__none__'
  // 版本号由组合选项派生，编辑弹窗里只读展示，避免手工错配
  editor.state.form['引用解析版本'] = version ?? null
}

async function onSubmit() {
  // 把组合选项拆回服务端需要的两个字段；选择“不引用”时两个字段都置空
  const combined = String(editor.state.form['解析模板编号'] ?? '__none__')
  if (combined === '__none__' || !combined) {
    editor.state.form['解析模板编号'] = null
    editor.state.form['引用解析版本'] = null
  } else {
    const [code, versionText] = combined.split('@')
    editor.state.form['解析模板编号'] = code
    editor.state.form['引用解析版本'] = Number(versionText)
  }
  const ok = await editor.save()
  if (ok) {
    await reload()
  } else {
    // 保存失败（如冲突）后恢复组合展示值，便于用户调整后重试
    const code = editor.state.form['解析模板编号']
    const version = editor.state.form['引用解析版本']
    editor.state.form['解析模板编号'] = code && version ? `${code}@${version}` : '__none__'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const [detailResponse, revisionResponse] = await Promise.all([
      request(`${ENDPOINT}/${row.id}`),
      request(`${ENDPOINT}/${row.id}/revisions`),
    ])
    if (!detailResponse.ok || !revisionResponse.ok) {
      throw new Error('转换规则详情读取失败')
    }
    detail.value = await detailResponse.json()
    revisions.value = (await revisionResponse.json()).items ?? []
    detailOpen.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转换规则详情读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, expected_version: row.version ?? 1 } }),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      const detailPayload = body.detail
      if (detailPayload?.conflict) {
        editor.state.conflict = detailPayload.conflict
      }
      const detailText = typeof detailPayload === 'string' ? detailPayload : detailPayload?.message
      throw new Error(detailText ?? '格式归一动作未生效，请刷新后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '格式归一操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('转换规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value[0].value = total.value
    stats.value[1].value = rows.value.filter((row) => row.status === '已启用').length
    stats.value[2].value = rows.value.filter((row) => row.status === '待校验').length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '格式归一列表读取失败'
  }
}

onMounted(reload)
</script>
