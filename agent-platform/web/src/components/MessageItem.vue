<script setup>
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import MarkdownView from "./MarkdownView.vue";
import ToolTrace from "./ToolTrace.vue";
import DataCard from "./DataCard.vue";
import PlanCard from "./PlanCard.vue";
import ConfirmCard from "./ConfirmCard.vue";
import CapabilityCard from "./CapabilityCard.vue";
import AskCard from "./AskCard.vue";
import PhasePanel from "./PhasePanel.vue";
import { useUiStore } from "../stores/ui";

const props = defineProps({
  message: { type: Object, required: true },
  live: { type: Boolean, default: false },
});

const ui = useUiStore();

const isUser = computed(() => props.message.role === "user");
const type = computed(() => props.message.type || "text");
const text = computed(() => (typeof props.message.content === "string" ? props.message.content : ""));

async function copy() {
  try {
    await navigator.clipboard.writeText(text.value);
    ui.toast("已复制");
  } catch (e) {
    ui.toast("复制失败，请手动选择文本");
  }
}
</script>

<template>
  <div v-if="isUser" class="msg user">
    <div class="bubble">{{ message.content }}</div>
  </div>

  <div v-else-if="type === 'tools'" class="msg assistant">
    <ToolTrace :calls="message.content?.calls || []" />
  </div>

  <div v-else-if="type === 'table'" class="msg assistant">
    <DataCard :table="message.content || {}" :tool-call="message.toolCall" />
  </div>

  <div v-else-if="type === 'phase'" class="msg assistant">
    <PhasePanel />
  </div>

  <div v-else-if="type === 'plan'" class="msg assistant">
    <PlanCard :content="message.content || {}" :historical="!live" />
  </div>

  <div v-else-if="type === 'confirm'" class="msg assistant">
    <ConfirmCard :content="message.content || {}" :historical="!live" />
  </div>

  <div v-else-if="type === 'capability'" class="msg assistant">
    <CapabilityCard :content="message.content || {}" />
  </div>

  <div v-else-if="type === 'ask'" class="msg assistant">
    <AskCard :content="message.content || {}" />
  </div>

  <div v-else-if="type === 'notice'" class="turn-note">{{ message.content }}</div>

  <div v-else-if="type === 'error'" class="msg assistant error">
    <div class="error-body">
      <AppIcon name="alert" class="error-ico" />
      <span>{{ message.content }}</span>
    </div>
  </div>

  <div v-else class="msg assistant">
    <div class="body"><MarkdownView :text="text" /></div>
    <div v-if="text" class="msg-tools">
      <button class="copy-btn" type="button" @click="copy">
        <AppIcon name="copy" /><span>复制</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  flex-direction: column;
  gap: 7px;
  animation: msg-in .22s ease-out;
}
.msg.user { align-items: flex-end; }
.msg.user .bubble {
  max-width: 78%;
  padding: 9px 14px;
  border-radius: 16px;
  border-bottom-right-radius: 6px;
  background: var(--user-bubble);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: calc(14.5px * var(--scale));
  line-height: 1.65;
}
.msg.assistant { align-items: stretch; }
.msg.assistant .body {
  font-size: calc(15px * var(--scale));
  line-height: 1.78;
  color: var(--ink);
}

.msg-tools { display: flex; gap: 4px; opacity: 0; transition: opacity .12s; }
.msg:hover .msg-tools { opacity: 1; }
.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 8px;
  border-radius: 7px;
  font-size: 11px;
  color: var(--ink-3);
  transition: background .12s, color .12s;
}
.copy-btn:hover { background: var(--surface-2); color: var(--ink-2); }
.copy-btn .i { width: 13px; height: 13px; }

/* 系统说明（暂停 / 接管）：居中、无气泡，和「助手说的话」区分开；
   不受「显示工具调用过程」开关影响——藏了用户就不知道这轮为什么停 */
.turn-note {
  align-self: center;
  max-width: 100%;
  padding: 4px 12px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink-3);
  font-size: 11.5px;
  line-height: 1.6;
  text-align: center;
}

.error-body {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: var(--danger);
  background: var(--danger-soft);
  border: 1px solid color-mix(in srgb, var(--danger) 25%, transparent);
  border-radius: 12px;
  padding: 10px 14px;
  font-size: calc(13.5px * var(--scale));
  line-height: 1.7;
}
.error-ico { width: 15px; height: 15px; margin-top: 3px; flex: none; }
</style>
