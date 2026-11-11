<template>
  <div v-if="conflict" class="conflict-panel">
    <h4>冲突阻断：{{ conflict.target_field }} 的解析与归一配置不一致</h4>
    <ul>
      <li v-for="(reason, index) in conflict.reasons" :key="index">{{ reason }}</li>
    </ul>
    <p class="hint-text" style="margin: 0 0 8px;">{{ conflict.resolution }}</p>
    <div class="conflict-cols">
      <div>
        <strong style="font-size: 12px;">解析模板（主配置来源）</strong>
        <pre>{{ JSON.stringify(conflict.parser_source, null, 2) }}</pre>
      </div>
      <div>
        <strong style="font-size: 12px;">归一规则（待确认来源）</strong>
        <pre>{{ JSON.stringify(conflict.format_source, null, 2) }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ConflictDetail } from '@/composables/useFieldEditor'

defineProps<{ conflict: ConflictDetail | null }>()
</script>
