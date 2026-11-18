<script setup>
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import { useChatStore } from "../stores/chat";

const props = defineProps({
  content: { type: Object, default: () => ({}) },
  historical: { type: Boolean, default: false },
});

const chat = useChatStore();
const summary = computed(() => props.content?.summary || "");

function answer(text) {
  chat.send(text);
}
</script>

<template>
  <div class="confirm-card">
    <div class="card-title">
      <AppIcon name="alert" />
      <span>待确认的删除</span>
    </div>
    <p class="text">{{ summary }}</p>

    <div v-if="!historical" class="actions">
      <button class="btn-sm primary" type="button" @click="answer('确认')">确认执行</button>
      <button class="btn-sm" type="button" @click="answer('取消')">取消</button>
    </div>
    <p v-if="historical" class="stale">历史记录中的确认仅在令牌有效期内可用（5 分钟）。</p>
  </div>
</template>

<style scoped>
.confirm-card {
  border-radius: 13px;
  padding: 13px 15px;
  font-size: calc(13.5px * var(--scale));
  line-height: 1.7;
  border: 1px solid color-mix(in srgb, var(--warn) 34%, var(--line));
  background: var(--surface);
  max-width: 100%;
}
.card-title { margin-bottom: 6px; }
.card-title .i { color: var(--warn); }
.text { margin: 0; white-space: pre-wrap; color: var(--ink); }
.actions { display: flex; gap: 8px; margin-top: 11px; flex-wrap: wrap; }
.stale { margin: 9px 0 0; font-size: 11px; color: var(--ink-3); }
</style>
