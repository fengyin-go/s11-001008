<template>
  <section class="page" data-module="parser">
    <header class="page-head">
      <div>
        <h2>内容解析器管理</h2>
        <p class="page-desc">维护内容解析模板的输入、执行约束与结果记录，为内容解析提供可验证的数据工程流程。解析模板是字段配置的主配置，扩展字段完整登记、按版本可追溯。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="editor.openCreate()">登记解析模板</button>
        <button class="btn" type="button" @click="exportRows">导出内容解析器清单</button>
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

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ displayValue(row[column]) }}</td>
          <td>
            <span class="version-text">v{{ row.version ?? 1 }}</span>
            <span v-if="row.legacy" class="tag legacy">旧</span>
            <span v-if="row.confirmed_version" class="tag ok">已确认 v{{ row.confirmed_version }}</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button class="link" type="button" @click="editor.openEdit(row)">编辑</button>
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
          <td :colspan="columns.length + 2" class="empty-state">暂无内容解析器数据，可先登记解析模板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条内容解析器记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <FieldEditorModal
      module-title="解析模板"
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
      module-title="解析模板"
      code-field="模板编号"
      :fields="fieldDefs"
      :detail="detail"
      :revisions="revisions"
      @close="detailOpen = false"
    />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import EntryDetailDrawer from '@/components/EntryDetailDrawer.vue'
import FieldEditorModal from '@/components/FieldEditorModal.vue'
import { request } from '@/api/client'
import { useFieldEditor, type FieldDef } from '@/composables/useFieldEditor'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/parser'
const columns = ["模板编号", "目标字段名", "XPath表达式", "正则模式", "默认取值", "字段类型", "必填标识", "清洗函数"]
const actions = ["提交校验", "启用模板", "停用模板"]

const fieldDefs: FieldDef[] = [
  { key: '模板编号', label: '模板编号', required: true, placeholder: '如 PARS-0004', defaultValue: '' },
  { key: '目标字段名', label: '目标字段名', required: true, placeholder: '如 price', defaultValue: '' },
  { key: 'XPath表达式', label: 'XPath表达式', required: true, type: 'textarea', full: true, placeholder: '如 //span[@class="price"]/text()', defaultValue: '' },
  { key: '正则模式', label: '正则模式', placeholder: '留空表示不做正则提取', defaultValue: '' },
  { key: '默认取值', label: '默认取值', placeholder: '解析失败时的兜底值，允许 0 或空串', defaultValue: null },
  { key: '字段类型', label: '字段类型', type: 'select', options: ['string', 'number', 'boolean', 'date', 'object'], defaultValue: 'string' },
  { key: '必填标识', label: '必填标识', type: 'boolean', defaultValue: false },
  { key: '清洗函数', label: '清洗函数', placeholder: '如 trim、lower、upper', defaultValue: '' },
]

const stats = ref([
  { label: '解析模板总数', value: 0 },
  { label: '已启用模板', value: 0 },
  { label: '待校验模板', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

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

async function onSubmit() {
  const ok = await editor.save()
  if (ok) {
    await reload()
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
      throw new Error('解析模板详情读取失败')
    }
    detail.value = await detailResponse.json()
    revisions.value = (await revisionResponse.json()).items ?? []
    detailOpen.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析模板详情读取失败'
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
      const detailText = typeof body.detail === 'string' ? body.detail : body.detail?.message
      throw new Error(detailText ?? '内容解析器动作未生效，请刷新后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '内容解析器操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('解析模板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value[0].value = total.value
    stats.value[1].value = rows.value.filter((row) => row.status === '已启用').length
    stats.value[2].value = rows.value.filter((row) => row.status === '待校验').length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '内容解析器列表读取失败'
  }
}

onMounted(reload)
</script>
