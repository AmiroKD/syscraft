<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";
import MessageList from "./MessageList.vue";
import EmptyState from "./EmptyState.vue";
import Composer from "./Composer.vue";
import { useChatStore } from "../stores/chat";
import { useEventStore } from "../stores/event";
import { useSettingsStore } from "../stores/settings";
import { useUiStore } from "../stores/ui";

const chat = useChatStore();
const events = useEventStore();
const settings = useSettingsStore();
const ui = useUiStore();

const scrollEl = ref(null);
const scrolled = ref(false);
const stick = ref(true);

const showEmpty = computed(() => chat.empty && !chat.typing && events.mode !== "generation");
const statusText = computed(() => chat.statusText);

function onScroll() {
  const el = scrollEl.value;
  if (!el) return;
  scrolled.value = el.scrollTop > 4;
  stick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
}

function scrollToBottom() {
  const el = scrollEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

// 内容长度变化就跟着滚：消息追加、阶段推进、日志输出都走这一条
const tail = computed(() => {
  const last = chat.messages[chat.messages.length - 1];
  const contentLength = typeof last?.content === "string"
    ? last.content.length
    : (last?.content?.calls?.length || 0) * 10;
  return [
    chat.messages.length,
    contentLength,
    chat.typing,
    events.steps.length,
    events.files.length,
    events.logs.length,
  ].join("|");
});

watch(tail, () => nextTick(() => { if (stick.value) scrollToBottom(); }));
watch(() => chat.sessionId, () => nextTick(() => { stick.value = true; scrollToBottom(); }));

async function send(text) {
  stick.value = true;
  await chat.send(text);
}

onMounted(() => nextTick(scrollToBottom));
</script>

<template>
  <main class="main" :class="{ empty: showEmpty, scrolled }">
    <header class="topbar">
      <button class="icon-btn only-mobile" type="button" aria-label="打开侧栏" @click="ui.openSidebar()">
        <AppIcon name="menu" />
      </button>

      <div class="topbar-title">
        <span>{{ chat.title || "新对话" }}</span>
        <span v-if="chat.demo" class="demo-badge">演示数据</span>
      </div>

      <div class="topbar-actions">
        <button class="model-pill" type="button" :title="'当前模型：' + settings.modelLabel + '（点击配置）'" @click="ui.openDialog('settings')">
          {{ settings.modelLabel }}
        </button>
        <span v-if="statusText" class="status-pill">{{ statusText }}</span>
        <button class="icon-btn" type="button" title="设置" aria-label="设置" @click="ui.openDialog('settings')">
          <AppIcon name="settings" />
        </button>
      </div>
    </header>

    <div ref="scrollEl" class="scroll" @scroll.passive="onScroll">
      <MessageList v-if="!showEmpty" />
      <EmptyState v-else @pick="send" />
    </div>

    <Composer @send="send" />
  </main>
</template>

<style scoped>
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background: var(--bg);
}

.topbar {
  flex: none;
  height: 54px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 18px;
  border-bottom: 1px solid transparent;
  transition: border-color .2s, background .2s;
}
.main.scrolled .topbar {
  border-bottom-color: var(--line);
  background: color-mix(in srgb, var(--bg) 86%, transparent);
  backdrop-filter: blur(10px);
}
.topbar-title { min-width: 0; display: flex; align-items: center; gap: 8px; }
.topbar-title > span:first-child {
  font-size: 13.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.demo-badge {
  flex: none;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--warn-soft);
  color: var(--warn);
  font-size: 10.5px;
  font-weight: 600;
}
.topbar-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }

.model-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 190px;
  height: 26px;
  margin-right: 2px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface);
  color: var(--ink-2);
  font-size: 11px;
  font-weight: 550;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: border-color .12s, background .12s, color .12s;
}
.model-pill::before {
  content: "";
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--ok);
}
.model-pill:hover { border-color: var(--line-strong); background: var(--surface-2); color: var(--ink); }

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
}
.status-pill::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse 1.2s infinite;
}

.scroll { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; scroll-behavior: smooth; }
.main.empty .scroll { display: flex; align-items: flex-end; justify-content: center; }
.main.empty :deep(.composer-area) { padding-bottom: 30vh; }

.only-mobile { display: none; }
@media (max-width: 860px) {
  .only-mobile { display: grid; }
}
</style>
