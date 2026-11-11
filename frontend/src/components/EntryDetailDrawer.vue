<template>
  <div v-if="open" class="modal-mask" @click.self="emit('close')">
    <div class="modal-card wide" role="dialog" aria-modal="true">
      <div class="modal-head">
        <h3>{{ moduleTitle }}详情 · {{ detail[codeField] ?? '—' }}</h3>
        <button class="modal-close" type="button" @click="emit('close')">×</button>
      </div>

      <div class="modal-body">
        <div class="readonly-box">
          <span>记录 ID：<strong>{{ detail.id }}</strong></span>
          <span>业务状态：<strong>{{ detail.status }}</strong></span>
          <span>
            当前版本：<strong>v{{ detail.version }}</strong>
            <span v-if="detail.confirmed_version">（已确认 v{{ detail.confirmed_version }}）</span>
          </span>
          <span v-if="detail.legacy" class="full">
            <span class="tag legacy">旧记录</span> 缺失的扩展字段已按系统默认值补齐展示，仓库原记录未被改写
          </span>
        </div>

        <dl class="detail-kv">
          <template v-for="field in fields" :key="field.key">
            <dt :class="{ full: field.full }">{{ field.label }}</dt>
            <dd :class="{ full: field.full }">{{ formatValue(detail[field.key]) }}</dd>
          </template>
        </dl>

        <h4 style="margin: 16px 0 8px; font-size: 14px;">版本历史（可追溯）</h4>
        <div v-if="revisions.length" class="revision-list">
          <div v-for="rev in revisions" :key="rev.version" class="revision-item">
            <div class="rev-head">
              <span>
                <strong>v{{ rev.version }}</strong>
                <span class="tag" :class="rev.confirmed ? 'ok' : 'draft'">
                  {{ rev.confirmed ? '已确认' : '草稿' }}
                </span>
              </span>
              <span>{{ rev.note }}</span>
            </div>
            <pre style="margin: 0; font-size: 11px; overflow-x: auto; white-space: pre-wrap;">{{ JSON.stringify(rev.snapshot, null, 2) }}</pre>
          </div>
        </div>
        <p v-else class="hint-text">暂无版本历史</p>
      </div>

      <div class="modal-foot">
        <button class="btn" type="button" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { FieldDef } from '@/composables/useFieldEditor'

defineProps<{
  open: boolean
  moduleTitle: string
  codeField: string
  fields: FieldDef[]
  detail: Record<string, unknown>
  revisions: Array<{ version: number; confirmed: boolean; note: string; snapshot: Record<string, unknown> }>
}>()

const emit = defineEmits<{ (event: 'close'): void }>()

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return value === '' ? '（空串）' : '—'
  }
  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }
  return String(value)
}
</script>
