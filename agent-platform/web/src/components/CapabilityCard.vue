<script setup>
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";

const props = defineProps({
  content: { type: Object, default: () => ({}) },
});

const names = computed(() => (props.content?.available || [])
  .map((item) => item.display || item.entity)
  .filter(Boolean));
</script>

<template>
  <div class="capability-card">
    <div class="card-title">
      <AppIcon name="spark" />
      <span>能力边界</span>
    </div>
    <div class="body">
      <p>
        当前系统提供的实体：
        <strong>{{ names.length ? names.join("、") : "暂无" }}</strong>
      </p>
      <p v-if="content?.hint" class="hint">{{ content.hint }}</p>
    </div>
  </div>
</template>

<style scoped>
.capability-card {
  border-radius: 13px;
  padding: 13px 15px;
  font-size: calc(13.5px * var(--scale));
  line-height: 1.7;
  border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent);
  background: var(--accent-soft);
  max-width: 100%;
}
.card-title { margin-bottom: 6px; }
.card-title .i { color: var(--accent); }
.body p { margin: 0; color: var(--ink-2); font-size: 12.5px; }
.body strong { color: var(--ink); }
.hint { margin-top: 4px !important; }
</style>
