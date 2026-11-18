<script setup>
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import MarkdownView from "./MarkdownView.vue";
import { useChatStore } from "../stores/chat";

const props = defineProps({
  content: { type: Object, default: () => ({}) },
  historical: { type: Boolean, default: false },
});

const chat = useChatStore();

const summary = computed(() => props.content?.summary || "");
const assumptions = computed(() => {
  const value = props.content?.assumptions;
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => String(item));
  return [String(value)];
});

function confirm() {
  chat.send("确认生成");
}

function cancel() {
  chat.send("取消");
}
</script>

<template>
  <div class="plan-card">
    <div class="card-title">
      <AppIcon name="spark" />
      <span>生成计划</span>
      <span v-if="content?.kind === 'amend'" class="tag">改结构</span>
    </div>

    <MarkdownView :text="summary" />

    <div v-if="assumptions.length" class="assumptions">
      <div class="sub">这些是系统替你做的推断，确认前请核对</div>
      <ul>
        <li v-for="(item, index) in assumptions" :key="index">{{ item }}</li>
      </ul>
    </div>

    <div v-if="!historical" class="actions">
      <button class="btn-sm primary" type="button" @click="confirm">确认生成</button>
      <button class="btn-sm" type="button" @click="cancel">取消</button>
    </div>
    <p v-else class="stale">历史记录中的计划已不可执行，重新描述需求即可生成新计划。</p>

    <p v-if="!historical" class="warn-note">
      确认后将开始编译，期间系统不可用约 1–3 分钟。
    </p>
  </div>
</template>

<style scoped>
.plan-card {
  border-radius: 13px;
  padding: 13px 15px;
  font-size: calc(13.5px * var(--scale));
  line-height: 1.7;
  border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
  background: var(--warn-soft);
  max-width: 100%;
}
.card-title { margin-bottom: 6px; }
.card-title .i { color: var(--warn); }
.tag {
  margin-left: 2px;
  padding: 0 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--warn) 18%, transparent);
  color: var(--warn);
  font-size: 10.5px;
  font-weight: 600;
}

.assumptions { margin-top: 8px; }
.assumptions .sub { font-size: 11px; color: var(--ink-3); }
.assumptions ul { margin: 3px 0 0; padding-left: 20px; font-size: 12.5px; color: var(--ink-2); }

.actions { display: flex; gap: 8px; margin-top: 11px; flex-wrap: wrap; }
.warn-note { margin: 9px 0 0; font-size: 11px; color: var(--ink-3); }
.stale { margin: 9px 0 0; font-size: 11px; color: var(--ink-3); }
</style>
