<template>
  <div v-if="state.open" class="modal-mask" @click.self="close">
    <div class="modal-card" role="dialog" aria-modal="true">
      <div class="modal-head">
        <h3>{{ moduleTitle }}{{ title }}</h3>
        <button class="modal-close" type="button" @click="close">×</button>
      </div>

      <div class="modal-body">
        <div v-if="state.mode === 'edit'" class="readonly-box">
          <span>记录 ID：<strong>{{ state.readonlyMeta.id }}</strong></span>
          <span>业务状态：<strong>{{ state.readonlyMeta.status }}</strong></span>
          <span>
            当前版本：<strong>v{{ state.readonlyMeta.version }}</strong>
            <span v-if="state.readonlyMeta.confirmedVersion">
              （已确认 v{{ state.readonlyMeta.confirmedVersion }}）
            </span>
          </span>
          <span v-if="state.readonlyMeta.legacy" class="full">
            <span class="tag legacy">旧记录</span>
            该记录产生于版本治理上线前，保存后将纳入版本管理并升级为
            v{{ (state.readonlyMeta.version ?? 1) + 1 }}
          </span>
        </div>
        <p class="hint-text">
          带 <span class="req">*</span> 为必填字段；id、业务状态、版本链由服务端维护，页面提交不会覆盖。
        </p>

        <form class="field-grid" @submit.prevent>
          <label
            v-for="field in fields"
            :key="field.key"
            :class="{ full: field.full || field.type === 'textarea' }"
          >
            {{ field.label }}<span v-if="field.required" class="req">*</span>
            <select
              v-else-if="field.type === 'select'"
              :value="String(state.form[field.key] ?? '')"
              :disabled="field.readonly"
              @change="state.form[field.key] = ($event.target as HTMLSelectElement).value"
            >
              <option v-for="option in field.options ?? []" :key="option" :value="option">
                {{ field.optionLabels?.[option] ?? option }}
              </option>
            </select>
            <textarea
              v-else-if="field.type === 'textarea'"
              :value="asText(state.form[field.key])"
              :placeholder="field.placeholder ?? ''"
              :disabled="field.readonly"
              @input="state.form[field.key] = ($event.target as HTMLInputElement).value"
            />
            <input
              v-else-if="field.type === 'boolean'"
              type="checkbox"
              :checked="Boolean(state.form[field.key])"
              :disabled="field.readonly"
              @change="state.form[field.key] = ($event.target as HTMLInputElement).checked"
            />
            <input
              v-else-if="field.type === 'number'"
              type="number"
              :value="asText(state.form[field.key])"
              :placeholder="field.placeholder ?? ''"
              :disabled="field.readonly"
              @input="state.form[field.key] = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)"
            />
            <input
              v-else
              type="text"
              :value="asText(state.form[field.key])"
              :placeholder="field.placeholder ?? ''"
              :disabled="field.readonly"
              @input="state.form[field.key] = ($event.target as HTMLInputElement).value"
            />
          </label>
        </form>
      </div>

      <div class="modal-foot">
        <span v-if="message" :class="messageKind === 'error' ? 'error-text' : 'success-text'">
          {{ message }}
        </span>
        <button class="btn" type="button" :disabled="state.saving" @click="close">取消</button>
        <button class="btn primary" type="button" :disabled="state.saving" @click="submit">
          {{ state.saving ? '提交中…' : state.mode === 'create' ? '确认登记' : '保存修改' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { FieldDef } from '@/composables/useFieldEditor'
import type { EditorState } from '@/composables/useFieldEditor'

const props = defineProps<{
  moduleTitle: string
  title: string
  fields: FieldDef[]
  state: EditorState
  message: string
  messageKind: 'error' | 'success'
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'submit'): Promise<void> | void
}>()

function asText(value: string | number | boolean | null): string {
  return value === null || value === undefined ? '' : String(value)
}

function close() {
  emit('close')
}

function submit() {
  return emit('submit')
}
</script>
