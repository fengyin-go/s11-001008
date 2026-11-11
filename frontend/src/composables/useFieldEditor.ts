/** 扩展字段编辑的通用逻辑：登记/编辑弹窗、乐观版本号、409 冲突与旧记录兼容。 */
import { computed, reactive, ref } from 'vue'

import { request } from '@/api/client'

export interface FieldDef {
  key: string
  label: string
  required?: boolean
  type?: 'text' | 'number' | 'boolean' | 'textarea' | 'select'
  options?: string[]
  optionLabels?: Record<string, string>
  placeholder?: string
  full?: boolean
  readonly?: boolean
  defaultValue: string | number | boolean | null
}

export interface ConflictSource {
  模块: string
  [key: string]: unknown
}

export interface ConflictDetail {
  target_field: string
  reasons: string[]
  resolution: string
  parser_source: ConflictSource
  format_source: ConflictSource
}

export interface EditorState {
  open: boolean
  mode: 'create' | 'edit'
  form: Record<string, string | number | boolean | null>
  entryId: number | null
  expectedVersion: number | null
  readonlyMeta: {
    id: number | null
    status: string
    version: number | null
    confirmedVersion: number | null
    legacy: boolean
  }
  conflict: ConflictDetail | null
  saving: boolean
}

function emptyForm(fields: FieldDef[]): Record<string, string | number | boolean | null> {
  const form: Record<string, string | number | boolean | null> = {}
  for (const field of fields) {
    form[field.key] = field.defaultValue
  }
  return form
}

export function useFieldEditor(endpoint: string, fields: FieldDef[]) {
  const message = ref('')
  const messageKind = ref<'error' | 'success'>('error')

  const state = reactive<EditorState>({
    open: false,
    mode: 'create',
    form: emptyForm(fields),
    entryId: null,
    expectedVersion: null,
    readonlyMeta: { id: null, status: '', version: null, confirmedVersion: null, legacy: false },
    conflict: null,
    saving: false,
  })

  const requiredFields = computed(() => fields.filter((field) => field.required))
  const title = computed(() => (state.mode === 'create' ? '登记' : '编辑扩展字段'))

  function resetMessage() {
    message.value = ''
    state.conflict = null
  }

  function openCreate() {
    state.open = true
    state.mode = 'create'
    state.form = emptyForm(fields)
    state.entryId = null
    state.expectedVersion = null
    state.readonlyMeta = { id: null, status: '', version: null, confirmedVersion: null, legacy: false }
    resetMessage()
  }

  async function openEdit(row: Record<string, unknown>) {
    resetMessage()
    const response = await request(`${endpoint}/${row.id}`)
    if (!response.ok) {
      messageKind.value = 'error'
      message.value = '明细读取失败，请稍后重试'
      return
    }
    const detail = await response.json()
    state.open = true
    state.mode = 'edit'
    state.entryId = Number(detail.id)
    state.expectedVersion = Number(detail.version ?? 1)
    state.readonlyMeta = {
      id: detail.id,
      status: detail.status,
      version: detail.version,
      confirmedVersion: detail.confirmed_version,
      legacy: Boolean(detail.legacy),
    }
    state.form = emptyForm(fields)
    for (const field of fields) {
      if (detail[field.key] !== undefined) {
        state.form[field.key] = detail[field.key] as string | number | boolean | null
      }
    }
  }

  function close() {
    state.open = false
    resetMessage()
  }

  async function save(): Promise<boolean> {
    resetMessage()
    const missing = requiredFields.value
      .filter((field) => String(state.form[field.key] ?? '').trim() === '')
      .map((field) => field.label)
    if (missing.length) {
      messageKind.value = 'error'
      message.value = `缺少必填字段：${missing.join('、')}`
      return false
    }

    const values: Record<string, string | number | boolean | null> = { ...state.form }
    if (state.mode === 'edit') {
      values.expected_version = state.expectedVersion as number
    }

    state.saving = true
    try {
      const url = state.mode === 'create' ? endpoint : `${endpoint}/${state.entryId}`
      const method = state.mode === 'create' ? 'POST' : 'PUT'
      const response = await request(url, {
        method,
        body: JSON.stringify({ values }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = body.detail ?? {}
        messageKind.value = 'error'
        if (typeof detail === 'string') {
          message.value = detail
        } else {
          message.value = detail.message ?? '提交失败，请稍后重试'
          if (detail.conflict) {
            state.conflict = detail.conflict as ConflictDetail
          }
          if (typeof detail.current_version === 'number') {
            message.value += `（服务端当前版本 v${detail.current_version}，请关闭后刷新列表再编辑）`
          }
        }
        return false
      }
      messageKind.value = 'success'
      message.value = body.message ?? '保存成功'
      state.open = false
      return true
    } catch (error) {
      messageKind.value = 'error'
      message.value = error instanceof Error ? error.message : '请求未送达'
      return false
    } finally {
      state.saving = false
    }
  }

  return { state, fields, title, message, messageKind, openCreate, openEdit, close, save, resetMessage }
}
